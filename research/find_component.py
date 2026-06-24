#!/usr/bin/env python3
"""Research spike (IDEAS.md §3): a purpose-aware "find the component that does X".

stitchgraph already has `find_similar(snippet)` (token similarity over name +
docstring + callees). The §2 spike showed the semantic axis is what carries
"what does this code do". This prototype turns `find_similar` into a usable
*component locator* by exploiting structure stitchgraph already models:

  - exclude TEST code (by `test` role AND by test-file path — the latter matters
    because function-local helpers nested in a test method are now first-class
    nodes, post the Panel Q/T nesting work, and don't carry the role themselves);
  - prefer EXPORTED / public-API symbols (the answer to "where is the thing that
    does X" is almost always public surface, not an internal helper).

Finding (see research/README.md): with test-exclusion + public-boost the top hit
is the right public component for clear queries (Response, Command, get_template,
Router.route). This is the on-brand §3 capability: the graph supplies verifiable,
role-aware structure; the ranking stays advisory. Exploratory, NOT packaged.

Run:  PYTHONPATH=src python research/find_component.py
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


def find_component(store, query: str, limit: int = 5, public_boost: float = 0.15):
    """find_similar, made purpose-aware: drop test code, boost exported API."""
    roles = {n.id: n.roles for n in store.all_nodes_full()}
    out = []
    for r in (sg.find_similar(store, query, limit=80).result or []):
        nid = r.get("id", "")
        rl = roles.get(nid, frozenset())
        if "test" in rl or _is_test_path(nid):
            continue
        score = (r.get("score") or 0.0) + (public_boost if "exported" in rl else 0.0)
        out.append((score, nid.split("::")[-1], "exported" in rl))
    out.sort(reverse=True)
    return out[:limit]


CASES = [
    ("research/_corpus/src/click-8.4.1",    "parse command line options and arguments"),
    ("research/_corpus/src/requests-2.34.2", "send an http request and return the response"),
    ("research/_corpus/src/jinja2-3.1.6",    "render a template by substituting variables"),
    ("research/_corpus/src/flask-3.1.3",     "match a url route to a view handler"),
]


def main() -> int:
    for path, query in CASES:
        if not Path(path).exists():
            print(f"(skip {path} — run archetype_fingerprint.py first to fetch corpus)")
            continue
        with sg.Store(":memory:") as store:
            sg.reindex(store, path)
            hits = find_component(store, query)
        pkg = path.split("/")[-1]
        print(f"\nQ: \"{query}\"   [{pkg}]")
        for score, name, public in hits:
            print(f"   {score:5.2f} {name}{'  *public' if public else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
