"""Experiment 01 — structural redundancy / clone detection (research; question #1).

Thesis under test: stitchgraph's *structural* matrix can surface reducible code — specifically
STRUCTURAL CLONES (functions that call the same set of helpers in the same composition, even with
different syntax/locals that a token-differ misses) and near-duplicates.

Crucially this is a WITHIN-language / within-codebase use, so the prior §2 caveat (topology tracks
the *extractor*, not function, across languages) does NOT bite here — one extractor, one language,
so structural similarity reflects real code-shape similarity.

Method: for each Function/Method node, build a "callee fingerprint" = the set of (relation, callee
leaf-name) over its CALLS/REFERENCES out-edges. Then:
  * EXACT structural clones  = nodes sharing an identical fingerprint (size >= MIN_SIG)
  * NEAR duplicates          = node pairs with Jaccard(fingerprint) >= NEAR_J and >= MIN_SIG shared

Output is a ranked list of candidates — advisory, on-brand (the graph proposes, a human disposes).
Run: python research/01-structural-redundancy/experiment.py [PATH]   (default: src/stitchgraph)
"""
from __future__ import annotations

import collections
import itertools
import sys

import stitchgraph as sg

FUNC_KINDS = {"Function", "Method"}
STRUCTURAL_RELS = ("CALLS", "REFERENCES")
MIN_SIG = 3      # ignore tiny functions (<3 callees) — too much noise
NEAR_J = 0.70    # Jaccard threshold for "near duplicate"


def _leaf(name: str | None) -> str | None:
    return name.rsplit(".", 1)[-1] if name else name


def load(path: str):
    store = sg.Store(":memory:")
    sg.reindex(store, path)
    nodes = {
        r[0]: {"kind": r[1], "name": r[2], "file": r[3], "roles": r[4] or ""}
        for r in store.conn.execute("select id,kind,name,file,roles from nodes")
    }
    out: dict[str, set] = collections.defaultdict(set)
    q = ("select src,relation,dst_id,dst_symbol from edges "
         "where relation in ('CALLS','REFERENCES')")
    for src, rel, dst_id, dst_sym in store.conn.execute(q):
        callee = nodes[dst_id]["name"] if dst_id in nodes else dst_sym
        leaf = _leaf(callee)
        if leaf:
            out[src].add((rel, leaf))
    return store, nodes, out


def main(path: str) -> None:
    store, nodes, out = load(path)
    funcs = {
        nid: out.get(nid, set())
        for nid, n in nodes.items()
        if n["kind"] in FUNC_KINDS and len(out.get(nid, ())) >= MIN_SIG
    }
    print(f"corpus: {path}")
    print(f"  nodes={len(nodes)}  functions/methods with >= {MIN_SIG} callees={len(funcs)}\n")

    # --- exact structural clones -------------------------------------------------
    groups: dict[frozenset, list[str]] = collections.defaultdict(list)
    for nid, sig in funcs.items():
        groups[frozenset(sig)].append(nid)
    exact = sorted(
        ((sig, ids) for sig, ids in groups.items() if len(ids) > 1),
        key=lambda x: (-len(x[0]), -len(x[1])),
    )
    print(f"== EXACT structural clones: {len(exact)} group(s) ==")
    for sig, ids in exact[:15]:
        print(f"  [{len(ids)} fns share {len(sig)} callees] "
              + ", ".join(sorted(i.split('::')[-1] for i in ids)))
        print(f"      callees: {sorted(c for _, c in sig)}")

    # --- near-duplicate pairs ----------------------------------------------------
    items = list(funcs.items())
    near = []
    for (a, sa), (b, sb) in itertools.combinations(items, 2):
        if sa == sb:
            continue  # counted as exact
        inter = sa & sb
        if len(inter) >= MIN_SIG:
            j = len(inter) / len(sa | sb)
            if j >= NEAR_J:
                near.append((j, a, b, inter))
    near.sort(reverse=True, key=lambda x: x[0])
    print(f"\n== NEAR-duplicate pairs (Jaccard >= {NEAR_J}): {len(near)} ==")
    for j, a, b, inter in near[:15]:
        print(f"  J={j:.2f}  {a.split('::')[-1]}  ~  {b.split('::')[-1]}")
        print(f"      shared callees ({len(inter)}): {sorted(c for _, c in inter)}")

    print("\nNOTE: candidates are advisory. Structural similarity != semantic redundancy; "
          "a human/LLM confirms whether each pair is genuinely mergeable.")
    store.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "src/stitchgraph")
