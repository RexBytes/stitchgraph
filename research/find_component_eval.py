#!/usr/bin/env python3
"""Research spike (IDEAS.md §3, QUANTIFY + ABLATE): how good is the purpose-aware component
locator, and which ingredient carries it?

`find_component.py` showed anecdotally (4 py packages) that test-exclusion + public-boost put the
right public component at the top. This turns that into a measured eval over a LABELLED query set
(py AND js packages), reporting precision@1 and MRR, and ablates the two ingredients:

  RAW            : plain find_similar, no filtering/boost.
  -TESTS         : drop test code (role + test-path).
  -TESTS+PUBLIC  : also boost exported/public API  (== the find_component recipe).

A hit is correct if the returned symbol's leaf name matches any of the query's ACCEPTABLE targets
(documented per query — the judgement is explicit and reproducible, not hidden in code). MRR uses the
rank of the first acceptable hit in the returned list.

Run:  PYTHONPATH=src python research/find_component_eval.py
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import sys
from pathlib import Path

import stitchgraph as sg

_TEST_HINTS = ("/test", "test_", "_test", "/tests/", "/spec", ".spec.", ".test.")


def _is_test_path(node_id: str) -> bool:
    rel = node_id.split("::", 1)[0].lower()
    leaf = rel.rsplit("/", 1)[-1]
    return any(h in rel for h in _TEST_HINTS) or leaf.startswith("test")


def rank(store, query: str, mode: str, limit: int = 10, public_boost: float = 0.15):
    """Return a ranked list of (leaf_name, is_public) under one ablation mode."""
    roles = {n.id: n.roles for n in store.all_nodes_full()}
    out = []
    for r in (sg.find_similar(store, query, limit=120).result or []):
        nid = r.get("id", "")
        rl = roles.get(nid, frozenset())
        if mode != "RAW" and ("test" in rl or _is_test_path(nid)):
            continue
        score = r.get("score") or 0.0
        if mode == "-TESTS+PUBLIC" and "exported" in rl:
            score += public_boost
        out.append((score, nid.split("::")[-1], "exported" in rl))
    out.sort(reverse=True)
    return [(name, pub) for _s, name, pub in out[:limit]]


# (corpus dir, query, {acceptable leaf-name substrings}). Acceptable sets are deliberately
# generous but purpose-specific — the point is "did it surface a right public entry point", not
# an exact-string match.
CASES = [
    ("click-8.4.1", "parse command line options and arguments", {"Command", "Group", "Option", "parse_args"}),
    ("click-8.4.1", "prompt the user for input on the terminal", {"prompt", "confirm", "getchar", "pause"}),
    ("requests-2.34.2", "send an http request and return the response", {"request", "Response", "send"}),
    ("requests-2.34.2", "manage a session with persistent cookies", {"Session"}),
    ("jinja2-3.1.6", "render a template by substituting variables", {"get_template", "render", "Template"}),
    ("jinja2-3.1.6", "escape html special characters", {"escape", "Markup"}),
    ("flask-3.1.3", "match a url route to a view handler", {"route", "add_url_rule", "Rule", "dispatch_request"}),
    ("flask-3.1.3", "return a json response", {"jsonify", "json"}),
    ("marshmallow-4.3.0", "serialize an object to a dictionary", {"dump", "dumps", "serialize"}),
    ("marshmallow-4.3.0", "validate and load input data", {"load", "loads", "validate"}),
    ("markdown-3.10.2", "convert markdown text to html", {"markdown", "convert", "Markdown"}),
    ("pygments-2.20.0", "highlight source code", {"highlight", "format"}),
    ("pygments-2.20.0", "tokenize source into tokens", {"get_tokens", "lex", "Lexer"}),
    # --- JS side ---
    ("express-5.2.1", "define a route handler for a url", {"route", "get", "use", "Router", "handle"}),
    ("axios-1.18.1", "send an http request", {"request", "Axios", "get", "post"}),
    ("dayjs-1.11.21", "format a date as a string", {"format", "Dayjs"}),
    ("marked-18.0.5", "convert markdown to html", {"marked", "parse", "lexer", "Lexer"}),
]


def _match(name: str, accept: set[str]) -> bool:
    return any(a.lower() in name.lower() for a in accept)


def main() -> int:
    modes = ["RAW", "-TESTS", "-TESTS+PUBLIC"]
    agg = {m: {"p1": 0, "mrr": 0.0, "n": 0} for m in modes}
    corpus = Path(__file__).resolve().parent / "_corpus" / "src"
    for pkg, query, accept in CASES:
        path = corpus / pkg
        if not path.exists():
            print(f"(skip {pkg} — not in corpus)")
            continue
        with sg.Store(":memory:") as store:
            sg.reindex(store, str(path))
            print(f'\nQ: "{query}"   [{pkg}]   accept={sorted(accept)}')
            for m in modes:
                ranked = rank(store, query, m)
                p1 = 1 if ranked and _match(ranked[0][0], accept) else 0
                rr = next((1 / (i + 1) for i, (nm, _) in enumerate(ranked) if _match(nm, accept)), 0.0)
                agg[m]["p1"] += p1
                agg[m]["mrr"] += rr
                agg[m]["n"] += 1
                top = ", ".join(f"{nm}{'*' if pub else ''}" for nm, pub in ranked[:3])
                print(f"   {m:14} P@1={p1} RR={rr:.2f}  top3: {top}")
    print("\n=== aggregate ===")
    for m in modes:
        a = agg[m]
        if a["n"]:
            print(f"  {m:14} P@1={a['p1']}/{a['n']} ({a['p1'] / a['n']:.0%})  MRR={a['mrr'] / a['n']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
