"""Body-aware graph diff — PDG/value-flow folded into the graph-diff oracle (Q3 substrate).

The call-level graph-diff (graphdiff.py) answers "same defs, same call edges?". That is blind to
*how* a function is implemented: two functions with the same name calling the same helpers the same
number of times can still have different control/data flow — a different (possibly wrong) body.

This adds the missing dimension. For every function present in BOTH sides, it compares their
expression-level value-flow fingerprint (experiment 04) and flags the ones whose body SHAPE
diverged even though the call graph did not. That is exactly the Q3 question: given a *planned*
structure and an *actual* implementation, where does the actual deviate — not just in which
functions/calls exist, but in what each function actually does?

Demo (run): a plan vs a faithful build (no deltas) and vs a buggy build whose call graph is
identical but where one function's data flow is wrong — the call-level diff says "equivalent",
the body-aware diff locates the offending function.

Run: PYTHONPATH=src:research/graphdiff:research/04-expr-dfg \
     python research/graphdiff/structure_diff.py
"""
from __future__ import annotations

import ast
import pathlib
import tempfile

import expr_dfg
from graphdiff import diff, index, summarize

BODY_THRESH = 0.95   # below this, a same-named function's body is "changed"


def _funcs(path: str) -> dict:
    out = {}
    root = pathlib.Path(path)
    for f in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue

        def visit(node, prefix):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, prefix + child.name + ".")
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[prefix + child.name] = child
                    visit(child, prefix + child.name + ".")

        visit(tree, "")
    return out


def body_diff(a_dir: str, b_dir: str):
    fa, fb = _funcs(a_dir), _funcs(b_dir)
    common = sorted(set(fa) & set(fb))
    changed = []
    for q in common:
        sim = expr_dfg.cosine(expr_dfg.fingerprint(fa[q]), expr_dfg.fingerprint(fb[q]))
        if sim < BODY_THRESH:
            changed.append((q, sim))
    return common, changed


def report(label: str, a_dir: str, b_dir: str):
    print("=" * 68)
    print(label)
    print("=" * 68)
    # 1. call-level oracle (graphdiff)
    d = diff(index(a_dir), index(b_dir), mode="id")
    print("call-level graph-diff:",
          "EQUIVALENT ✓" if d.is_empty else "deltas found")
    if not d.is_empty:
        print(summarize(d, limit=4))
    # 2. body-level oracle (this module)
    common, changed = body_diff(a_dir, b_dir)
    if not changed:
        print(f"body-level diff: all {len(common)} shared function bodies match ✓")
    else:
        print(f"body-level diff: {len(changed)}/{len(common)} function bodies CHANGED:")
        for q, sim in changed:
            print(f"    - {q}()  body similarity {sim:.2f}  (call graph unchanged)")
    print()


PLAN = {
    "score.py": (
        "def heavy(v):\n    return v * v\n\n"
        "def combine(x, y):\n    return x + y\n\n"
        "def score(a, b):\n"
        "    x = heavy(a)\n"
        "    y = heavy(b)\n"
        "    return combine(x, y)\n"
    )
}
ACTUAL_GOOD = PLAN
ACTUAL_BAD = {
    # same name, same calls (heavy x2, combine x1) -> identical call graph,
    # but the second heavy is fed `a` instead of `b`: a DATA-FLOW bug the call graph can't see.
    "score.py": (
        "def heavy(v):\n    return v * v\n\n"
        "def combine(x, y):\n    return x + y\n\n"
        "def score(a, b):\n"
        "    x = heavy(a)\n"
        "    y = heavy(a)\n"
        "    return combine(x, y)\n"
    )
}


def _write(d, files):
    for name, body in files.items():
        (pathlib.Path(d) / name).write_text(body)
    return d


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as p, tempfile.TemporaryDirectory() as g, \
            tempfile.TemporaryDirectory() as bad:
        _write(p, PLAN)
        _write(g, ACTUAL_GOOD)
        _write(bad, ACTUAL_BAD)
        report("PLAN  vs  faithful build", p, g)
        report("PLAN  vs  buggy build (data-flow bug, identical call graph)", p, bad)
        print("Takeaway: the call-level oracle reports both builds 'equivalent'. The body-aware "
              "oracle\nlocates score() in the buggy build — `b` no longer flows into the second "
              "heavy() call.\nThis is the Q3 plan-vs-actual check the call graph alone cannot make.")
