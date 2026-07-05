"""Similarity sidecar — the token-vector index that makes `find_similar` /
`find_component` interactive at scale (design §1; the "prebuilt index" gap
recorded in docs/PERFORMANCE.md).

The reference path re-derives everything per query: `resolved_edges(CALLS)`
materialises every edge (26.8M Edge objects on the megacorpus — the actual
3-minutes-per-query cost), then tokenises every code node. This sidecar bakes
all of that once into `<db>.simcache/`: an EXACT-vocabulary sparse TF matrix
(CSR: one L2-normalised row per FUNCTION/METHOD/CLASS node, token dims from a
stored vocab — no hashing trick, so scores match the reference cosine up to
float summation order) plus the node id list. A query tokenises the snippet,
maps tokens through the vocab (out-of-vocab tokens still count toward the query
norm, exactly like the reference), and scores every node in one CSR·vector
product: <1 s at 106k nodes.

Contracts (all inherited from the adjacency sidecar, adjcache.py):
- SQLite is authoritative; the sidecar is disposable — delete any time.
- generation-gated: `reindex`/`replace_file` bump `meta.generation`; a stale
  sidecar is refused and lazily rebuilt by the next query.
- numpy-gated (guarded import); config `[index] similarity_cache = false` or
  pure mode disables it; every absence falls back to the reference path.
- The DENSE-embedder path bypasses this sidecar entirely (a registered embedder
  changes the vector space; persisting embeddings is the recorded follow-up).
"""

from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING

try:
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from .store import Store

_VERSION = 1
_SUFFIX = ".simcache"
_loaded: dict[str, tuple[tuple, SimilarityCache]] = {}
_build_failed: dict[str, str] = {}


class SimilarityCache:
    def __init__(self, path: str) -> None:
        with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)
        if self.manifest.get("version") != _VERSION:
            raise ValueError(f"simcache version {self.manifest.get('version')}")
        ld = lambda n: _np.load(os.path.join(path, n), mmap_mode="r")  # noqa: E731
        self.indptr = ld("indptr.npy")
        self.indices = ld("indices.npy")
        self.data = ld("data.npy")
        with open(os.path.join(path, "ids.txt"), encoding="utf-8") as f:
            self.ids = f.read().split("\n") if os.path.getsize(
                os.path.join(path, "ids.txt")) else []
        with open(os.path.join(path, "vocab.txt"), encoding="utf-8") as f:
            vocab = f.read().split("\n") if os.path.getsize(
                os.path.join(path, "vocab.txt")) else []
        self.vocab = {tok: i for i, tok in enumerate(vocab)}

    def query(self, tokens: list[str], limit: int) -> list[tuple[str, float]]:
        """Cosine of the token-count query against every stored row — same maths
        as the reference `_cosine(Counter, Counter)`: out-of-vocab query tokens
        contribute to the query norm but match nothing."""
        from collections import Counter
        counts = Counter(tokens)
        if not counts:
            return []
        qnorm = float(_np.sqrt(sum(c * c for c in counts.values())))
        dims, weights = [], []
        for tok, c in counts.items():
            d = self.vocab.get(tok)
            if d is not None:
                dims.append(d)
                weights.append(float(c))
        if not dims:
            return []
        q = _np.zeros(len(self.vocab), _np.float32)
        q[dims] = weights
        try:
            from scipy.sparse import csr_matrix
            m = csr_matrix((self.data, self.indices, self.indptr),
                           shape=(len(self.ids), len(self.vocab)))
            scores = m @ q
        except Exception:  # noqa: BLE001 — scipy absent: bounded numpy fallback
            scores = _np.zeros(len(self.ids), _np.float32)
            data = _np.asarray(self.data)
            idxs = _np.asarray(self.indices)
            contrib = data * q[idxs]                       # per-nnz contribution
            rows = _np.repeat(_np.arange(len(self.ids)), _np.diff(self.indptr))
            _np.add.at(scores, rows, contrib)
        scores = scores / qnorm  # rows are pre-normalised; divide by query norm
        order = _np.argsort(-scores)
        out = []
        for i in order[: max(0, limit)]:
            s = float(scores[i])
            if s <= 0.0:
                break
            out.append((self.ids[i], s))
        return out


