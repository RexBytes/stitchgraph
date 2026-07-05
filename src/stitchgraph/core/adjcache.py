"""Mmapped CSR adjacency sidecar — memoized derivation, never the source of truth.

Every reachability/degree sweep used to rebuild its adjacency from SQLite into a
`dict[str, list[str]]` of Python strings on each call — 130 s and ~2 GB per op on a
16M-edge graph (Home Assistant, field analysis 2026-07-04), thrown away afterwards.
This module derives that adjacency ONCE into a compact binary sidecar next to the
index (`<db>.adjcache/`) and memory-maps it thereafter: 137 MB on disk for the same
16M-edge graph, 0.03 s to open, full liveness BFS in <1 s at ~350 MB RSS.

Layout (one little-endian .npy per array, `np.load(mmap_mode="r")`-able):
  manifest.json  {version, generation, node_count, edge_count, relations}
  nodes.txt      node ids, newline-separated; line number == int id (sorted ids)
  fwd_indptr.npy int64[N+1]  CSR over ALL resolved edges, rows = src
  fwd_indices.npy int32[E]
  fwd_rel.npy    uint8[E]    relation code = index into manifest["relations"]
  fwd_conf.npy   uint8[ceil(E/8)]  packed per-edge `provenance == extracted` bit
  rev_*          the same four, rows = dst (for fan-in / blast radius)

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

_VERSION = 1
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
        with open(os.path.join(path, "nodes.txt"), encoding="utf-8") as f:
            self.ids = f.read().split("\n") if os.path.getsize(
                os.path.join(path, "nodes.txt")) else []
        self.idx = {nid: i for i, nid in enumerate(self.ids)}
        self.n = len(self.ids)

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

    def _bfs(self, indptr, indices, rel, conf, seeds, allowed, confident_only):
        seen = _np.zeros(self.n, bool)
        seed_arr = self._seed_array(seeds)
        seen[seed_arr] = True
        frontier = seed_arr
        while frontier.size:
            starts = indptr[frontier]
            counts = (indptr[frontier + 1] - starts).astype(_np.int64)
            total = int(counts.sum())
            if total == 0:
                break
            # Gather every neighbour slice of the frontier in one shot (the SpMV step):
            # e_idx enumerates each row's [start, start+count) range back to back.
            offs = _np.repeat(starts - _np.concatenate(([0], _np.cumsum(counts)[:-1])),
                              counts)
            e_idx = _np.arange(total, dtype=_np.int64) + offs
            mask = allowed[rel[e_idx]]
            if confident_only:
                # packed-bit probe: bit i of the conf mask, no unpacking
                mask &= ((conf[e_idx >> 3] >> (7 - (e_idx & 7))) & 1).astype(bool)
            neigh = indices[e_idx[mask]]
            new = neigh[~seen[neigh]]
            seen[new] = True
            frontier = _np.unique(new)
        return seen

    def _degrees(self, indptr, rel, conf, relations, confident_only=False):
        allowed = self._rel_mask(relations)
        mask = allowed[_np.asarray(rel)]
        if confident_only:
            e = _np.arange(len(mask), dtype=_np.int64)
            mask &= ((conf[e >> 3] >> (7 - (e & 7))) & 1).astype(bool)
        if not len(mask):
            return {}
        starts = _np.minimum(indptr[:-1], len(mask) - 1)  # reduceat needs in-range
        counts = _np.add.reduceat(mask.astype(_np.int64), starts)
        counts[indptr[:-1] == indptr[1:]] = 0  # empty rows: reduceat repeats next value
        return {self.ids[i]: int(counts[i]) for i in _np.nonzero(counts)[0]}

    # -- the reach.py mirrors ------------------------------------------------
    def reachable(self, seeds: Iterable[str], relations: Iterable[Relation],
                  confident_only: bool = False) -> set[str]:
        seen = self._bfs(self.fwd_indptr, self.fwd_indices, self.fwd_rel,
                         self.fwd_conf, seeds, self._rel_mask(relations),
                         confident_only)
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
            starts = indptr[frontier]
            counts = (indptr[frontier + 1] - starts).astype(_np.int64)
            total = int(counts.sum())
            if total == 0:
                break
            offs = _np.repeat(starts - _np.concatenate(([0], _np.cumsum(counts)[:-1])),
                              counts)
            e_idx = _np.arange(total, dtype=_np.int64) + offs
            mask = allowed[rel[e_idx]]
            if confident_only:
                mask &= ((conf[e_idx >> 3] >> (7 - (e_idx & 7))) & 1).astype(bool)
            neigh = indices[e_idx[mask]]
            contrib = labels[_np.repeat(frontier, counts)[mask]]
            before = labels.copy()  # n × 8 B per round — cheap next to the edge gather
            _np.bitwise_or.at(labels, neigh, contrib)
            # A node re-enters the frontier only when it gained NEW lane bits, so
            # each lane's propagation terminates exactly like its solo BFS.
            frontier = _np.nonzero(labels != before)[0]
        out = []
        for g in range(len(seed_groups)):
            bit = _np.uint64(1 << g)
            out.append({self.ids[i] for i in _np.nonzero((labels & bit) != 0)[0]})
        return out

    def reverse_reachable(self, targets: Iterable[str],
                          relations: Iterable[Relation]) -> set[str]:
        targets = set(targets)
        seen = self._bfs(self.rev_indptr, self.rev_indices, self.rev_rel,
                         self.rev_conf, targets, self._rel_mask(relations), False)
        out = {self.ids[i] for i in _np.nonzero(seen)[0]}
        out.difference_update(targets)  # blast radius excludes the targets themselves
        return out

    def fan_in(self, relations: Iterable[Relation],
               confident_only: bool = False) -> dict[str, int]:
        return self._degrees(self.rev_indptr, self.rev_rel, self.rev_conf, relations,
                             confident_only)

    def fan_out(self, relations: Iterable[Relation],
                confident_only: bool = False) -> dict[str, int]:
        return self._degrees(self.fwd_indptr, self.fwd_rel, self.fwd_conf, relations,
                             confident_only)

    def _filtered_csr(self, relations: Iterable[Relation], *, drop_self: bool = False):
        """Materialise a relation-filtered forward CSR (indptr, indices, rows) so the
        Python traversals below touch only kept edges with no per-edge relation test.
        Vectorised; the transient row array is int32[E]."""
        allowed = self._rel_mask(relations)
        mask = allowed[_np.asarray(self.fwd_rel)]
        rows = _np.repeat(_np.arange(self.n, dtype=_np.int32),
                          _np.diff(self.fwd_indptr))
        if drop_self:
            mask &= rows != _np.asarray(self.fwd_indices)
        rows = rows[mask]
        indices = _np.asarray(self.fwd_indices)[mask]
        indptr = _np.zeros(self.n + 1, _np.int64)
        _np.cumsum(_np.bincount(rows, minlength=self.n), out=indptr[1:])
        return indptr, indices, rows

    def self_loops(self, relations: Iterable[Relation]) -> set[str]:
        """Node ids with a self-edge under `relations` (recursion markers for SCC)."""
        allowed = self._rel_mask(relations)
        rows = _np.repeat(_np.arange(self.n, dtype=_np.int32),
                          _np.diff(self.fwd_indptr))
        hit = rows[(rows == _np.asarray(self.fwd_indices))
                   & allowed[_np.asarray(self.fwd_rel)]]
        return {self.ids[i] for i in _np.unique(hit)}

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

    cap = store.conn.execute(
        "SELECT COUNT(*) FROM edges WHERE dst_id IS NOT NULL").fetchone()[0]
    src = _np.empty(cap, _np.int32)
    dst = _np.empty(cap, _np.int32)
    rel = _np.empty(cap, _np.uint8)
    conf = _np.empty(cap, _np.uint8)
    cur = store.conn.execute(
        "SELECT src, dst_id, relation, provenance = 'extracted' "
        "FROM edges WHERE dst_id IS NOT NULL")
    n = 0
    while True:
        rows = cur.fetchmany(100_000)
        if not rows:
            break
        for s, d, r, c in rows:
            si, di, ri = idx.get(s), idx.get(d), code.get(r)
            if si is None or di is None or ri is None:  # R29A / corrupt-row parity
                continue
            src[n], dst[n], rel[n], conf[n] = si, di, ri, c
            n += 1
    src, dst, rel, conf = src[:n], dst[:n], rel[:n], conf[:n]

    if current_generation(store) != gen:
        return False  # graph changed under us; don't persist a torn snapshot

    tmp = f"{target}.tmp{os.getpid()}"
    try:
        os.makedirs(tmp, exist_ok=True)

        def csr(key, prefix):
            order = _np.argsort(key, kind="stable")
            _np.save(f"{tmp}/{prefix}_indices.npy",
                     (dst if prefix == "fwd" else src)[order])
            _np.save(f"{tmp}/{prefix}_rel.npy", rel[order])
            _np.save(f"{tmp}/{prefix}_conf.npy", _np.packbits(conf[order]))
            indptr = _np.zeros(n_nodes + 1, _np.int64)
            _np.cumsum(_np.bincount(key, minlength=n_nodes), out=indptr[1:])
            _np.save(f"{tmp}/{prefix}_indptr.npy", indptr)

        csr(src, "fwd")
        csr(dst, "rev")
        with open(f"{tmp}/nodes.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(ids))
        with open(f"{tmp}/manifest.json", "w", encoding="utf-8") as f:
            json.dump({"version": _VERSION, "generation": gen,
                       "node_count": n_nodes, "edge_count": n,
                       "relations": rel_names}, f)
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
    if not build or _build_failed.get(target) == gen or not _config_allows(store):
        return None
    if not build_cache(store):
        _build_failed[target] = gen  # one attempt per index state, not per sweep
        return None
    return _try_open()
