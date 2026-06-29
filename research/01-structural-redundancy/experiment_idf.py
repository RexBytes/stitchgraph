"""Experiment 01b — IDF-weighted structural redundancy (precision pass over experiment.py).

experiment.py's raw set-Jaccard is dominated by HUB CALLEES: in an edge-building module,
{Edge, Provenance, Relation, append} are touched by nearly every function, so any two
edge-emitters look ~0.8 similar without being redundant. This pass down-weights common
callees by IDF — weight(c) = log(N / df(c)) — so sharing a RARE callee counts far more than
sharing a ubiquitous one. A high weighted score means "these two share *distinctive* helpers",
which is what a genuine refactor candidate looks like.

Run: python research/01-structural-redundancy/experiment_idf.py [PATH]   (default: src/stitchgraph)
"""
from __future__ import annotations

import itertools
import math
import sys

from experiment import FUNC_KINDS, MIN_SIG, load

NEAR_W = 0.60   # weighted-Jaccard threshold (lower than raw 0.70: weighting shrinks scores)


def main(path: str) -> None:
    _, nodes, out = load(path)
    funcs = {
        nid: out.get(nid, set())
        for nid, n in nodes.items()
        if n["kind"] in FUNC_KINDS and len(out.get(nid, ())) >= MIN_SIG
    }
    n_funcs = len(funcs)

    # document frequency of each (rel, leaf) callee across functions
    df: dict[tuple, int] = {}
    for sig in funcs.values():
        for c in sig:
            df[c] = df.get(c, 0) + 1
    idf = {c: math.log(n_funcs / d) for c, d in df.items()}

    def wj(sa: set, sb: set) -> float:
        inter = sa & sb
        union = sa | sb
        wi = sum(idf[c] for c in inter)
        wu = sum(idf[c] for c in union)
        return wi / wu if wu else 0.0

    print(f"corpus: {path}  (functions={n_funcs})")
    top = sorted(df.items(), key=lambda x: -x[1])[:6]
    print("  most common callees (hub noise IDF≈0): "
          + ", ".join(f"{c[1]}×{d}" for c, d in top) + "\n")

    items = list(funcs.items())
    scored = []
    for (a, sa), (b, sb) in itertools.combinations(items, 2):
        if len(sa & sb) < MIN_SIG:
            continue
        score = wj(sa, sb)
        if score >= NEAR_W:
            # how much of the score comes from DISTINCTIVE (rare) shared callees?
            shared = sorted(sa & sb, key=lambda c: -idf[c])
            distinctive = [c for c in shared if df[c] <= max(2, n_funcs // 20)]
            scored.append((score, a, b, shared, distinctive))
    scored.sort(reverse=True, key=lambda x: x[0])

    print(f"== IDF-weighted near-duplicates (wJ >= {NEAR_W}): {len(scored)} ==")
    for score, a, b, shared, distinctive in scored[:20]:
        print(f"  wJ={score:.2f}  {a.split('::')[-1]}  ~  {b.split('::')[-1]}")
        print(f"      distinctive shared ({len(distinctive)}): "
              + (", ".join(c[1] for c in distinctive) or "— none (all hub callees)"))
    if not scored:
        print("  (none) — every high-raw-Jaccard pair was hub-callee noise.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "src/stitchgraph")
