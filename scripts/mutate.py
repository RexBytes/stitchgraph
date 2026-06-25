#!/usr/bin/env python3
"""Minimal in-house mutation tester — the meta-oracle.

Validates that the test suite (oracles included) actually *kills* injected bugs, rather
than merely executing the code. A SURVIVED mutant is a line whose behaviour no test pins:
either add the test, or decide the line is genuinely untestable and record why.

Deliberately tiny and dependency-free (AST-based, one mutation at a time, revert after):
`mutmut`/`cosmic-ray` are heavier and `mutmut` 3.x's mutants/-copy model fights this repo's
src-layout + editable install. Keep this cheap; if it grows, reach for a real tool.

    python scripts/mutate.py src/stitchgraph/core/envelope.py \
        -- python -m pytest -x -q tests/test_core.py tests/test_properties.py

Exit non-zero if any mutant SURVIVED (so CI/nightly can gate on it).
"""
from __future__ import annotations

import ast
import subprocess
import sys


class _Mutator(ast.NodeTransformer):
    """Apply exactly the `target`-th eligible mutation; count eligible sites in `n`."""

    _CMP = {ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.LtE: ast.Gt, ast.Gt: ast.LtE,
            ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
            ast.In: ast.NotIn, ast.NotIn: ast.In}
    _BOOL = {ast.And: ast.Or, ast.Or: ast.And}

    def __init__(self, target: int) -> None:
        self.target = target
        self.n = 0

    def _hit(self) -> bool:
        hit = self.n == self.target
        self.n += 1
        return hit

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        if len(node.ops) == 1 and type(node.ops[0]) in self._CMP and self._hit():
            node.ops = [self._CMP[type(node.ops[0])]()]
        return node

    def visit_BoolOp(self, node: ast.BoolOp):
        self.generic_visit(node)
        if self._hit():
            node.op = self._BOOL[type(node.op)]()
        return node

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, bool) and self._hit():
            node.value = not node.value
        return node


def _count(src: str) -> int:
    m = _Mutator(-1)
    m.visit(ast.parse(src))
    return m.n


def _mutate(src: str, i: int) -> str:
    m = _Mutator(i)
    tree = m.visit(ast.parse(src))
    return ast.unparse(ast.fix_missing_locations(tree))


def main() -> int:
    path, test_cmd = sys.argv[1], sys.argv[sys.argv.index("--") + 1:]
    original = open(path).read()
    total = _count(original)
    print(f"{path}: {total} mutation sites; kill-signal: {' '.join(test_cmd)}\n")
    # Verify the baseline is GREEN before mutating. A failing kill-signal on unmutated
    # code makes EVERY mutant look KILLED (any nonzero exit reads as a kill), so a broken
    # test silently turns the meta-oracle into a rubber stamp (panel R34 caught exactly
    # this). Abort loudly instead of reporting a false all-killed.
    baseline = subprocess.run(test_cmd, capture_output=True)
    if baseline.returncode != 0:
        print("BASELINE NOT GREEN — the kill-signal fails on unmutated code; fix the "
              "tests first (a red baseline reports every mutant as falsely KILLED).")
        sys.stderr.write(baseline.stdout.decode("utf-8", "replace")[-2000:])
        return 2
    survived = []
    try:
        for i in range(total):
            mutated = _mutate(original, i)
            if mutated == ast.unparse(ast.parse(original)):
                continue  # no-op mutation
            open(path, "w").write(mutated)
            r = subprocess.run(test_cmd, capture_output=True)
            status = "KILLED " if r.returncode != 0 else "SURVIVED"
            if r.returncode == 0:
                survived.append(i)
            print(f"  [{i + 1}/{total}] {status}")
    finally:
        open(path, "w").write(original)  # always restore
    print()
    if survived:
        print(f"{len(survived)} SURVIVED mutant(s) — un-pinned behaviour (add a test or justify):")
        for i in survived:
            diff = _diff(original, i)
            print(f"  mutant {i}: {diff}")
        return 1
    print(f"All {total} mutants KILLED — the suite pins every mutated site.")
    return 0


def _diff(src: str, i: int) -> str:
    """One-line before/after for the i-th mutation (best-effort line locate)."""
    import difflib
    a = ast.unparse(ast.parse(src)).splitlines()
    b = _mutate(src, i).splitlines()
    for line in difflib.unified_diff(a, b, n=0, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            return line[1:].strip()
    return "(unlocated)"


if __name__ == "__main__":
    raise SystemExit(main())
