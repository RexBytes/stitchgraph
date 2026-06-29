"""Measure the graph-diff oracle on a REAL cross-language twin (Q2, honest number).

The demo.py toys were too clean. Here we index a faithful Python<->JS translation of the
same ~9-function recursive-descent calculator (fixtures/calc_py, fixtures/calc_js) and ask:
in leaf mode, what fraction of each side's structure is MATCHED across the language hop, and
what is the residual delta? The residual is the honest delta-to-noise figure — and we classify
each residual as either a genuine difference or a known extractor-asymmetry artifact.

Run: PYTHONPATH=src:research/graphdiff python research/graphdiff/measure_translation.py
"""
from __future__ import annotations

import collections
import pathlib

from graphdiff import _edge_keys, _node_keys, diff, index, summarize

HERE = pathlib.Path(__file__).parent
PY = str(HERE / "fixtures" / "calc_py")
JS = str(HERE / "fixtures" / "calc_js")


def _overlap(ca: collections.Counter, cb: collections.Counter) -> tuple[int, int, int]:
    inter = sum((ca & cb).values())
    return inter, sum(ca.values()), sum(cb.values())


def main() -> None:
    a, b = index(PY), index(JS)

    print("Cross-language twin: recursive-descent calculator (Python <-> JS)")
    print(f"  python: {len(a['nodes'])} nodes, {len(a['edges'])} edges")
    print(f"  js    : {len(b['nodes'])} nodes, {len(b['edges'])} edges\n")

    for mode in ("id", "leaf"):
        na, nb = _node_keys(a, mode), _node_keys(b, mode)
        ea, eb = _edge_keys(a, mode), _edge_keys(b, mode)
        ni, na_t, nb_t = _overlap(na, nb)
        ei, ea_t, eb_t = _overlap(ea, eb)
        print(f"== mode={mode} ==")
        print(f"  nodes matched: {ni}/{na_t} py  {ni}/{nb_t} js"
              f"   (recall {ni/na_t:.0%} / {ni/nb_t:.0%})")
        print(f"  edges matched: {ei}/{ea_t} py  {ei}/{eb_t} js"
              f"   (recall {ei/ea_t:.0%} / {ei/eb_t:.0%})")
        print()

    print("-- leaf-mode residual deltas (the honest noise) --")
    d = diff(a, b, mode="leaf")
    print(summarize(d))

    # classify residuals: which are KNOWN extractor-asymmetry artifacts vs real differences?
    print("\n-- residual classification --")
    artifacts = {
        "Module": "module-node naming (calc vs calc) / file kind — extractor artifact",
        "console": "JS console.log builtin has no Python twin (print is a stmt) — language artifact",
        "print": "Python print builtin call — no JS function-node twin — language artifact",
        "parseInt": "JS numeric parse builtin vs Python int() — language artifact",
        "int": "Python int() builtin vs JS parseInt — language artifact",
        "len": "Python len() vs JS .length property — language artifact",
        "Parser": "constructor call: JS `new Parser` edge vs Python __init__ — extractor artifact",
    }
    residual_terms = collections.Counter()
    for item in d.nodes_only_a + d.nodes_only_b + d.edges_only_a + d.edges_only_b:
        for key in artifacts:
            if key in item:
                residual_terms[key] += 1
    for key, n in residual_terms.most_common():
        print(f"  [{n}x] {key}: {artifacts[key]}")
    print("\nReading: the CORE algorithm call-shape (tokenize/parse/_expr/_term/_factor/"
          "evaluate/calc recursion) is what we want matched; residuals should be dominated "
          "by language builtins + constructor/module-node conventions, not by lost algorithm edges.")


if __name__ == "__main__":
    main()
