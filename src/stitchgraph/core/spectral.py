"""Spectral subsystem decomposition (design §6) — the structural counterpart to the semantic
`find_similar` axis. Partitions a repo's call/reference graph into its natural **subsystems** by
spectral clustering of the graph Laplacian, and labels each cluster with the identifier tokens that
most distinguish it (a "spectral-summarize"). Advisory and read-only — like `orient`/`risk` it never
feeds liveness (the cardinal rule is a CALL-layer property).

Promoted from `research/06-spectral/` (IDEAS §6): on a well-structured repo, spectral clustering of
the undirected call graph recovers the module structure (measured NMI vs the directory tree), and the
per-cluster distinctive-token labels name the subsystems ("fingerprint", "resolver", "body-builder").

Scale (IDEAS §6 "Scale" note): the top-k eigenvectors are all that's needed, so this is matrix-free
in spirit. With the optional `[spectral]` extra (scipy) it uses sparse ARPACK (`eigsh`) — O(k·edges),
no dense matrix — and has no size limit. Without scipy it falls back to a dense numpy
eigendecomposition, which is fine for typical repos but capped at `_DENSE_CAP` nodes (above that the
operation refuses and points at the extra). numpy is always required (it already backs `algebra`).
"""
from __future__ import annotations

import collections
import math
import re
from typing import Any

from .model import NodeKind
from .reach import LIVENESS_RELATIONS, _adjacency
from .store import Store

try:
    import numpy as np
    HAS_NUMPY = True
except Exception:  # noqa: BLE001 — numpy absent → the operation refuses cleanly
    HAS_NUMPY = False

try:
    from scipy.sparse import csr_matrix, diags  # noqa: F401
    from scipy.sparse.linalg import eigsh
    HAS_SCIPY = True
except Exception:  # noqa: BLE001 — no [spectral] extra → dense numpy fallback (capped)
    HAS_SCIPY = False

# Above this many nodes in the giant component, the dense (scipy-less) path is refused — build the
# n×n Laplacian only when it is cheap. With the [spectral] extra (sparse eigsh) there is no cap.
_DENSE_CAP = 2500
_CODE_KINDS = {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS}


def _toks(name: str) -> list[str]:
    """Identifier → lowercase word tokens (camelCase / snake_case / dotted split), len>1."""
    out: list[str] = []
    for part in re.split(r"[._\-/]", name):
        out += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part)
    return [t.lower() for t in out if len(t) > 1]


def _undirected(store: Store, relations) -> dict[str, set[str]]:
    directed = _adjacency(store, relations)
    g: dict[str, set[str]] = collections.defaultdict(set)
    for u, vs in directed.items():
        for v in vs:
            if u != v:
                g[u].add(v)
                g[v].add(u)
    return g


def _giant(g: dict[str, set[str]]) -> list[str]:
    """Largest connected component, as a sorted node-id list (deterministic)."""
    seen: set[str] = set()
    best: list[str] = []
    for start in sorted(g):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            u = stack.pop()
            comp.append(u)
            for w in g[u]:
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        if len(comp) > len(best):
            best = comp
    return sorted(best)


def _kmeans(X, k: int, seed: int = 0, iters: int = 100):
    """Lloyd k-means with deterministic k-means++ seeding. k-means++ (distance-weighted center
    choice) matters here: a plain random init can land both centres in the same cluster, which — with
    a clean spectral embedding — collapses the result to one cluster. Labels start at -1 (not 0) so
    the first real assignment can never be mistaken for convergence."""
    rng = np.random.default_rng(seed)
    centers = [X[int(rng.integers(len(X)))]]
    for _ in range(1, k):
        d2 = np.min(np.stack([((X - c) ** 2).sum(1) for c in centers]), axis=0)
        total = float(d2.sum())
        probs = (d2 / total) if total > 0 else np.full(len(X), 1.0 / len(X))
        centers.append(X[int(rng.choice(len(X), p=probs))])
    centers = np.array(centers)
    labels = np.full(len(X), -1, dtype=int)
    for _ in range(iters):
        d = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(2)
        new = d.argmin(1)
        if (new == labels).all():
            break
        labels = new
        for c in range(k):
            m = labels == c
            if m.any():
                centers[c] = X[m].mean(0)
    return labels


