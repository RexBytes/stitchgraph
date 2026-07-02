#!/usr/bin/env python3
"""Research spike (IDEAS.md §6): does a spectral read of stitchgraph's *system matrix* (the node
adjacency `A`) surface structure the shipped PageRank/reachability sweeps don't?

The load-bearing question for §6: is an **authority-SVD** importance ranking *sensibly different and
useful* vs the shipped PageRank, and does the **graph Laplacian** give a usable subsystem
decomposition + a modularity number? This is a numpy-only proof of concept on a single repo (default:
stitchgraph's own `src/`, so it needs no network). Dense decomposition is fine at this scale
(~10^3 nodes); a real feature would use sparse iterative solvers (see §6 caveats).

Computes, over the same node universe + LIVENESS_RELATIONS PageRank uses:
  - PageRank (shipped `algebra.pagerank`)                        — baseline importance
  - HITS via SVD of A: authority = |V[:,0]|, hub = |U[:,0]|       — the §6 "importance" candidate
  - singular-value decay                                          — is the system low-rank (few modes)?
  - Laplacian L = D − A_sym: #near-zero eigenvalues (components), algebraic connectivity of the
    giant component (λ2 = spectral gap), Fiedler 2-way split                — decomposition + modularity

Reports top-10 by each importance measure + Jaccard@10 / Spearman overlap PageRank↔authority (are
they the same ranking or complementary?), the σ-decay, and the Fiedler partition.

Run:  PYTHONPATH=src python research/06-spectral/spectral_probe.py [path]
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

import numpy as np

import stitchgraph as sg
from stitchgraph.core.algebra import pagerank
from stitchgraph.core.reach import LIVENESS_RELATIONS


def build_adjacency(store):
    """Dense directed adjacency A over LIVENESS_RELATIONS (A[i,j]=1 : node i -> node j), plus the
    id list and id->leaf-label map. Same node universe / relations the shipped PageRank uses."""
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
    label = {}
    for node in store.all_nodes_full():
        label[node.id] = node.id.split("::")[-1] or node.id
    return A, ids, [label.get(i, i.split("::")[-1]) for i in ids]


def _top(scores: np.ndarray, labels, k=10):
    order = np.argsort(-scores)[:k]
    return [(labels[i], float(scores[i])) for i in order], set(order.tolist())


def _components(adj_sym):
    """Connected components of the undirected adjacency (list of index-lists), largest first."""
    n = adj_sym.shape[0]
    seen = [False] * n
    nbr = [np.nonzero(adj_sym[i])[0].tolist() for i in range(n)]
    comps = []
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
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    return comps


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = np.argsort(np.argsort(-a))
    rb = np.argsort(np.argsort(-b))
    ra, rb = ra - ra.mean(), rb - rb.mean()
    denom = (np.sqrt((ra * ra).sum()) * np.sqrt((rb * rb).sum())) or 1.0
    return float((ra * rb).sum() / denom)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "src/stitchgraph"
    with sg.Store(":memory:") as store:
        sg.reindex(store, path)
        A, ids, labels = build_adjacency(store)
        pr = pagerank(store)
    n = len(ids)
    print(f"repo: {path}   nodes={n}   edges={int(A.sum())}")
    pr_vec = np.array([pr.get(i, 0.0) for i in ids])

    # --- HITS via SVD of A ---
    U, S, Vt = np.linalg.svd(A, full_matrices=False)
    hub = np.abs(U[:, 0])            # out-side principal mode (orchestrators)
    authority = np.abs(Vt[0, :])     # in-side principal mode (depended-upon)
    energy = float((S[:10] ** 2).sum() / (S ** 2).sum()) if S.sum() else 0.0

    pr_top, pr_set = _top(pr_vec, labels)
    au_top, au_set = _top(authority, labels)
    hub_top, hub_set = _top(hub, labels)

    print("\n--- top-10 PageRank (shipped baseline) ---")
    for name, s in pr_top:
        print(f"   {s:8.5f}  {name}")
    print("\n--- top-10 SVD authority (most depended-upon) ---")
    for name, s in au_top:
        print(f"   {s:8.5f}  {name}")
    print("\n--- top-10 SVD hub (orchestrators) ---")
    for name, s in hub_top:
        print(f"   {s:8.5f}  {name}")

    jac = len(pr_set & au_set) / len(pr_set | au_set)
    print("\n--- PageRank vs authority: same ranking or complementary? ---")
    print(f"   Jaccard@10(PageRank, authority) = {jac:.2f}   (0=disjoint, 1=identical top-10)")
    print(f"   Spearman rank corr (all nodes)  = {_spearman(pr_vec, authority):+.3f}")
    print(f"\n--- singular-value decay (is the system low-rank?) ---")
    print(f"   top-10 σ: {np.round(S[:10], 2).tolist()}")
    print(f"   fraction of matrix energy in top-10 modes: {energy:.1%}")

    # --- Laplacian: components + Fiedler split of the giant component ---
    A_sym = ((A + A.T) > 0).astype(np.float64)
    comps = _components(A_sym)
    giant = comps[0]
    print(f"\n--- Laplacian decomposition ---")
    print(f"   connected components (undirected): {len(comps)}   "
          f"(sizes: {[len(c) for c in comps[:6]]}{' …' if len(comps) > 6 else ''})")
    G = A_sym[np.ix_(giant, giant)]
    L = np.diag(G.sum(1)) - G
    w, V = np.linalg.eigh(L)
    lam2 = float(w[1]) if len(w) > 1 else 0.0
    fiedler = V[:, 1] if V.shape[1] > 1 else np.zeros(len(giant))
    left = [giant[i] for i in range(len(giant)) if fiedler[i] < 0]
    right = [giant[i] for i in range(len(giant)) if fiedler[i] >= 0]
    print(f"   giant component: {len(giant)} nodes; algebraic connectivity λ2 (spectral gap) = {lam2:.4f}")
    print(f"   Fiedler 2-way split: {len(left)} | {len(right)} nodes")

    def _dirs(members):
        c = collections.Counter(ids[i].split("::", 1)[0].split("/")[-2]
                                if "/" in ids[i].split("::", 1)[0] else "."
                                for i in members)
        return ", ".join(f"{d}:{n}" for d, n in c.most_common(5))
    print(f"     side A top dirs: {_dirs(left)}")
    print(f"     side B top dirs: {_dirs(right)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
