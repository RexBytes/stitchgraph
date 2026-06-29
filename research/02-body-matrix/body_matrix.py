"""Experiment 02 — function-BODY matrix (research; the level below the call graph).

stitchgraph's shipped matrix is inter-procedural: nodes are defs, edges are CALLS/REFERENCES/
INHERITS/IMPORTS. It is blind to what happens *inside* a function. This spike builds the next
level down — a per-function structural matrix from the body AST — and tests the claim from the
Q1 follow-up: a body-level representation catches clones the callee-fingerprint cannot see
(same logic, renamed variables, few or no helper calls).

Python-only on purpose: the deep stdlib `ast` lets us do this soundly without the 12-language
tax, and matches the project's "Python is the deepest extractor" stance.

Per function we build:
  * a NORMALISED token sequence  — pre-order AST walk with identifiers/literals anonymised
    (Name->VAR, Constant->CONST, arg->ARG; nested def/class/lambda collapsed to NESTED). This
    abstracts away names so a renamed copy (a "Type-2 clone") has an IDENTICAL sequence.
  * a node-type HISTOGRAM        — order-insensitive backstop (cosine similarity).
  * n_calls                      — distinct callee names, to mark functions the call-fingerprint
                                   (experiment.py, MIN_SIG=3) would never even consider.

Similarity:
  * exact normalised match  -> Type-1/Type-2 structural clone
  * SequenceMatcher ratio   -> near clone (order-sensitive)
  * histogram cosine        -> order-insensitive sanity check

Run: python research/02-body-matrix/body_matrix.py [PATH]   (default: src/stitchgraph)
"""
from __future__ import annotations

import ast
import collections
import difflib
import itertools
import math
import pathlib
import sys

MIN_TOK = 14     # ignore trivial bodies (getters, one-liners) — pure noise
NEAR = 0.85      # SequenceMatcher ratio threshold for "near clone"

_ANON = {ast.Name: "VAR", ast.Constant: "CONST", ast.arg: "ARG"}
_OPAQUE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _tokens(stmts) -> list[str]:
    out: list[str] = []

    def rec(node: ast.AST) -> None:
        if isinstance(node, _OPAQUE):
            out.append("NESTED")
            return
        out.append(_ANON.get(type(node), type(node).__name__))
        for child in ast.iter_child_nodes(node):
            rec(child)

    for s in stmts:
        rec(s)
    return out


def _n_calls(fn) -> int:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return len(names)


def iter_functions(tree: ast.AST, path: str):
    out = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((f"{path}::{prefix}{child.name}", child))
                visit(child, prefix + child.name + ".")

    visit(tree, "")
    return out


def analyze(path: str):
    funcs = []
    root = pathlib.Path(path)
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for qual, fn in iter_functions(tree, str(f)):
            toks = _tokens(fn.body)
            if len(toks) >= MIN_TOK:
                funcs.append({
                    "qual": qual, "toks": toks,
                    "hist": collections.Counter(toks), "ncalls": _n_calls(fn),
                })
    return funcs


def _cosine(a: collections.Counter, b: collections.Counter) -> float:
    keys = set(a) | set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _short(q: str) -> str:
    file, _, name = q.partition("::")
    return f"{name} ({pathlib.Path(file).name})"


def find_clones(funcs):
    exact: dict[tuple, list] = collections.defaultdict(list)
    for fn in funcs:
        exact[tuple(fn["toks"])].append(fn)
    exact_groups = sorted((g for g in exact.values() if len(g) > 1),
                          key=lambda g: (-len(g[0]["toks"]), -len(g)))

    near = []
    for a, b in itertools.combinations(funcs, 2):
        if a["toks"] == b["toks"]:
            continue
        r = difflib.SequenceMatcher(None, a["toks"], b["toks"]).ratio()
        if r >= NEAR:
            near.append((r, a, b, _cosine(a["hist"], b["hist"])))
    near.sort(reverse=True, key=lambda x: x[0])
    return exact_groups, near


def main(path: str) -> None:
    funcs = analyze(path)
    invisible = sum(1 for f in funcs if f["ncalls"] < 3)
    print(f"corpus: {path}")
    print(f"  functions with >= {MIN_TOK} body tokens: {len(funcs)}")
    print(f"  of which the call-fingerprint cannot see (<3 callees): "
          f"{invisible} ({invisible/len(funcs):.0%})\n")

    exact_groups, near = find_clones(funcs)

    print(f"== EXACT body clones (identical normalised AST): {len(exact_groups)} group(s) ==")
    for g in exact_groups[:12]:
        tag = "  [call-fingerprint BLIND]" if all(f["ncalls"] < 3 for f in g) else ""
        print(f"  [{len(g)} fns, {len(g[0]['toks'])} tokens]{tag}")
        print("      " + ", ".join(_short(f["qual"]) for f in g))

    print(f"\n== NEAR body clones (SequenceMatcher >= {NEAR}): {len(near)} ==")
    for r, a, b, cos in near[:15]:
        blind = " [call-fingerprint BLIND]" if a["ncalls"] < 3 and b["ncalls"] < 3 else ""
        print(f"  r={r:.2f} cos={cos:.2f}{blind}  {_short(a['qual'])}  ~  {_short(b['qual'])}")

    print("\nNOTE: structural, not semantic — advisory. A body clone is a *candidate*; a human/LLM "
          "confirms it's genuinely mergeable. 'call-fingerprint BLIND' = both functions have <3 "
          "callees, so experiment.py (the call-graph detector) could never surface them.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "src/stitchgraph")
