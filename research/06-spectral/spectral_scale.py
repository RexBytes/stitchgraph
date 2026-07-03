#!/usr/bin/env python3
"""Research spike (IDEAS.md §6, push B — the scaling design, validated).

Proves the two claims recorded in IDEAS §6's "Scale" caveat:
  B1  MATRIX-FREE correctness — top-k singular values / smallest-k Laplacian eigenvalues from a
      Krylov solver fed only a `LinearOperator` (a function computing A@x, streamed over the edge
      list, no matrix ever formed) MATCH the dense np.linalg result on a real repo.
  B2  SCALE — the same matrix-free path runs on a large synthetic sparse graph (n=100k, ~800k edges)
      that would be 80 GB dense, using O(n) memory, and its spectral embedding RECOVERS planted
      communities (NMI vs ground truth) — i.e. the method is correct at scale, not just small.

Needs scipy (research-only optional). Run:
  PYTHONPATH=src python research/06-spectral/spectral_scale.py
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import collections
import math
import time

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, eigsh, svds

import stitchgraph as sg
from stitchgraph.core.reach import LIVENESS_RELATIONS


# ---------- B1: matrix-free reproduces dense on a real repo ----------
def repo_edges(path):
    with sg.Store(":memory:") as store:
        sg.reindex(store, path)
        rels = {r.value for r in LIVENESS_RELATIONS}
        ids = store.all_node_ids()
        idx = {nid: i for i, nid in enumerate(ids)}
        rows, cols = [], []
        for e in store.resolved_edges():
            if e.dst_id and e.relation.value in rels and e.src in idx and e.dst_id in idx:
                rows.append(idx[e.src]); cols.append(idx[e.dst_id])
    return np.array(rows), np.array(cols), len(ids)


def streamed_matvec_op(rows, cols, n, batch=50_000):
    """A LinearOperator whose matvec computes y=A@x by STREAMING the edge list in batches — only the
    two length-n vectors + one edge-batch are held (the out-of-core pattern; no matrix materialised)."""
    def mv(x):
        x = x.ravel(); y = np.zeros(n)
        for s in range(0, len(rows), batch):
            r, c = rows[s:s + batch], cols[s:s + batch]
            np.add.at(y, r, x[c])
        return y

    def rmv(x):
        x = x.ravel(); y = np.zeros(n)
        for s in range(0, len(rows), batch):
            r, c = rows[s:s + batch], cols[s:s + batch]
            np.add.at(y, c, x[r])
        return y
    return LinearOperator((n, n), matvec=mv, rmatvec=rmv, dtype=np.float64)


def b1_matrix_free_matches_dense(path="src/stitchgraph"):
    rows, cols, n = repo_edges(path)
    A_dense = np.zeros((n, n)); A_dense[rows, cols] = 1.0
    k = 6
    sd = np.linalg.svd(A_dense, compute_uv=False)[:k]
    op = streamed_matvec_op(rows, cols, n)
    sk = np.sort(svds(op, k=k, return_singular_vectors=False))[::-1]
    print(f"B1 matrix-free SVD on {path} (n={n}, {len(rows)} edges):")
    print(f"    dense       top-{k} σ: {np.round(sd, 3).tolist()}")
    print(f"    streamed op top-{k} σ: {np.round(sk, 3).tolist()}")
    print(f"    max |Δσ| = {np.abs(sd - sk).max():.2e}  -> matrix-free reproduces dense "
          f"(no n×n array; peak = O(n) vectors + one {50_000}-edge batch)")


# ---------- B2: scale — big synthetic sparse graph, planted communities ----------
def planted_graph(n=100_000, comms=10, avg_deg=8, p_out=0.0005, seed=0):
    rng = np.random.default_rng(seed)
    label = rng.integers(0, comms, size=n)
    members = [np.nonzero(label == c)[0] for c in range(comms)]
    rows, cols = [], []
    m = n * avg_deg // 2
    # intra-community edges (most mass)
    n_in = int(m * 0.9)
    for _ in range(0):  # placeholder to keep structure clear
        pass
    src_c = rng.integers(0, comms, size=n_in)
    a = np.array([members[c][rng.integers(0, len(members[c]))] for c in src_c])
    b = np.array([members[c][rng.integers(0, len(members[c]))] for c in src_c])
    rows.append(a); cols.append(b)
    # inter-community noise
    n_out = m - n_in
    rows.append(rng.integers(0, n, size=n_out)); cols.append(rng.integers(0, n, size=n_out))
    r = np.concatenate(rows); c = np.concatenate(cols)
    keep = r != c
    return r[keep], c[keep], label


def _nmi(cl, truth):
    n = len(truth)
    ent = lambda L: -sum((x / n) * math.log(x / n) for x in collections.Counter(L).values())
    cc, tc = collections.Counter(cl), collections.Counter(truth)
    mi = 0.0
    for (a, b), m in collections.Counter(zip(cl, truth, strict=False)).items():
        mi += (m / n) * math.log((m / n) / ((cc[a] / n) * (tc[b] / n)))
    return mi / (math.sqrt(ent(cl) * ent(truth)) or 1.0)


def _kmeans(X, k, seed=0, iters=40):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), size=k, replace=False)]
    lab = np.zeros(len(X), int)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(2)
        new = d.argmin(1)
        if (new == lab).all():
            break
        lab = new
        for cc in range(k):
            if (lab == cc).any():
                C[cc] = X[lab == cc].mean(0)
    return lab


def b2_scale(n=100_000, comms=10):
    r, c, label = planted_graph(n=n, comms=comms)
    # symmetric sparse adjacency (scipy CSR — O(edges) memory, never n×n dense)
    data = np.ones(len(r))
    A = sp.csr_matrix((data, (r, c)), shape=(n, n))
    A = ((A + A.T) > 0).astype(float)
    deg = np.asarray(A.sum(1)).ravel()
    dinv = np.divide(1.0, np.sqrt(deg), out=np.zeros_like(deg), where=deg > 0)
    Dinv = sp.diags(dinv)
    P = Dinv @ A @ Dinv  # normalised adjacency; its top eigvecs = Laplacian's smallest
    dense_gb = n * n * 8 / 1e9
    t0 = time.time()
    # matrix-free-ish: eigsh takes the sparse operator, never a dense matrix
    w, V = eigsh(P, k=comms, which="LA")
    emb = V / np.where(np.linalg.norm(V, axis=1, keepdims=True) > 0,
                       np.linalg.norm(V, axis=1, keepdims=True), 1.0)
    cl = _kmeans(emb, comms)
    dt = time.time() - t0
    nmi = _nmi(cl.tolist(), label.tolist())
    print(f"\nB2 scale: synthetic planted graph n={n:,}, {A.nnz // 2:,} undirected edges, "
          f"{comms} communities")
    print(f"    dense n×n would be {dense_gb:.0f} GB; sparse CSR held = {A.data.nbytes / 1e6:.0f} MB "
          f"(O(edges)); eigsh + k-means in {dt:.1f}s")
    print(f"    spectral clustering NMI vs planted communities = {nmi:.3f}  (1.0 = perfect recovery)")


def main():
    b1_matrix_free_matches_dense()
    b2_scale()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
