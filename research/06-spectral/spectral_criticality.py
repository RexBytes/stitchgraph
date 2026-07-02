#!/usr/bin/env python3
"""Research spike (IDEAS.md §6, push C — criticality & controllability).

Two more §6 candidate decompositions, tested on a real repo (stitchgraph's own src):
  C1  ELECTRICAL / CURRENT-FLOW criticality — treat edges as unit conductances (the user's
      "components in a circuit" analogy taken literally). Rank nodes by current-flow closeness
      (via the Laplacian pseudo-inverse) and find ARTICULATION POINTS (cut vertices whose removal
      fragments the graph = unambiguous load-bearing parts). Compare to PageRank.
  C2  STRUCTURAL CONTROLLABILITY driver nodes (Liu–Slotine–Barabási 2011): the minimum driver set =
      n − |maximum matching| of the bipartite representation. Does the driver / leverage set line up
      with stitchgraph's already-detected entry points (exported/main/script roles)?

numpy+scipy, dense pseudo-inverse on the giant component (PoC scale). Run:
  PYTHONPATH=src python research/06-spectral/spectral_criticality.py
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components, maximum_bipartite_matching

import stitchgraph as sg
from stitchgraph.core.algebra import pagerank
from stitchgraph.core.reach import LIVENESS_RELATIONS


def load(path="src/stitchgraph"):
    with sg.Store(":memory:") as store:
        sg.reindex(store, path)
        rels = {r.value for r in LIVENESS_RELATIONS}
        ids = store.all_node_ids()
        idx = {nid: i for i, nid in enumerate(ids)}
        n = len(ids)
        A = np.zeros((n, n))
        for e in store.resolved_edges():
            if e.dst_id and e.relation.value in rels and e.src in idx and e.dst_id in idx:
                A[idx[e.src], idx[e.dst_id]] = 1.0
        entry = {nid for nid in ids if (nd := store.get_node(nid)) is not None
                 and ({"exported", "main", "script"} & set(nd.roles))}
        pr = pagerank(store)
    label = [i.split("::")[-1] for i in ids]
    return A, ids, label, entry, pr


def giant_idx(A_sym):
    n = A_sym.shape[0]
    seen = [False] * n
    nbr = [np.nonzero(A_sym[i])[0].tolist() for i in range(n)]
    best = []
    for s in range(n):
        if seen[s]:
            continue
        st, comp = [s], []
        seen[s] = True
        while st:
            v = st.pop(); comp.append(v)
            for w in nbr[v]:
                if not seen[w]:
                    seen[w] = True; st.append(w)
        if len(comp) > len(best):
            best = comp
    return sorted(best)


def articulation_points(G):
    """Cut vertices of an undirected adjacency (Tarjan), returned as local indices."""
    n = G.shape[0]
    nbr = [np.nonzero(G[i])[0].tolist() for i in range(n)]
    disc = [-1] * n; low = [0] * n; parent = [-1] * n; ap = set(); timer = [0]
    import sys
    sys.setrecursionlimit(max(10000, n * 4))

    def dfs(u):
        children = 0
        disc[u] = low[u] = timer[0]; timer[0] += 1
        for v in nbr[u]:
            if disc[v] == -1:
                parent[v] = u; children += 1
                dfs(v)
                low[u] = min(low[u], low[v])
                if parent[u] == -1 and children > 1:
                    ap.add(u)
                if parent[u] != -1 and low[v] >= disc[u]:
                    ap.add(u)
            elif v != parent[u]:
                low[u] = min(low[u], disc[v])
    for s in range(n):
        if disc[s] == -1:
            dfs(s)
    return ap


def main():
    A, ids, label, entry, pr = load()
    n = len(ids)
    A_sym = ((A + A.T) > 0).astype(float)
    giant = giant_idx(A_sym)
    G = A_sym[np.ix_(giant, giant)]
    ng = len(giant)
    print(f"repo: src/stitchgraph  n={n}  giant={ng}  entry-points={len(entry)}")

    # --- C1: current-flow closeness via Laplacian pseudo-inverse ---
    L = np.diag(G.sum(1)) - G
    Lp = np.linalg.pinv(L)
    # effective resistance R(i,j)=Lp[i,i]+Lp[j,j]-2Lp[i,j]; current-flow closeness = (n-1)/sum_j R(i,j)
    diagLp = np.diag(Lp)
    R = diagLp[:, None] + diagLp[None, :] - 2 * Lp
    cf_close = (ng - 1) / R.sum(1)
    pr_g = np.array([pr.get(ids[giant[i]], 0.0) for i in range(ng)])
    cf_top = set(np.argsort(-cf_close)[:10].tolist())
    pr_top = set(np.argsort(-pr_g)[:10].tolist())
    print("\nC1 current-flow closeness (electrical-network centrality):")
    for i in np.argsort(-cf_close)[:10]:
        print(f"    {cf_close[i]:7.3f}  {label[giant[i]]}")
    print(f"    Jaccard@10(current-flow, PageRank) = {len(cf_top & pr_top) / len(cf_top | pr_top):.2f}")

    ap = articulation_points(G)
    ap_entry = sum(1 for i in ap if ids[giant[i]] in entry)
    print(f"\nC1 articulation points (removal fragments the graph): {len(ap)} of {ng} nodes")
    print(f"    e.g. {[label[giant[i]] for i in list(ap)[:8]]}")
    print(f"    {ap_entry}/{len(ap)} articulation points are entry-points "
          f"(base rate {len(entry)}/{n} = {len(entry) / n:.0%})")

    # --- C2: structural-controllability driver nodes via maximum bipartite matching ---
    # Directed A over the giant; bipartite src->dst; driver nodes = unmatched targets.
    Ag = A[np.ix_(giant, giant)]
    B = sp.csr_matrix(Ag)
    match = maximum_bipartite_matching(B, perm_type="column")  # match[col] = row or -1
    matched_targets = set(np.nonzero(match >= 0)[0].tolist())
    drivers = [i for i in range(ng) if i not in matched_targets]
    nd = len(drivers)
    driver_entry = sum(1 for i in drivers if ids[giant[i]] in entry)
    print(f"\nC2 structural-controllability driver nodes: N_D={nd} ({nd / ng:.0%} of giant)")
    print(f"    {driver_entry}/{nd} drivers are entry-points; "
          f"recall = {driver_entry}/{sum(1 for i in range(ng) if ids[giant[i]] in entry)} "
          f"of the giant's entry-points are drivers")
    _, comp_labels = connected_components(sp.csr_matrix(G), directed=False)
    print(f"    (sanity: giant is 1 undirected component: {len(set(comp_labels.tolist())) == 1})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
