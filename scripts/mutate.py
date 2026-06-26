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

Use --only to restrict mutation to specific functions/classes (by name; a match on any
enclosing def/class scope counts), so a big file's correctness-critical core can be pinned
by a FAST, targeted kill-signal without survivors in unrelated functions the signal doesn't
exercise. This is how v2's streaming code is gated:

    python scripts/mutate.py src/stitchgraph/core/operations.py \
        --only _dedup_edges,_StoreEdgeSink,_reindex_streaming,_auto_stream \
        -- python -m pytest -x -q tests/oracles/test_streaming_differential.py \
           tests/oracles/test_incremental_differential.py tests/test_streaming_reindex.py

Exit non-zero if any mutant SURVIVED (so CI/nightly can gate on it).
"""
from __future__ import annotations

import ast
import subprocess
import sys


class _Mutator(ast.NodeTransformer):
    """Apply exactly the `target`-th eligible mutation; count eligible sites in `n`.

    `only` (optional set of names) restricts eligibility to sites lexically inside a
    function/class whose name is in the set — so the index space is identical between the
    count pass and the mutate pass (both skip out-of-scope sites without counting them)."""

    _CMP = {ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.LtE: ast.Gt, ast.Gt: ast.LtE,
            ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
            ast.In: ast.NotIn, ast.NotIn: ast.In}
    _BOOL = {ast.And: ast.Or, ast.Or: ast.And}

    def __init__(self, target: int, only: set[str] | None = None) -> None:
        self.target = target
        self.only = only
        self.scope: list[str] = []
        self.n = 0

    def _in_scope(self) -> bool:
        return self.only is None or bool(self.only & set(self.scope))

    def _hit(self) -> bool:
        if not self._in_scope():
            return False  # out-of-scope: not an eligible site, do NOT advance the index
        hit = self.n == self.target
        self.n += 1
        return hit

    def _scoped(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        return node

    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped
    visit_ClassDef = _scoped

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


def _count(src: str, only: set[str] | None) -> int:
    m = _Mutator(-1, only)
    m.visit(ast.parse(src))
    return m.n


def _mutate(src: str, i: int, only: set[str] | None) -> str:
    m = _Mutator(i, only)
    tree = m.visit(ast.parse(src))
    return ast.unparse(ast.fix_missing_locations(tree))


def main() -> int:
    argv = sys.argv[1:]
    only: set[str] | None = None
    if "--only" in argv:
        oi = argv.index("--only")
        only = {n.strip() for n in argv[oi + 1].split(",") if n.strip()}
        del argv[oi:oi + 2]
    sys.argv = [sys.argv[0], *argv]
    path, test_cmd = sys.argv[1], sys.argv[sys.argv.index("--") + 1:]
    original = open(path).read()
    total = _count(original, only)
    scope = f" (only: {', '.join(sorted(only))})" if only else ""
    print(f"{path}: {total} mutation sites{scope}; kill-signal: {' '.join(test_cmd)}\n")
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
            mutated = _mutate(original, i, only)
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
            diff = _diff(original, i, only)
            print(f"  mutant {i}: {diff}")
        return 1
    print(f"All {total} mutants KILLED — the suite pins every mutated site.")
    return 0


def _diff(src: str, i: int, only: set[str] | None = None) -> str:
    """One-line before/after for the i-th mutation (best-effort line locate)."""
    import difflib
    a = ast.unparse(ast.parse(src)).splitlines()
    b = _mutate(src, i, only).splitlines()
    for line in difflib.unified_diff(a, b, n=0, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            return line[1:].strip()
    return "(unlocated)"


if __name__ == "__main__":
    raise SystemExit(main())
