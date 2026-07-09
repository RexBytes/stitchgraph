"""Mmapped CSR adjacency sidecar — memoized derivation, never the source of truth.

Every reachability/degree sweep used to rebuild its adjacency from SQLite into a
`dict[str, list[str]]` of Python strings on each call — 130 s and ~2 GB per op on a
16M-edge graph (Home Assistant, field analysis 2026-07-04), thrown away afterwards.
This module derives that adjacency ONCE into a compact binary sidecar next to the
index (`<db>.adjcache/`) and memory-maps it thereafter: 137 MB on disk for the same
16M-edge graph, 0.03 s to open, full liveness BFS in <1 s at ~350 MB RSS.

Layout v2 (one little-endian .npy per array, `np.load(mmap_mode="r")`-able):
  manifest.json  {version, generation, node_count, edge_count, relations}
  nodes.txt      node ids, newline-separated; line number == int id (sorted ids)
  fwd_indptr.npy int64[N+1]  CSR over the FLAT resolved edges, rows = src
  fwd_indices.npy int32[F]
  fwd_rel.npy    uint8[F]    relation code = index into manifest["relations"]
  fwd_conf.npy   uint8[ceil(F/8)]  packed per-edge `provenance == extracted` bit
  rev_*          the same four, rows = dst (for fan-in / blast radius)
  -- compressed groups (research/20), stored SHARED instead of expanded: a group
  -- row is (src, relation, candidate set); the interned set's members live once.
  grp_indptr.npy int64[N+1]  group rows per src   grp_set/grp_rel/grp_conf[G]
  set_indptr.npy int64[S+1]  members per set      set_members.npy int32[M]
  setuse_*       the group rows re-sorted by set (indptr[S+1], src/rel/conf[G])
  mem_*          set ids containing each node     (indptr[N+1], mem_set int32[M])

The v1 sidecar expanded every group into per-member rows: at Home Assistant
scale that is 16.1M CSR rows (~290 MB of edge arrays, ~74 s of per-row build
loop) for a graph whose shared form is 175k flat rows + 161k group rows + 201k
member entries (~8 MB). The sweeps do the expansion where it is free — the BFS
family deduplicates candidate sets per frontier before touching members, so a
64-arm homonym set referenced by 900 frontier sites is gathered once, not 900×.

The provenance bitmask is the 0/1-matrix idea in its right place: 2 MB for 16M
edges, probed in the BFS inner loop with a shift-and-mask — it is what makes the
EXTRACTED-only sweep (scan's certainty pass) ~10⁴× faster than constructing an
`Edge` object per row just to read one field.

Contracts:
- **SQLite is authoritative; the sidecar is disposable.** Deleting the directory
  is always safe — the next sweep rebuilds it (or falls back to pure Python).
- **Staleness:** the sidecar records the store's `generation` meta (bumped by
  `reindex`, `replace_file`, and the invalid-root wipe) plus the node count; any
  mismatch and `load_cache` refuses it. Callers mutating the graph through other
  means must call `Store.bump_generation()` themselves.
- **Build is lazy** — on first use, NOT inside `reindex`: the streaming reindex
  carries a hard constant-memory gate (130 MB RLIMIT_AS) that a numpy import plus
  build arrays inside the indexing process would violate. The first sweep after a
  (re)index pays the one-time build (~74 s on 16M edges — already cheaper than the
  ~130 s dict build it replaces); every later sweep opens in milliseconds.
- **Stdlib-only core:** numpy is a guarded import (the `modes.py` pattern). No
  numpy, read-only filesystem, `:memory:` store, or `[index] adjacency_cache =
  false` → `load_cache` returns None and callers use their existing paths.
- **Parity:** edges whose src/dst id has no node row are skipped (panel R29A, as
  `_adjacency`/GraphBLAS); rows with an unknown relation string are skipped (the
  corrupt-index guard, as `iter_resolved`).
"""

from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING

try:
    import numpy as _np
except Exception:  # noqa: BLE001 — numpy absent → no sidecar, callers fall back
    _np = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .model import Relation
    from .store import Store

_VERSION = 2  # v2 = group-sharing layout; v1 sidecars are refused and rebuilt
_SUFFIX = ".adjcache"
_GENERATION_KEY = "generation"

# Per-store-path memo: (generation, node_count, manifest mtime) -> AdjacencyCache,
# so repeated sweeps in one process skip re-reading nodes.txt / rebuilding the id
# map (~30 ms on 59k nodes). A failed/refused build is memoised per generation so a
# read-only filesystem costs one attempt per index state, not one per sweep.
_loaded: dict[str, tuple[tuple, AdjacencyCache]] = {}
_build_failed: dict[str, str] = {}  # store path -> generation that failed


def current_generation(store: Store) -> str:
    return store.get_meta(_GENERATION_KEY) or "0"


def _gather_rows(indptr, frontier):
    """Enumerate every entry index of the CSR rows in `frontier`, back to back
    (the SpMV gather all sweeps share): row r contributes [indptr[r], indptr[r+1]).
    Returns (entry_indices int64[T], per-row counts int64[len(frontier)]); the
    entry array is None when the frontier's rows are all empty."""
    starts = indptr[frontier]
    counts = (indptr[frontier + 1] - starts).astype(_np.int64)
    total = int(counts.sum())
    if not total:
        return None, counts
    offs = _np.repeat(starts - _np.concatenate(([0], _np.cumsum(counts)[:-1])), counts)
    return _np.arange(total, dtype=_np.int64) + offs, counts