def sidecar_path(store: Store) -> str | None:
    path = getattr(store, "path", ":memory:")
    return None if str(path) == ":memory:" else f"{path}{_SUFFIX}"


def build_cache(store: Store) -> bool:
    """Derive the sidecar: one streaming pass over CALLS edges for callee tokens,
    one pass over code nodes for vectors. Returns False, never raises."""
    if _np is None:
        return False
    target = sidecar_path(store)
    if target is None:
        return False
    from .adjcache import current_generation
    from .model import NodeKind, Relation
    from .similar import _node_tokens  # single source of token truth

    gen = current_generation(store)
    callees: dict[str, list[str]] = {}
    cur = store.conn.execute(
        "SELECT src, dst_symbol FROM edges WHERE dst_id IS NOT NULL AND relation = ?",
        (Relation.CALLS.value,))
    while True:
        rows = cur.fetchmany(50_000)
        if not rows:
            break
        for src, sym in rows:
            if isinstance(src, str) and isinstance(sym, str):
                callees.setdefault(src, []).append(sym)

    code = [n for n in store.all_nodes_full()
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)]
    ids = [n.id for n in code]
    if any("\n" in i for i in ids):
        return False  # hostile id would corrupt the line-mapped ids.txt
    vocab: dict[str, int] = {}
    indptr = [0]
    indices: list[int] = []
    data: list[float] = []
    from collections import Counter
    for n in code:
        counts = Counter(_node_tokens(store, n, callees))
        norm = (sum(c * c for c in counts.values()) ** 0.5) or 1.0
        for tok, c in sorted(counts.items()):
            indices.append(vocab.setdefault(tok, len(vocab)))
            data.append(c / norm)
        indptr.append(len(indices))
    del callees

    if current_generation(store) != gen:
        return False  # graph changed mid-read — don't persist a torn snapshot
    tmp = f"{target}.tmp{os.getpid()}"
    try:
        os.makedirs(tmp, exist_ok=True)
        _np.save(f"{tmp}/indptr.npy", _np.array(indptr, _np.int64))
        _np.save(f"{tmp}/indices.npy", _np.array(indices, _np.int32))
        _np.save(f"{tmp}/data.npy", _np.array(data, _np.float32))
        with open(f"{tmp}/ids.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(ids))
        vocab_list = [t for t, _ in sorted(vocab.items(), key=lambda kv: kv[1])]
        with open(f"{tmp}/vocab.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(vocab_list))
        with open(f"{tmp}/manifest.json", "w", encoding="utf-8") as f:
            json.dump({"version": _VERSION, "generation": gen,
                       "node_count": store.node_count(), "rows": len(ids)}, f)
        shutil.rmtree(target, ignore_errors=True)
        os.rename(tmp, target)
        return True
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return False


def load_cache(store: Store, *, build: bool = True) -> SimilarityCache | None:
    """Fresh cache or None, never an error — the adjcache contract."""
    from .adjcache import current_generation
    from .purity import pure_mode
    if _np is None or pure_mode():
        return None
    target = sidecar_path(store)
    if target is None:
        return None
    from .config import load_config
    gen = current_generation(store)
    n_nodes = store.node_count()

    def _try_open() -> SimilarityCache | None:
        try:
            mtime = os.path.getmtime(os.path.join(target, "manifest.json"))
            key = (gen, n_nodes, mtime)
            hit = _loaded.get(target)
            if hit and hit[0] == key:
                return hit[1]
            cache = SimilarityCache(target)
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
    if not build or _build_failed.get(target) == gen:
        return None
    try:
        allowed = load_config(store.get_meta("root")).similarity_cache
    except Exception:  # noqa: BLE001
        allowed = True
    if not allowed:
        return None
    if not build_cache(store):
        _build_failed[target] = gen
        return None
    return _try_open()