def _embedding(A_rows, A_cols, n: int, kdim: int):
    """Smallest-`kdim` eigenvectors of the normalised Laplacian L = I - D^-1/2 A D^-1/2, as an
    (n, kdim) array. Uses sparse ARPACK (scipy) when available — deterministic via a fixed start
    vector — else a dense numpy eigendecomposition. Returns (embedding, laplacian_eigenvalues)."""
    if HAS_SCIPY:
        A = csr_matrix((np.ones(len(A_rows)), (A_rows, A_cols)), shape=(n, n))
        A = ((A + A.T) > 0).astype(float)
        deg = np.asarray(A.sum(1)).ravel()
        dinv = np.divide(1.0, np.sqrt(deg), out=np.zeros_like(deg), where=deg > 0)
        D = diags(dinv)
        P = D @ A @ D  # normalised adjacency; its largest eigenvalues = Laplacian's smallest
        kk = min(kdim, n - 1)
        v0 = np.ones(n) / math.sqrt(n)  # fixed start → deterministic ARPACK
        vals, vecs = eigsh(P, k=kk, which="LA", v0=v0)
        order = np.argsort(-vals)  # descending P-eigenvalue = ascending Laplacian
        return vecs[:, order], (1.0 - vals[order])
    # dense fallback
    A = np.zeros((n, n))
    A[A_rows, A_cols] = 1.0
    A = ((A + A.T) > 0).astype(float)
    deg = A.sum(1)
    dinv = np.divide(1.0, np.sqrt(deg), out=np.zeros_like(deg), where=deg > 0)
    lsym = np.eye(n) - dinv[:, None] * A * dinv[None, :]
    vals, vecs = np.linalg.eigh(lsym)  # ascending
    return vecs[:, :kdim], vals[:kdim]


def _auto_k(eigvals) -> int:
    """Pick the cluster count from the largest eigengap in the smallest Laplacian eigenvalues
    (the classic spectral heuristic), bounded to [2, 12]."""
    v = sorted(float(x) for x in eigvals)
    if len(v) < 3:
        return 2
    gaps = [(v[i + 1] - v[i], i + 1) for i in range(1, min(len(v) - 1, 12))]
    if not gaps:
        return 2
    return max(2, max(gaps)[1])


def decompose(store: Store, k: int | None = None,
              relations=LIVENESS_RELATIONS) -> tuple[list[dict], dict]:
    """Spectral-cluster the giant component of the call/reference graph into subsystems, each with a
    distinctive-token label. Returns (clusters, meta). Each cluster: {size, label, dirs, exemplars,
    members}. `k` is the cluster count (auto via eigengap when None). Raises RuntimeError with a
    clear message if numpy is missing or the graph is too large for the dense fallback (install the
    `[spectral]` extra). Advisory — computed on demand, never feeds liveness."""
    if not HAS_NUMPY:
        raise RuntimeError("subsystem decomposition needs numpy")
    g = _undirected(store, relations)
    giant = _giant(g)
    n = len(giant)
    if n < 4:
        return [], {"giant": n, "clustered": 0, "solver": "none", "reason": "graph too small"}
    if not HAS_SCIPY and n > _DENSE_CAP:
        raise RuntimeError(
            f"giant component has {n} nodes (> {_DENSE_CAP}); install the 'spectral' extra "
            "(pip install 'stitchgraph[spectral]') for the sparse solver that scales past the cap")
    idx = {nid: i for i, nid in enumerate(giant)}
    rows, cols = [], []
    for u in giant:
        for v in g[u]:
            if v in idx:
                rows.append(idx[u])
                cols.append(idx[v])
    kdim = 16 if k is None else min(max(k, 2), n - 1)
    emb_full, eigvals = _embedding(np.array(rows), np.array(cols), n, kdim)
    k = _auto_k(eigvals) if k is None else max(2, min(k, n - 1))
    emb = emb_full[:, :k]
    norm = np.linalg.norm(emb, axis=1, keepdims=True)
    emb = emb / np.where(norm > 0, norm, 1.0)
    assign = _kmeans(emb, k)

    # names / roles / dirs for labelling
    nodes = {nd.id: nd for nd in store.all_nodes_full()}
    toks_of = {i: _toks(giant[i].split("::")[-1]) for i in range(n)}
    gdf: collections.Counter[str] = collections.Counter()
    for i in range(n):
        gdf.update(set(toks_of[i]))

    def _dir(nid: str) -> str:
        return nid.split("::", 1)[0]

    clusters: list[dict[str, Any]] = []
    for c in sorted(set(assign.tolist())):
        members = [giant[i] for i in range(n) if assign[i] == c]
        tf: collections.Counter[str] = collections.Counter()
        for i in range(n):
            if assign[i] == c:
                tf.update(toks_of[i])
        score = {t: tf[t] * math.log((n + 1) / (gdf[t] + 1)) for t in tf}
        label = " ".join(t for t, _ in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
        dirs = collections.Counter(_dir(m) for m in members)
        # exemplars: prefer exported/public code entities, then highest in-degree by member order
        exported = [m for m in members
                    if (nd := nodes.get(m)) is not None and "exported" in nd.roles
                    and nd.kind in _CODE_KINDS]
        pool = exported or [m for m in members
                            if (nd := nodes.get(m)) is not None and nd.kind in _CODE_KINDS]
        exemplars = [m.split("::")[-1] for m in (pool or members)[:5]]
        clusters.append({
            "size": len(members),
            "label": label or "(unlabelled)",
            "dirs": [d for d, _ in dirs.most_common(3)],
            "exemplars": exemplars,
            "members": members,
        })
    clusters.sort(key=lambda c: (-c["size"], c["label"]))
    total_nodes = len(store.all_node_ids())
    meta = {"giant": n, "clustered": n, "outside_giant": total_nodes - n, "k": k,
            "solver": "scipy" if HAS_SCIPY else "numpy-dense"}
    return clusters, meta
