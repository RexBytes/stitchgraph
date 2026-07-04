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

    def reverse_reachable(self, targets: Iterable[str],
                          relations: Iterable[Relation]) -> set[str]:
        targets = set(targets)
        seen = self._bfs(self.rev_indptr, self.rev_indices, self.rev_rel,
                         self.rev_conf, targets, self._rel_mask(relations), False)
        out = {self.ids[i] for i in _np.nonzero(seen)[0]}
        out.difference_update(targets)  # blast radius excludes the targets themselves
        return out

    def fan_in(self, relations: Iterable[Relation]) -> dict[str, int]:
        return self._degrees(self.rev_indptr, self.rev_rel, self.rev_conf, relations)

    def fan_out(self, relations: Iterable[Relation]) -> dict[str, int]:
        return self._degrees(self.fwd_indptr, self.fwd_rel, self.fwd_conf, relations)


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
    if _np is None:
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