def _intarray(arr):
    """int64 numpy array -> array.array('q'): C-int storage (8 B/entry) with fast
    Python scalar indexing. `.tolist()` here boxed every edge id — ~28 B/entry,
    the 4.1 GB find_chokepoints peak at 26.8M edges (scale validation follow-up);
    this keeps the traversals' speed at ~1/3.5 the memory."""
    import array
    out = array.array("q")
    out.frombytes(arr.astype(_np.int64).tobytes())
    return out


class AdjacencyCache:
    """Read handle over one sidecar directory. Query methods mirror the pure-Python
    `reach` functions exactly (same inputs, same result sets) — pinned by the
    equivalence tests in tests/test_adjcache.py."""

    def __init__(self, path: str) -> None:
        with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)
        if self.manifest.get("version") != _VERSION:
            raise ValueError(f"sidecar version {self.manifest.get('version')}")
        ld = lambda n: _np.load(os.path.join(path, n), mmap_mode="r")  # noqa: E731
        self.fwd_indptr, self.fwd_indices = ld("fwd_indptr.npy"), ld("fwd_indices.npy")
        self.fwd_rel, self.fwd_conf = ld("fwd_rel.npy"), ld("fwd_conf.npy")
        self.rev_indptr, self.rev_indices = ld("rev_indptr.npy"), ld("rev_indices.npy")
        self.rev_rel, self.rev_conf = ld("rev_rel.npy"), ld("rev_conf.npy")
        # group-sharing arrays (v2): per-src group rows, the interned sets they
        # reference, the same rows re-sorted by set (the reverse hop), and the
        # per-node containing-sets transpose. All possibly empty.
        self.grp_indptr, self.grp_set = ld("grp_indptr.npy"), ld("grp_set.npy")
        self.grp_rel, self.grp_conf = ld("grp_rel.npy"), ld("grp_conf.npy")
        self.set_indptr, self.set_members = ld("set_indptr.npy"), ld("set_members.npy")
        self.setuse_indptr, self.setuse_src = (ld("setuse_indptr.npy"),
                                               ld("setuse_src.npy"))
        self.setuse_rel, self.setuse_conf = ld("setuse_rel.npy"), ld("setuse_conf.npy")
        self.mem_indptr, self.mem_set = ld("mem_indptr.npy"), ld("mem_set.npy")
        self.set_sizes = _np.diff(self.set_indptr)
        self.n_sets = len(self.set_indptr) - 1
        self._mem_keys_cache = None  # lazy sorted (node,set) membership keys
        with open(os.path.join(path, "nodes.txt"), encoding="utf-8") as f:
            self.ids = f.read().split("\n") if os.path.getsize(
                os.path.join(path, "nodes.txt")) else []
        self.idx = {nid: i for i, nid in enumerate(self.ids)}
        self.n = len(self.ids)
        # Row overlay (v3.40.0): after a replace_file, the loader PATCHES a stale
        # mmap instead of rebuilding — per-node replacement rows for every node the
        # edit touched (node_idx -> (dst_idx int64[], rel uint8[], conf bool[])),
        # consulted by the BFS family and _degrees; the base arrays are never
        # written. New nodes extend ids/idx past base_n and live only here.
        self.base_n = self.n
        self.overlay_fwd: dict[int, tuple] = {}
        self.overlay_rev: dict[int, tuple] = {}
        # sorted int64 key arrays for vectorized isin (None until a delta lands)
        self._ov_fwd_keys: object = None
        self._ov_rev_keys: object = None

    @property
    def has_overlay(self) -> bool:
        return bool(self.overlay_fwd) or bool(self.overlay_rev)

    def apply_delta(self, store: Store, srcs: list[str], dsts: list[str],
                    new_ids: list[str]) -> bool:
        """Patch this cache to the store's CURRENT state given the union of touched
        rows from the adj_delta chain. Returns False (leave the cache unused) on
        anything unrepresentable — the caller then falls back to a full rebuild."""
        rel_code = {name: i for i, name in enumerate(self.manifest["relations"])}
        from .envelope import Provenance
        extracted = Provenance.EXTRACTED.value
        for nid in sorted(set(new_ids) - set(self.idx)):
            self.idx[nid] = len(self.ids)
            self.ids.append(nid)
        self.n = len(self.ids)

        def _rows(id_list: list[str], by_src: bool) -> dict[int, tuple] | None:
            out: dict[int, tuple] = {}
            col = "src" if by_src else "dst_id"
            other = "dst_id" if by_src else "src"
            for i in range(0, len(id_list), 500):
                chunk = id_list[i:i + 500]
                q = (f"SELECT {col}, {other}, relation, provenance FROM edges_all "
                     f"WHERE {col} IN "
                     f"({','.join('?' * len(chunk))})")
                grouped: dict[str, list] = {c: [] for c in chunk}
                for key, nbr, rel, prov in store.conn.execute(q, chunk):
                    grouped.setdefault(key, []).append((nbr, rel, prov))
                for key, rows in grouped.items():
                    ki = self.idx.get(key)
                    if ki is None:
                        continue  # touched then deleted and never re-added: no row
                    nbrs, rels, confs = [], [], []
                    for nbr, rel, prov in rows:
                        ni = self.idx.get(nbr)
                        if ni is None:
                            continue  # edge to a node outside the graph (defensive)
                        code = rel_code.get(rel)
                        if code is None:
                            return None  # relation unknown to this manifest: rebuild
                        nbrs.append(ni)
                        rels.append(code)
                        confs.append(prov == extracted)
                    out[ki] = (_np.array(nbrs, _np.int64),
                               _np.array(rels, _np.uint8),
                               _np.array(confs, bool))
            return out

        # Every touched src needs a fwd row (empty if all its edges vanished); every
        # touched dst needs a rev row; NEW nodes need both (they may be only-ever
        # targets or only-ever sources).
        fwd_ids = sorted(set(srcs) | set(new_ids))
        rev_ids = sorted(set(dsts) | set(new_ids))
        fwd = _rows(fwd_ids, by_src=True)
        rev = _rows(rev_ids, by_src=False)
        if fwd is None or rev is None:
            return False
        empty = (_np.empty(0, _np.int64), _np.empty(0, _np.uint8), _np.empty(0, bool))
        for nid in fwd_ids:
            ki = self.idx.get(nid)
            if ki is not None:
                self.overlay_fwd[ki] = fwd.get(ki, empty)
        for nid in rev_ids:
            ki = self.idx.get(nid)
            if ki is not None:
                self.overlay_rev[ki] = rev.get(ki, empty)
        self._ov_fwd_keys = _np.array(sorted(self.overlay_fwd), _np.int64)
        self._ov_rev_keys = _np.array(sorted(self.overlay_rev), _np.int64)
        return True

    # -- helpers -------------------------------------------------------------
    def _rel_mask(self, relations: Iterable[Relation]):
        """Boolean lookup table over relation codes for `allowed[rel[e]]` gathers."""
        names = self.manifest["relations"]
        allowed = _np.zeros(len(names), bool)
        for r in relations:
            v = getattr(r, "value", r)
            if v in names:
                allowed[names.index(v)] = True
        return allowed

    def _seed_array(self, seeds: Iterable[str]):
        found = sorted({i for s in seeds if (i := self.idx.get(s)) is not None})
        return _np.array(found, _np.int64)

    def _split_frontier(self, frontier, ov_keys):
        """(base_frontier, overlay_frontier): overlay rows — and NEW nodes past
        base_n, which have no base indptr slot at all — must never reach the
        vectorized base gather."""
        if ov_keys is not None and ov_keys.size:
            m = _np.isin(frontier, ov_keys)
            if self.n > self.base_n:
                m |= frontier >= self.base_n
            if m.any():
                return frontier[~m], frontier[m]
        elif self.n > self.base_n:
            m = frontier >= self.base_n
            if m.any():
                return frontier[~m], frontier[m]
        return frontier, None

    def _ov_neighbours(self, node, overlay, allowed, confident_only):
        row = overlay.get(int(node))
        if row is None:
            return None  # a new node with no overlay row: no edges
        nbrs, rels, confs = row
        if not nbrs.size:
            return None
        m = allowed[rels]
        if confident_only:
            m = m & confs
        return nbrs[m]

    # -- group gathers (v2): the two-level hop that keeps sets shared ---------
    def _grp_rows(self, frontier, allowed, confident_only):
        """Hop one of every forward group gather: the frontier's group rows,
        masked exactly like flat edges. Returns (kept row indices, the frontier
        node each came from), or (None, None)."""
        g_idx, counts = _gather_rows(self.grp_indptr, frontier)
        if g_idx is None:
            return None, None
        mask = allowed[self.grp_rel[g_idx]]
        if confident_only:
            mask &= ((self.grp_conf[g_idx >> 3] >> (7 - (g_idx & 7))) & 1).astype(bool)
        if not mask.any():
            return None, None
        return g_idx[mask], _np.repeat(frontier, counts)[mask]

    def _set_slices(self, set_ids):
        """Concatenated members of `set_ids` (dups allowed) + per-set counts."""
        m_idx, counts = _gather_rows(self.set_indptr, set_ids)
        if m_idx is None:
            return _np.empty(0, _np.int64), counts
        return _np.asarray(self.set_members)[m_idx].astype(_np.int64), counts

    def _fwd_grp_neighbours(self, frontier, allowed, confident_only):
        """Frontier -> masked group rows -> UNIQUE candidate sets -> members.
        Deduplicating sets before expansion is the point of the shared layout: a
        64-arm homonym set referenced by 900 frontier sites expands once."""
        g_idx, _rows = self._grp_rows(frontier, allowed, confident_only)
        if g_idx is None:
            return None
        members, _counts = self._set_slices(
            _np.unique(_np.asarray(self.grp_set)[g_idx]))
        return members if members.size else None

    def _rev_grp_neighbours(self, frontier, allowed, confident_only):
        """Dst frontier -> sets CONTAINING each node (membership is
        relation-free) -> masked group rows using those sets -> their srcs."""
        m_idx, _counts = _gather_rows(self.mem_indptr, frontier)
        if m_idx is None:
            return None
        us = _np.unique(_np.asarray(self.mem_set)[m_idx])
        su_idx, _su_counts = _gather_rows(self.setuse_indptr, us)
        if su_idx is None:
            return None
        mask = allowed[self.setuse_rel[su_idx]]
        if confident_only:
            mask &= ((self.setuse_conf[su_idx >> 3] >> (7 - (su_idx & 7)))
                     & 1).astype(bool)
        out = _np.asarray(self.setuse_src)[su_idx[mask]]
        return out.astype(_np.int64) if out.size else None

    def _member_keys(self):
        """Sorted packed (node * n_sets + set) membership keys, built lazily once
        per handle — the vectorised `src in its own candidate set?` probe."""
        if self._mem_keys_cache is None:
            mem_nodes = _np.repeat(_np.arange(self.base_n, dtype=_np.int64),
                                   _np.diff(self.mem_indptr))
            self._mem_keys_cache = _np.sort(
                mem_nodes * max(self.n_sets, 1) + _np.asarray(self.mem_set))
        return self._mem_keys_cache

    def _bfs(self, indptr, indices, rel, conf, seeds, allowed, confident_only,
             overlay=None, ov_keys=None, grp=None):
        seen = _np.zeros(self.n, bool)
        seed_arr = self._seed_array(seeds)
        seen[seed_arr] = True
        frontier = seed_arr
        while frontier.size:
            frontier, ov_front = self._split_frontier(frontier, ov_keys)
            parts = []
            # Gather every neighbour slice of the frontier in one shot (the SpMV
            # step): e_idx enumerates each row's [start, start+count) back to back.
            e_idx, _counts = _gather_rows(indptr, frontier)
            if e_idx is not None:
                mask = allowed[rel[e_idx]]
                if confident_only:
                    # packed-bit probe: bit i of the conf mask, no unpacking
                    mask &= ((conf[e_idx >> 3] >> (7 - (e_idx & 7))) & 1).astype(bool)
                parts.append(indices[e_idx[mask]])
            if grp is not None:
                nb = grp(frontier, allowed, confident_only)
                if nb is not None:
                    parts.append(nb)
            if ov_front is not None:
                for f in ov_front.tolist():  # overlay is small (<= patch threshold)
                    nb = self._ov_neighbours(f, overlay or {}, allowed, confident_only)
                    if nb is not None:
                        parts.append(nb)
            if not parts:
                break
            neigh = parts[0] if len(parts) == 1 else _np.concatenate(parts)
            new = neigh[~seen[neigh]]
            seen[new] = True
            frontier = _np.unique(new)
        return seen

    @staticmethod
    def _row_sums(indptr, weights):
        """Per-row sums of an entry-aligned weight vector (the reduceat idiom
        with the empty-row and out-of-range guards every caller needs)."""
        sums = _np.zeros(len(indptr) - 1, _np.int64)
        if len(weights):
            starts = _np.minimum(indptr[:-1], len(weights) - 1)  # reduceat in-range
            sums = _np.add.reduceat(weights.astype(_np.int64), starts)
            sums[indptr[:-1] == indptr[1:]] = 0  # empty rows: reduceat repeats next
        return sums

    def _entry_mask(self, rel, conf, allowed, confident_only):
        mask = allowed[_np.asarray(rel)]
        if confident_only and len(mask):
            e = _np.arange(len(mask), dtype=_np.int64)
            mask &= ((conf[e >> 3] >> (7 - (e & 7))) & 1).astype(bool)
        return mask

    def _fwd_grp_degrees(self, allowed, confident_only):
        """Per-src count contributed by group rows: each masked row weighs its
        whole candidate set."""
        mask = self._entry_mask(self.grp_rel, self.grp_conf, allowed,
                                confident_only)
        if not len(mask):
            return _np.zeros(self.base_n, _np.int64)
        w = _np.asarray(self.set_sizes)[_np.asarray(self.grp_set)] * mask
        return self._row_sums(self.grp_indptr, w)

    def _rev_grp_degrees(self, allowed, confident_only):
        """Per-dst count from groups: node v gains one edge per masked group row
        whose set contains v — computed set-first (masked row count per set),
        then summed over each node's containing sets."""
        mask = self._entry_mask(self.setuse_rel, self.setuse_conf, allowed,
                                confident_only)
        if not len(mask) or not len(self.mem_set):
            return _np.zeros(self.base_n, _np.int64)
        per_set = self._row_sums(self.setuse_indptr, mask)
        return self._row_sums(self.mem_indptr, per_set[_np.asarray(self.mem_set)])

    def _degrees(self, indptr, rel, conf, relations, confident_only=False,
                 overlay=None, grp_degrees=None):
        allowed = self._rel_mask(relations)
        counts = self._row_sums(
            indptr, self._entry_mask(rel, conf, allowed, confident_only))
        if grp_degrees is not None:
            counts = counts + grp_degrees(allowed, confident_only)
        out = {self.ids[i]: int(counts[i]) for i in _np.nonzero(counts)[0]}
        # Overlay rows REPLACE their base counts (v3.40.0 incremental refresh);
        # new nodes past base_n exist only here.
        for k, (_nbrs, rels, confs) in (overlay or {}).items():
            m = allowed[rels]
            if confident_only:
                m = m & confs
            c = int(m.sum())
            if c:
                out[self.ids[k]] = c
            else:
                out.pop(self.ids[k], None)
        return out

    # -- the reach.py mirrors ------------------------------------------------
    def reachable(self, seeds: Iterable[str], relations: Iterable[Relation],
                  confident_only: bool = False) -> set[str]:
        seen = self._bfs(self.fwd_indptr, self.fwd_indices, self.fwd_rel,
                         self.fwd_conf, seeds, self._rel_mask(relations),
                         confident_only, overlay=self.overlay_fwd,
                         ov_keys=self._ov_fwd_keys, grp=self._fwd_grp_neighbours)
        return {self.ids[i] for i in _np.nonzero(seen)[0]}

    def reachable_many(self, seed_groups: list[set[str]],
                       relations: Iterable[Relation],
                       confident_only: bool = False) -> list[set[str]]:
        """Up to 64 independent reachability queries in ONE fixed-point sweep —
        the bit-parallel BFS (v3.39.0). `audit_graph` runs one forward closure
        per test (2,056 BFS = 31.6 min on the HA field index); packing 64 test
        seeds into the bit-lanes of a uint64 label per node answers 64 of them
        per sweep: node n is reachable from group g iff bit g of labels[n] is
        set at the fixed point. Same edge gather and relation/confidence masks
        as `_bfs`; pinned per-lane identical to sequential `reachable` calls by
        the differential test. More than 64 groups chunk recursively."""
        if len(seed_groups) > 64:
            out: list[set[str]] = []
            for i in range(0, len(seed_groups), 64):
                out.extend(self.reachable_many(seed_groups[i:i + 64], relations,
                                               confident_only))
            return out
        labels = self._label_sweep(seed_groups, relations, confident_only)
        out = []
        for g in range(len(seed_groups)):
            bit = _np.uint64(1 << g)
            out.append({self.ids[i] for i in _np.nonzero((labels & bit) != 0)[0]})
        return out

    def _label_sweep(self, seed_groups: list, relations: Iterable[Relation],
                     confident_only: bool):
        """The bit-parallel fixed point shared by `reachable_many` (which wants the
        per-group id SETS) and `reach_hits` (which wants only the per-node lane
        POPCOUNT): uint64 labels, bit g set on node n iff n is forward-reachable
        from group g. At most 64 groups."""
        allowed = self._rel_mask(relations)
        indptr, indices = self.fwd_indptr, self.fwd_indices
        rel, conf = self.fwd_rel, self.fwd_conf
        labels = _np.zeros(self.n, _np.uint64)
        for g, seeds in enumerate(seed_groups):
            idx = self._seed_array(seeds)
            if idx.size:
                labels[idx] |= _np.uint64(1 << g)
        frontier = _np.nonzero(labels)[0]
        while frontier.size:
            frontier, ov_front = self._split_frontier(frontier, self._ov_fwd_keys)
            neigh_parts, contrib_parts = [], []
            e_idx, counts = _gather_rows(indptr, frontier)
            if e_idx is not None:
                mask = allowed[rel[e_idx]]
                if confident_only:
                    mask &= ((conf[e_idx >> 3] >> (7 - (e_idx & 7))) & 1).astype(bool)
                neigh_parts.append(indices[e_idx[mask]])
                contrib_parts.append(labels[_np.repeat(frontier, counts)[mask]])
            # group hop, two-level like the BFS but label-carrying: OR the
            # frontier labels into each touched SET, then broadcast the set's
            # accumulated label to its members — one expansion per unique set.
            g_idx, g_rows = self._grp_rows(frontier, allowed, confident_only)
            if g_idx is not None:
                sets = _np.asarray(self.grp_set)[g_idx]
                set_label = _np.zeros(self.n_sets, _np.uint64)
                _np.bitwise_or.at(set_label, sets, labels[g_rows])
                us = _np.unique(sets)
                members, m_counts = self._set_slices(us)
                if members.size:
                    neigh_parts.append(members)
                    contrib_parts.append(_np.repeat(set_label[us], m_counts))
            if ov_front is not None:
                for f in ov_front.tolist():
                    nb = self._ov_neighbours(f, self.overlay_fwd, allowed,
                                             confident_only)
                    if nb is not None:
                        neigh_parts.append(nb)
                        contrib_parts.append(_np.full(nb.size, labels[f], _np.uint64))
            if not neigh_parts:
                break
            neigh = (neigh_parts[0] if len(neigh_parts) == 1
                     else _np.concatenate(neigh_parts))
            contrib = (contrib_parts[0] if len(contrib_parts) == 1
                       else _np.concatenate(contrib_parts))
            before = labels.copy()  # n × 8 B per round — cheap next to the edge gather
            _np.bitwise_or.at(labels, neigh, contrib)
            # A node re-enters the frontier only when it gained NEW lane bits, so
            # each lane's propagation terminates exactly like its solo BFS.
            frontier = _np.nonzero(labels != before)[0]
        return labels

    def reach_hits(self, seed_groups: list, relations: Iterable[Relation],
                   confident_only: bool = False):
        """Per-node count of seed groups whose forward closure contains the node
        (int64[n]). The bit-parallel sweeps never materialise the id sets — 64
        lanes popcount per chunk — so 1,024 single-node groups cost 16 sweeps and
        one int vector, which is what makes the sampled transitive-fan-in
        estimator (reach.transitive_fan_in_estimate) tractable at field scale."""
        hits = _np.zeros(self.n, _np.int64)
        for i in range(0, len(seed_groups), 64):
            labels = self._label_sweep(seed_groups[i:i + 64], relations,
                                       confident_only)
            # popcount via byte view: n×8 uint8 → unpack → sum. Transient is
            # n×64 bits ≈ 6.4 MB per sweep at 100k nodes.
            hits += _np.unpackbits(labels.view(_np.uint8)).reshape(
                self.n, 64).sum(axis=1).astype(_np.int64)
        return hits

    def reverse_reachable(self, targets: Iterable[str],
                          relations: Iterable[Relation],
                          confident_only: bool = False) -> set[str]:
        targets = set(targets)
        seen = self._bfs(self.rev_indptr, self.rev_indices, self.rev_rel,
                         self.rev_conf, targets, self._rel_mask(relations),
                         confident_only,
                         overlay=self.overlay_rev, ov_keys=self._ov_rev_keys,
                         grp=self._rev_grp_neighbours)
        out = {self.ids[i] for i in _np.nonzero(seen)[0]}
        out.difference_update(targets)  # blast radius excludes the targets themselves
        return out

    def fan_in(self, relations: Iterable[Relation],
               confident_only: bool = False) -> dict[str, int]:
        return self._degrees(self.rev_indptr, self.rev_rel, self.rev_conf, relations,
                             confident_only, overlay=self.overlay_rev,
                             grp_degrees=self._rev_grp_degrees)

    def fan_out(self, relations: Iterable[Relation],
                confident_only: bool = False) -> dict[str, int]:
        return self._degrees(self.fwd_indptr, self.fwd_rel, self.fwd_conf, relations,
                             confident_only, overlay=self.overlay_fwd,
                             grp_degrees=self._fwd_grp_degrees)

    def _filtered_csr(self, relations: Iterable[Relation], *, drop_self: bool = False):
        """Materialise a relation-filtered forward CSR (indptr, indices, rows) so the
        Python traversals below touch only kept edges with no per-edge relation test.
        Group rows are EXPANDED here — the per-edge walkers pay logical size
        transiently while the disk stays shared. Within a row the order is flat
        edges then group expansions in stored order: the edges_all UNION ALL
        order the scan differential pins."""
        allowed = self._rel_mask(relations)
        mask = allowed[_np.asarray(self.fwd_rel)]
        rows = _np.repeat(_np.arange(self.base_n, dtype=_np.int32),
                          _np.diff(self.fwd_indptr))
        if drop_self:
            mask &= rows != _np.asarray(self.fwd_indices)
        rows = rows[mask]
        indices = _np.asarray(self.fwd_indices)[mask]
        gmask = self._entry_mask(self.grp_rel, self.grp_conf, allowed, False)
        kept = _np.nonzero(gmask)[0]
        if kept.size:
            g_rows = _np.repeat(_np.arange(self.base_n, dtype=_np.int32),
                                _np.diff(self.grp_indptr))[kept]
            members, counts = self._set_slices(_np.asarray(self.grp_set)[kept])
            rows_g = _np.repeat(g_rows, counts)
            indices_g = members.astype(_np.int32)
            if drop_self:
                keep2 = rows_g != indices_g
                rows_g, indices_g = rows_g[keep2], indices_g[keep2]
            order = _np.argsort(_np.concatenate((rows, rows_g)), kind="stable")
            rows = _np.concatenate((rows, rows_g))[order]
            indices = _np.concatenate((indices, indices_g))[order]
        indptr = _np.zeros(self.n + 1, _np.int64)
        _np.cumsum(_np.bincount(rows, minlength=self.n), out=indptr[1:])
        return indptr, indices, rows

    def self_loops(self, relations: Iterable[Relation]) -> set[str]:
        """Node ids with a self-edge under `relations` (recursion markers for SCC)."""
        allowed = self._rel_mask(relations)
        rows = _np.repeat(_np.arange(self.base_n, dtype=_np.int32),
                          _np.diff(self.fwd_indptr))
        hit = rows[(rows == _np.asarray(self.fwd_indices))
                   & allowed[_np.asarray(self.fwd_rel)]]
        out = {self.ids[i] for i in _np.unique(hit)}
        # A group self-loop is a src inside its own candidate set — probed via
        # the packed (node, set) membership keys, no expansion.
        gmask = allowed[_np.asarray(self.grp_rel)]
        if gmask.any():
            keys = self._member_keys()
            if len(keys):
                g_rows = _np.repeat(_np.arange(self.base_n, dtype=_np.int64),
                                    _np.diff(self.grp_indptr))
                q = (g_rows * max(self.n_sets, 1)
                     + _np.asarray(self.grp_set, dtype=_np.int64))
                pos = _np.minimum(_np.searchsorted(keys, q), len(keys) - 1)
                found = gmask & (keys[pos] == q)
                out |= {self.ids[i] for i in _np.unique(g_rows[found])}
        return out

    def scc(self, seeds: Iterable[str],
            relations: Iterable[Relation]) -> list[list[str]]:
        """Tarjan SCC over the relation-filtered graph — ITERATIVE but emitting
        components in exactly the recursive reference's order (`_scc.tarjan_scc`):
        seeds visited in caller order, neighbours in stored-edge order, components
        in reverse-topological completion order, members in stack-pop order. The
        scan differential depends on this parity."""
        indptr_a, indices_a, _ = self._filtered_csr(relations)
        indptr = _intarray(indptr_a)
        indices = _intarray(indices_a)
        n = self.n
        index = [-1] * n
        low = [0] * n
        on_stack = bytearray(n)
        stack: list[int] = []
        counter = 0
        out: list[list[str]] = []
        for s in seeds:
            si = self.idx.get(s)
            if si is None or index[si] >= 0:
                continue
            index[si] = low[si] = counter
            counter += 1
            stack.append(si)
            on_stack[si] = 1
            work = [(si, indptr[si])]
            while work:
                v, ptr = work.pop()
                end = indptr[v + 1]
                advanced = False
                while ptr < end:
                    w = indices[ptr]
                    ptr += 1
                    if index[w] < 0:
                        work.append((v, ptr))
                        index[w] = low[w] = counter
                        counter += 1
                        stack.append(w)
                        on_stack[w] = 1
                        work.append((w, indptr[w]))
                        advanced = True
                        break
                    if on_stack[w] and index[w] < low[v]:
                        low[v] = index[w]
                if advanced:
                    continue
                if low[v] == index[v]:
                    comp: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = 0
                        comp.append(self.ids[w])
                        if w == v:
                            break
                    out.append(comp)
                if work and low[v] < low[work[-1][0]]:
                    low[work[-1][0]] = low[v]
        return out

    def articulation(self, relations: Iterable[Relation]) -> dict[str, int]:
        """Cut vertices of the undirected projection, blast-radius valued — the exact
        algorithm of `reach.articulation_points` over ints. Neighbour order parity:
        the reference iterates `sorted(undirected[u])` (string sort); sidecar ids are
        stored sorted, so ascending int == that order, and the symmetrised unique()
        below yields ascending neighbours per row for free."""
        _indptr_f, indices_f, rows = self._filtered_csr(relations, drop_self=True)
        n = self.n
        # symmetrise + dedup via packed 64-bit keys; unique() sorts, giving both the
        # ascending root order and ascending per-row neighbour order the reference
        # uses. Transients here dominated the op's memory at 26.8M edges (scale
        # validation follow-up) — free each as soon as its successor exists.
        fwd_keys = rows.astype(_np.int64) * n + indices_f
        rev_keys = indices_f.astype(_np.int64) * n + rows
        del indices_f, rows
        both = _np.concatenate((fwd_keys, rev_keys))
        del fwd_keys, rev_keys
        keys = _np.unique(both)
        del both
        uu = (keys // n).astype(_np.int64)
        vv = keys % n  # already int64; no copy
        del keys
        indptr_a = _np.zeros(n + 1, _np.int64)
        _np.cumsum(_np.bincount(uu, minlength=n), out=indptr_a[1:])
        indptr = _intarray(indptr_a)
        neigh = _intarray(vv)
        del vv

        disc = [-1] * n
        low = [0] * n
        timer = 0
        guarded: dict[int, int] = {}
        roots = _np.unique(uu).tolist()
        for root in roots:
            if disc[root] >= 0:
                continue
            disc[root] = low[root] = timer
            timer += 1
            size = {root: 1}
            sep: dict[int, list[int]] = {}
            stack: list[tuple[int, int, int]] = [(root, -1, indptr[root])]
            while stack:
                u, parent, ptr = stack.pop()
                end = indptr[u + 1]
                advanced = False
                while ptr < end:
                    v = neigh[ptr]
                    ptr += 1
                    if disc[v] < 0:
                        stack.append((u, parent, ptr))
                        disc[v] = low[v] = timer
                        timer += 1
                        size[v] = 1
                        stack.append((v, u, indptr[v]))
                        advanced = True
                        break
                    if v != parent and disc[v] < low[u]:
                        low[u] = disc[v]
                if advanced:
                    continue
                if parent >= 0:
                    if low[u] < low[parent]:
                        low[parent] = low[u]
                    size[parent] += size[u]
                    if low[u] >= disc[parent]:
                        sep.setdefault(parent, []).append(size[u])
            comp_total = size[root]
            for u, sizes in sep.items():
                if u == root:
                    if len(sizes) > 1:
                        guarded[root] = (comp_total - 1) - max(sizes)
                else:
                    parent_side = comp_total - 1 - sum(sizes)
                    guarded[u] = (comp_total - 1) - max([*sizes, parent_side])
        return {self.ids[u]: g for u, g in guarded.items() if g > 0}


# --------------------------------------------------------------------------
def sidecar_path(store: Store) -> str | None:
    path = getattr(store, "path", ":memory:")
    if str(path) == ":memory:":
        return None
    return f"{path}{_SUFFIX}"


def _config_allows(store: Store) -> bool:
    from .config import load_config
    root = store.get_meta("root")
    try:
        return load_config(root).adjacency_cache
    except Exception:  # noqa: BLE001 — config trouble never blocks a sweep
        return True


def build_cache(store: Store) -> bool:
    """Derive the sidecar from the store. Returns False (never raises) when it
    can't or shouldn't: no numpy, `:memory:`, unwritable directory, or the graph
    changed generation mid-read (a concurrent reindex — the fresh generation's
    first sweep will rebuild)."""
    if _np is None:
        return False
    target = sidecar_path(store)
    if target is None:
        return False
    from .model import Relation
    gen = current_generation(store)
    rel_names = [r.value for r in Relation]
    code = {v: i for i, v in enumerate(rel_names)}

    ids = [r[0] for r in store.conn.execute("SELECT id FROM nodes ORDER BY id")]
    if any(not isinstance(i, str) or "\n" in i for i in ids):
        return False  # a hostile/corrupt id would corrupt the line-mapped nodes.txt
    idx = {nid: i for i, nid in enumerate(ids)}
    n_nodes = len(ids)

    def _read(query, cap, kinds):
        """Fetch `query` rows into parallel arrays, translating each column
        through the (idx | code | set_index | passthrough) map for its kind;
        rows with any untranslatable key are skipped (R29A / corrupt-row
        parity — an edge to a missing node or unknown relation contributes
        nothing, flat or grouped)."""
        arrs = [_np.empty(cap, _np.int32 if k is not int else _np.uint8)
                for k in kinds]
        cur = store.conn.execute(query)
        n = 0
        while True:
            rows = cur.fetchmany(100_000)
            if not rows:
                break
            for row in rows:
                vals = []
                for v, k in zip(row, kinds, strict=True):
                    t = v if k is int else k.get(v)
                    if t is None:
                        break
                    vals.append(t)
                else:
                    for a, t in zip(arrs, vals, strict=True):
                        a[n] = t
                    n += 1
        return [a[:n] for a in arrs]

    # flat edges (branch 1 of edges_all)
    fcap = store.conn.execute(
        "SELECT COUNT(*) FROM edges WHERE dst_id IS NOT NULL").fetchone()[0]
    src, dst, rel, conf = _read(
        "SELECT src, dst_id, relation, provenance = 'extracted' "
        "FROM edges WHERE dst_id IS NOT NULL", fcap, (idx, idx, code, int))
    rel = rel.astype(_np.uint8)

    # interned candidate sets: dense index in set_id order; members in PK order
    # (set_id, dst_id) — the probe order of the edges_all join branch.
    set_index = {sid: i for i, (sid,) in enumerate(store.conn.execute(
        "SELECT set_id FROM cand_sets ORDER BY set_id"))}
    n_sets = len(set_index)
    mcap = store.conn.execute("SELECT COUNT(*) FROM cand_members").fetchone()[0]
    m_set, m_dst = _read(
        "SELECT set_id, dst_id FROM cand_members ORDER BY set_id, dst_id",
        mcap, (set_index, idx))

    # group rows in rowid order (the join branch's outer scan order)
    gcap = store.conn.execute("SELECT COUNT(*) FROM edge_groups").fetchone()[0]
    gsrc, gset, grel, gconf = _read(
        "SELECT src, set_id, relation, provenance = 'extracted' "
        "FROM edge_groups ORDER BY id", gcap, (idx, set_index, code, int))

    if current_generation(store) != gen:
        return False  # graph changed under us; don't persist a torn snapshot

    set_indptr = _np.zeros(n_sets + 1, _np.int64)
    _np.cumsum(_np.bincount(m_set, minlength=n_sets), out=set_indptr[1:])
    logical = len(src) + int(_np.diff(set_indptr)[gset].sum()) if len(gset) \
        else len(src)

    tmp = f"{target}.tmp{os.getpid()}"
    try:
        os.makedirs(tmp, exist_ok=True)

        def csr(key, n_rows, prefix, cols):
            """Stable-sort the entry arrays by `key` and save them CSR-shaped:
            within a row, stored scan order survives — the view-order parity
            the scan differential pins."""
            order = _np.argsort(key, kind="stable")
            for name, arr, packed in cols:
                arr = arr[order]
                _np.save(f"{tmp}/{prefix}_{name}.npy",
                         _np.packbits(arr) if packed else arr)
            indptr = _np.zeros(n_rows + 1, _np.int64)
            _np.cumsum(_np.bincount(key, minlength=n_rows), out=indptr[1:])
            _np.save(f"{tmp}/{prefix}_indptr.npy", indptr)

        csr(src, n_nodes, "fwd", [("indices", dst, False), ("rel", rel, False),
                                  ("conf", conf, True)])
        csr(dst, n_nodes, "rev", [("indices", src, False), ("rel", rel, False),
                                  ("conf", conf, True)])
        csr(gsrc, n_nodes, "grp", [("set", gset, False), ("rel", grel, False),
                                   ("conf", gconf, True)])
        csr(gset, n_sets, "setuse", [("src", gsrc, False), ("rel", grel, False),
                                     ("conf", gconf, True)])
        csr(m_dst, n_nodes, "mem", [("set", m_set, False)])
        _np.save(f"{tmp}/set_indptr.npy", set_indptr)
        _np.save(f"{tmp}/set_members.npy", m_dst)  # already (set, dst)-ordered
        with open(f"{tmp}/nodes.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(ids))
        with open(f"{tmp}/manifest.json", "w", encoding="utf-8") as f:
            json.dump({"version": _VERSION, "generation": gen,
                       "node_count": n_nodes, "edge_count": logical,
                       "flat_count": len(src), "group_count": len(gsrc),
                       "set_count": n_sets, "relations": rel_names}, f)
        shutil.rmtree(target, ignore_errors=True)
        os.rename(tmp, target)
        return True
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return False


def load_cache(store: Store, *, build: bool = True) -> AdjacencyCache | None:
    """The single entry point for sweeps: a fresh cache or None, never an error.
    Fresh = manifest generation and node count match the live store. When stale or
    absent (and `build` and config allow), rebuilds synchronously — the one-time
    cost that replaces every subsequent per-sweep dict build."""
    from .purity import pure_mode
    if _np is None or pure_mode():
        return None
    target = sidecar_path(store)
    if target is None:
        return None
    gen = current_generation(store)
    n_nodes = store.node_count()

    def _try_open() -> AdjacencyCache | None:
        try:
            mtime = os.path.getmtime(os.path.join(target, "manifest.json"))
            key = (gen, n_nodes, mtime)
            hit = _loaded.get(target)
            if hit and hit[0] == key:
                return hit[1]
            cache = AdjacencyCache(target)
            if (cache.manifest.get("generation") == gen
                    and cache.manifest.get("node_count") == n_nodes):
                _loaded[target] = (key, cache)
                return cache
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
        return None

    cache = _try_open()
    if cache is not None:
        return cache

    def _try_patch() -> AdjacencyCache | None:
        """Incremental refresh (v3.40.0): when the sidecar is stale by a chain of
        replace_file edits whose touched rows were captured (adj_delta:{g} metas),
        patch a row overlay instead of paying the full rebuild (~2 min at HA
        scale). Any gap, tombstone, oversize, or error -> None (full rebuild)."""
        import json as _json
        try:
            mtime = os.path.getmtime(os.path.join(target, "manifest.json"))
            cache = AdjacencyCache(target)
            cg = int(cache.manifest.get("generation", "-1"))
            cur = int(gen)
            if not (0 <= cg < cur) or cur - cg > 64:
                return None
            srcs: set[str] = set()
            dsts: set[str] = set()
            new_ids: set[str] = set()
            for g in range(cg + 1, cur + 1):
                raw = store.get_meta(f"adj_delta:{g}")
                if not raw or raw == "FULL":
                    return None
                d = _json.loads(raw)
                srcs.update(d.get("srcs", ()))
                dsts.update(d.get("dsts", ()))
                new_ids.update(d.get("new_ids", ()))
            if len(srcs) + len(dsts) + len(new_ids) > 2048:
                return None
            if not cache.apply_delta(store, sorted(srcs), sorted(dsts),
                                     sorted(new_ids)):
                return None
            if cache.n != n_nodes:
                return None  # id drift the delta chain didn't explain: rebuild
            _loaded[target] = ((gen, n_nodes, mtime), cache)
            return cache
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None

    cache = _try_patch()
    if cache is not None:
        return cache
    if not build or _build_failed.get(target) == gen or not _config_allows(store):
        return None
    if not build_cache(store):
        _build_failed[target] = gen  # one attempt per index state, not per sweep
        return None
    return _try_open()
