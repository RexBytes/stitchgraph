#!/usr/bin/env python3
"""Research spike (IDEAS.md §6, push A — decomposition done right).

Three questions the first spike left open:
  A1  Does DE-DUPLICATING structurally-identical nodes (same in+out neighbour set) rescue
      authority-SVD as an importance measure, or is it fundamentally worse than PageRank?
  A2  Do the higher singular modes (2,3,4 — not just the dominant boilerplate mode) carry
      meaningful structure?
  A3  SPECTRAL SUMMARIZE: label each spectral cluster with its most *distinctive* identifier tokens
      (compose §2/§3's semantic axis with §6's clusters) — do the clusters read as real subsystems?
      Measured (purity/NMI vs directory) AND shown (the auto-labels), across 3 repos.

numpy-only, dense on the giant component (PoC scale). Run:
  PYTHONPATH=src python research/06-spectral/spectral_decompose.py
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import collections
import math
import re

import numpy as np

import stitchgraph as sg
from stitchgraph.core.model import NodeKind
from stitchgraph.core.reach import LIVENESS_RELATIONS

REPOS = ["src/stitchgraph", "research/_corpus/src/flask-3.1.3", "research/_corpus/src/requests-2.34.2"]


def _toks(name: str) -> list[str]:
    out: list[str] = []
    for p in re.split(r"[._\-/]", name):
        out += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", p)
    return [t.lower() for t in out if len(t) > 1]


def load(path):
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
        exported = {nid for nid in ids
                    if (nd := store.get_node(nid)) is not None and "exported" in nd.roles}
    return A, ids, exported


def _giant(A_sym):
    n = A_sym.shape[0]
    seen = [False] * n
    nbr = [np.nonzero(A_sym[i])[0].tolist() for i in range(n)]
    best: list[int] = []
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


def _kmeans(X, k, seed=0, iters=60):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), size=k, replace=False)]
    lab = np.zeros(len(X), int)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None, :, :]) ** 2).sum(2)
        new = d.argmin(1)
        if (new == lab).all():
            break
        lab = new
        for c in range(k):
            if (lab == c).any():
                C[c] = X[lab == c].mean(0)
    return lab


def _dir(nid):
    parts = nid.split("::", 1)[0].split("/")
    if "stitchgraph" in parts:
        parts = parts[parts.index("stitchgraph") + 1:]
    return "/".join(parts[:-1]) or "(root)"


def _purity(cl, truth):
    return sum(collections.Counter(truth[i] for i in range(len(truth)) if cl[i] == c).most_common(1)[0][1]
               for c in set(cl)) / len(truth)


def _nmi(cl, truth):
    n = len(truth)
    ent = lambda L: -sum((c / n) * math.log(c / n) for c in collections.Counter(L).values())
    cc, tc = collections.Counter(cl), collections.Counter(truth)
    mi = 0.0
    for (c, t), m in collections.Counter(zip(cl, truth, strict=False)).items():
        mi += (m / n) * math.log((m / n) / ((cc[c] / n) * (tc[t] / n)))
    return mi / (math.sqrt(ent(cl) * ent(truth)) or 1.0)


def dedup(A):
    """Merge nodes sharing an identical (in-set, out-set) neighbour signature — genuine graph
    twins. Returns the collapsed adjacency + group sizes."""
    n = A.shape[0]
    sig = {}
    for i in range(n):
        key = (tuple(np.nonzero(A[i])[0].tolist()), tuple(np.nonzero(A[:, i])[0].tolist()))
        sig.setdefault(key, []).append(i)
    groups = list(sig.values())
    return len(groups), max(len(g) for g in groups)


def main():
    for path in REPOS:
        try:
            A, ids, exported = load(path)
        except Exception as e:  # noqa: BLE001
            print(f"(skip {path}: {e})")
            continue
        n = len(ids)
        A_sym = ((A + A.T) > 0).astype(float)
        giant = _giant(A_sym)
        ng = len(giant)
        print(f"\n{'=' * 78}\n{path}   nodes={n}  giant={ng}  exported={len(exported)}")

        # A1: dedup structural twins
        ngroups, biggest = dedup(A)
        print(f"  structural twins: {n - ngroups} nodes collapse into groups "
              f"(distinct signatures={ngroups}, biggest twin-group={biggest})")

        # A1/A2: authority-SVD modes 1-4, overlap with exported (proxy for 'real interface')
        U, S, Vt = np.linalg.svd(A, full_matrices=False)
        exp_idx = {i for i, nid in enumerate(ids) if nid in exported}
        for mode in range(min(4, len(S))):
            auth = np.abs(Vt[mode, :])
            top = set(np.argsort(-auth)[:10].tolist())
            hit = len(top & exp_idx)
            print(f"    authority mode {mode} (σ={S[mode]:.1f}): {hit}/10 of top are exported API")

        # A3: spectral clustering + semantic labels
        G = A_sym[np.ix_(giant, giant)]
        truth = [_dir(ids[i]) for i in giant]
        k = max(2, len({t for t in truth}))
        deg = G.sum(1)
        dinv = np.divide(1.0, np.sqrt(deg), out=np.zeros_like(deg), where=deg > 0)
        Lsym = np.eye(ng) - dinv[:, None] * G * dinv[None, :]
        _w, V = np.linalg.eigh(Lsym)
        emb = V[:, :k]
        emb = emb / np.where(np.linalg.norm(emb, axis=1, keepdims=True) > 0,
                             np.linalg.norm(emb, axis=1, keepdims=True), 1.0)
        cl = _kmeans(emb, k).tolist()
        print(f"  spectral clustering (k={k}): purity={_purity(cl, truth):.3f}  NMI={_nmi(cl, truth):.3f}")

        # global token df for TF-IDF cluster labels
        gdf: collections.Counter[str] = collections.Counter()
        toks_of = {i: _toks(ids[giant[i]].split("::")[-1]) for i in range(ng)}
        for i in range(ng):
            gdf.update(set(toks_of[i]))
        print("  spectral-summarize — each cluster's most distinctive name tokens + an exemplar:")
        for c in sorted(set(cl)):
            members = [i for i in range(ng) if cl[i] == c]
            tf: collections.Counter[str] = collections.Counter()
            for i in members:
                tf.update(toks_of[i])
            # distinctiveness = cluster tf * idf
            score = {t: tf[t] * math.log((ng + 1) / (gdf[t] + 1)) for t in tf}
            label = ", ".join(t for t, _ in sorted(score.items(), key=lambda x: -x[1])[:5])
            ex = next((ids[giant[i]].split("::")[-1] for i in members
                       if ids[giant[i]] in exported), ids[giant[members[0]]].split("::")[-1])
            topdir = collections.Counter(_dir(ids[giant[i]]) for i in members).most_common(1)[0]
            print(f"    c{c:<2} n={len(members):<4} [{topdir[0]} {topdir[1]}/{len(members)}] "
                  f"tokens: {label}   e.g. {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
