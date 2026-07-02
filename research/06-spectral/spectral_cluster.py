#!/usr/bin/env python3
"""Research spike (IDEAS.md §6, push 2): the small probe showed the *decomposition* use-case is the
promising one (naive authority-SVD was dominated by duplicate-helper clusters; low-rank structure is
real). This tests the load-bearing decomposition claim directly:

  Q1  Does k-way SPECTRAL CLUSTERING of the call graph recover real subsystems? Measured against the
      directory structure as a ground-truth proxy — purity + normalised mutual information (NMI) —
      and compared to a naive baseline (assign every node to the biggest cluster).
  Q2  Does MODULE-AGGREGATED authority-SVD (sum node authority per file/dir) de-noise the
      duplicate-helper artifact and surface architecturally central modules?

numpy-only, dense normalised Laplacian on the giant component. Default repo: stitchgraph's own src.

Run:  PYTHONPATH=src python research/06-spectral/spectral_cluster.py [path]
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import collections
import math
import sys

import numpy as np

import stitchgraph as sg
from stitchgraph.core.reach import LIVENESS_RELATIONS


def build(store):
    rels = {r.value for r in LIVENESS_RELATIONS}
    ids = store.all_node_ids()
    idx = {nid: i for i, nid in enumerate(ids)}
    n = len(ids)
    A = np.zeros((n, n), dtype=np.float64)
    for e in store.resolved_edges():
        if e.dst_id is None or e.relation.value not in rels:
            continue
        if e.src in idx and e.dst_id in idx:
            A[idx[e.src], idx[e.dst_id]] = 1.0
    return A, ids


def _dir_label(nid: str) -> str:
    """Directory of the defining file, relative to the package root — the subsystem proxy."""
    rel = nid.split("::", 1)[0]
    parts = rel.split("/")
    if "stitchgraph" in parts:
        parts = parts[parts.index("stitchgraph") + 1:]
    d = "/".join(parts[:-1]) or "(root)"
    return d


def _kmeans(X, k, iters=50, seed=0):
    rng = np.random.default_rng(seed)
    centers = X[rng.choice(len(X), size=k, replace=False)]
    labels = np.zeros(len(X), dtype=int)
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


def _purity(clusters, truth) -> float:
    total = len(truth)
    hit = 0
    for c in set(clusters):
        members = [truth[i] for i in range(total) if clusters[i] == c]
        if members:
            hit += collections.Counter(members).most_common(1)[0][1]
    return hit / total


def _nmi(clusters, truth) -> float:
    n = len(truth)
    def _ent(labs):
        cnt = collections.Counter(labs)
        return -sum((c / n) * math.log(c / n) for c in cnt.values())
    hc, ht = _ent(clusters), _ent(truth)
    joint = collections.Counter(zip(clusters, truth, strict=False))
    mi = 0.0
    cc, tc = collections.Counter(clusters), collections.Counter(truth)
    for (c, t), nct in joint.items():
        p = nct / n
        mi += p * math.log(p / ((cc[c] / n) * (tc[t] / n)))
    denom = math.sqrt(hc * ht) or 1.0
    return mi / denom


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "src/stitchgraph"
    with sg.Store(":memory:") as store:
        sg.reindex(store, path)
        A, ids = build(store)
    n = len(ids)
    A_sym = ((A + A.T) > 0).astype(np.float64)

    # giant component
    seen = [False] * n
    nbr = [np.nonzero(A_sym[i])[0].tolist() for i in range(n)]
    best = []
    for s in range(n):
        if seen[s]:
            continue
        stack, comp = [s], []
        seen[s] = True
        while stack:
            v = stack.pop()
            comp.append(v)
            for w in nbr[v]:
                if not seen[w]:
                    seen[w] = True
                    stack.append(w)
        if len(comp) > len(best):
            best = comp
    giant = sorted(best)
    G = A_sym[np.ix_(giant, giant)]
    truth = [_dir_label(ids[i]) for i in giant]
    k = len({t for t in truth})
    print(f"repo: {path}   giant component: {len(giant)} nodes   directory-subsystems (k): {k}")
    print(f"   dirs: {', '.join(f'{d}:{c}' for d, c in collections.Counter(truth).most_common())}")

    # normalised Laplacian L_sym = I - D^-1/2 A D^-1/2 ; smallest-k eigvecs -> row-normalise -> k-means
    deg = G.sum(1)
    dinv = np.divide(1.0, np.sqrt(deg), out=np.zeros_like(deg), where=deg > 0)
    Lsym = np.eye(len(giant)) - (dinv[:, None] * G * dinv[None, :])
    w, V = np.linalg.eigh(Lsym)
    emb = V[:, :k]
    rn = np.linalg.norm(emb, axis=1, keepdims=True)
    emb = emb / np.where(rn > 0, rn, 1.0)
    clusters = _kmeans(emb, k)

    baseline = _purity([0] * len(giant), truth)  # everything in one cluster
    print(f"\n--- Q1: does spectral clustering recover the directory subsystems? ---")
    print(f"   spectral-clustering purity = {_purity(clusters.tolist(), truth):.3f}   "
          f"(majority-class baseline {baseline:.3f})")
    print(f"   spectral-clustering NMI    = {_nmi(clusters.tolist(), truth):.3f}   "
          f"(0=random, 1=perfect)")
    # show what each cluster is dominated by
    print("   cluster -> dominant directory (share):")
    for c in sorted(set(clusters.tolist())):
        members = [truth[i] for i in range(len(giant)) if clusters[i] == c]
        top, cnt = collections.Counter(members).most_common(1)[0]
        print(f"     c{c:<2} n={len(members):<4} -> {top} ({cnt}/{len(members)} = {cnt / len(members):.0%})")

    # --- Q2: module-aggregated authority (de-noise the duplicate-helper artifact) ---
    U, S, Vt = np.linalg.svd(A, full_matrices=False)
    authority = np.abs(Vt[0, :])
    by_dir: collections.Counter[str] = collections.Counter()
    for i, nid in enumerate(ids):
        by_dir[_dir_label(nid)] += authority[i]
    print(f"\n--- Q2: module-aggregated authority (sum of node authority per directory) ---")
    for d, s in by_dir.most_common(8):
        print(f"   {s:7.3f}  {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
