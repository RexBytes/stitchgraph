"""Completeness oracle for the body fingerprint (v3.0.0) — the white-box guard for one bug class.

Three panel rounds each found the SAME class of defect: a Python statement type that carries
value-flow but was silently dropped by `core/structure.py:_build_vfg` (`except*` → control-flow
nested defs → `match`). Rather than rely on adversarial review to find them one at a time, this
oracle catches the whole class deterministically:

  1. Metamorphic battery — for every value-bearing statement type, two source variants differing
     ONLY by an inner `helper()` (a CALL node) vs `0` (a CONST node) MUST produce different
     fingerprints. If the statement is dropped, both variants collapse to the same fingerprint and
     similarity is 1.0 — the oracle fails. (Names are anonymised in the fingerprint, so the probe
     varies *structure*, not identifiers.)

  2. Introspective guard — every concrete `ast.stmt` subclass must be in our known set. When a
     future Python version adds a statement type, this fails and forces us to add coverage — so the
     class can't silently reopen. (At runtime the generic fallback in `_build_vfg.do` already keeps
     an unknown type from vanishing; this test makes the gap *visible*.)

Stdlib-only — runs in the core (no-extras) CI job.
"""
from __future__ import annotations

import ast

import pytest

from stitchgraph.core import structure


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = structure.fingerprint_source(src_a)[name]
    b = structure.fingerprint_source(src_b)[name]
    if a == b:
        return 1.0  # identical fingerprints == construct dropped; avoid the cosine float-rounding
        #             blind spot: self-cosine of a large WL vector rounds to 0.999...98 < 1.0
    return structure.similarity(a, b)


# Each template has a {probe} slot in a value-bearing position of one statement type. Filling it
# with `helper()` (CALL) vs `0` (CONST) must change the fingerprint iff the statement is walked.
_TEMPLATES: dict[str, str] = {
    "Expr": "def t(a):\n    {probe}\n",
    "Return": "def t(a):\n    return {probe}\n",
    "Assign": "def t(a):\n    x = {probe}\n    return x\n",
    "AugAssign": "def t(a):\n    x = 0\n    x += {probe}\n    return x\n",
    "AnnAssign": "def t(a):\n    x: int = {probe}\n    return x\n",
    "If-test": "def t(a):\n    if {probe}:\n        return 1\n    return 0\n",
    "If-body": "def t(a):\n    if a:\n        return {probe}\n    return 0\n",
    "For-iter": "def t(a):\n    for x in {probe}:\n        pass\n",
    "For-body": "def t(a):\n    for x in a:\n        return {probe}\n",
    "While-test": "def t(a):\n    while {probe}:\n        break\n",
    "With-ctx": "def t(a):\n    with {probe} as c:\n        return c\n",
    "Try-body": "def t(a):\n    try:\n        return {probe}\n    except Exception:\n        return 0\n",
    "Try-handler": "def t(a):\n    try:\n        pass\n    except Exception:\n        return {probe}\n",
    "TryStar": "def t(a):\n    try:\n        return {probe}\n    except* Exception:\n        pass\n",
    "Raise": "def t(a):\n    raise {probe}\n",
    "Assert": "def t(a):\n    assert {probe}\n",
    "Delete": "def t(a):\n    x = [1]\n    del x[{probe}]\n",
    "Match-subject": "def t(a):\n    match {probe}:\n        case _:\n            return 0\n",
    "Match-body": "def t(a):\n    match a:\n        case _:\n            return {probe}\n",
    "AsyncFor": "async def t(a):\n    async for x in {probe}:\n        pass\n",
    "AsyncWith": "async def t(a):\n    async with {probe} as c:\n        return c\n",
    "Await": "async def t(a):\n    return await {probe}\n",
}


@pytest.mark.parametrize("label", sorted(_TEMPLATES))
def test_value_bearing_statement_is_walked(label):
    tmpl = _TEMPLATES[label]
    sim = _sim(tmpl.format(probe="helper()"), tmpl.format(probe="0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the statement type is being dropped by _build_vfg")


def _concrete_stmt_types() -> set[str]:
    return {c.__name__ for c in ast.stmt.__subclasses__()}


# Every concrete statement type, classified. value_bearing = exercised by the battery above;
# no_value_flow = legitimately contributes nothing (no sub-expression / no body of interest).
_VALUE_BEARING = {
    "Expr", "Return", "Assign", "AugAssign", "AnnAssign", "If", "For", "AsyncFor", "While",
    "With", "AsyncWith", "Try", "TryStar", "Raise", "Assert", "Delete", "Match",
    "FunctionDef", "AsyncFunctionDef", "ClassDef",  # defs: bodies fingerprinted recursively
    "TypeAlias",  # 3.12+: aliased-type expr handled by the generic fallback
}
_NO_VALUE_FLOW = {"Pass", "Break", "Continue", "Global", "Nonlocal", "Import", "ImportFrom"}


def test_no_uncovered_statement_type():
    # If a future Python adds a statement type, force a conscious decision (cover it in the battery
    # or classify it no-value-flow) instead of letting it silently drop from the fingerprint.
    known = _VALUE_BEARING | _NO_VALUE_FLOW
    actual = _concrete_stmt_types()
    uncovered = actual - known
    assert not uncovered, (
        f"new ast.stmt type(s) not classified by the completeness oracle: {sorted(uncovered)} — "
        f"add to _VALUE_BEARING (+ a battery template) or _NO_VALUE_FLOW")


# --- expression-level completeness (the sibling class — where the Subscript-index bug lived) -----
# Same metamorphic idea one level down: a CALL vs a CONST in a sub-expression position must change
# the fingerprint, else that expression type is dropping a child (as ev(Subscript) once dropped the
# index, and ev(Dict) once dropped keys).
_EXPR_TEMPLATES: dict[str, str] = {
    "BoolOp": "def t(a):\n    return a and {probe}\n",
    "BinOp": "def t(a):\n    return a + {probe}\n",
    "UnaryOp": "def t(a):\n    return -{probe}\n",
    "Compare": "def t(a):\n    return a < {probe}\n",
    "Call-arg": "def t(a):\n    return f({probe})\n",
    "Call-func": "def t(a):\n    return ({probe})()\n",
    "Attribute": "def t(a):\n    return ({probe}).attr\n",
    "Subscript-value": "def t(a):\n    return ({probe})[0]\n",
    "Subscript-index": "def t(a):\n    return a[{probe}]\n",
    "Slice-lower": "def t(a):\n    return a[{probe}:1]\n",
    "List": "def t(a):\n    return [{probe}]\n",
    "Tuple": "def t(a):\n    return ({probe}, 1)\n",
    "Set": "def t(a):\n    return {{{probe}}}\n",
    "Dict-key": "def t(a):\n    return {{{probe}: 1}}\n",
    "Dict-value": "def t(a):\n    return {{1: {probe}}}\n",
    "ListComp-elt": "def t(a):\n    return [{probe} for x in a]\n",
    "ListComp-iter": "def t(a):\n    return [x for x in {probe}]\n",
    "ListComp-if": "def t(a):\n    return [x for x in a if {probe}]\n",
    "SetComp": "def t(a):\n    return {{{probe} for x in a}}\n",
    "DictComp-key": "def t(a):\n    return {{{probe}: 1 for x in a}}\n",
    "DictComp-value": "def t(a):\n    return {{x: {probe} for x in a}}\n",
    "GeneratorExp": "def t(a):\n    return sum({probe} for x in a)\n",
    "IfExp-body": "def t(a):\n    return {probe} if a else 0\n",
    "IfExp-test": "def t(a):\n    return 0 if {probe} else 1\n",
    "IfExp-orelse": "def t(a):\n    return 0 if a else {probe}\n",
    "Starred": "def t(a):\n    return [*{probe}]\n",
    "NamedExpr": "def t(a):\n    return (y := {probe})\n",
    "Await": "async def t(a):\n    return await {probe}\n",
    "Yield": "def t(a):\n    yield {probe}\n",
    "YieldFrom": "def t(a):\n    yield from {probe}\n",
    "FString": 'def t(a):\n    return f"{{{probe}}}"\n',
}


@pytest.mark.parametrize("label", sorted(_EXPR_TEMPLATES))
def test_value_bearing_expression_is_walked(label):
    tmpl = _EXPR_TEMPLATES[label]
    sim = _sim(tmpl.format(probe="helper()"), tmpl.format(probe="0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def _concrete_expr_types() -> set[str]:
    return {c.__name__ for c in ast.expr.__subclasses__()}


# Covered by the expr battery above; leaf = no sub-expression to lose; opaque = intentionally a
# black box (lambda bodies are NESTED, documented).
_EXPR_COVERED = {
    "BoolOp", "NamedExpr", "BinOp", "UnaryOp", "IfExp", "Dict", "Set", "ListComp", "SetComp",
    "DictComp", "GeneratorExp", "Await", "Yield", "YieldFrom", "Compare", "Call",
    "FormattedValue", "JoinedStr", "Attribute", "Subscript", "Starred", "List", "Tuple", "Slice",
}
_EXPR_LEAF = {"Constant", "Name"}
_EXPR_OPAQUE = {"Lambda"}


def test_no_uncovered_expression_type():
    known = _EXPR_COVERED | _EXPR_LEAF | _EXPR_OPAQUE
    uncovered = _concrete_expr_types() - known
    assert not uncovered, (
        f"new ast.expr type(s) not classified by the completeness oracle: {sorted(uncovered)} — "
        f"add to _EXPR_COVERED (+ a battery template), _EXPR_LEAF, or _EXPR_OPAQUE")


def test_lambda_body_is_opaque():
    # A lambda is an opaque NESTED leaf (the `_EXPR_OPAQUE` classification above, and matching every
    # tree-sitter frontend's closure handling): two functions differing only inside a lambda body must
    # fingerprint identically — the body must NOT leak into the enclosing function.
    a = structure.fingerprint_source("def f(xs):\n    return sorted(xs, key=lambda x: helper(x))")["f"]
    b = structure.fingerprint_source("def f(xs):\n    return sorted(xs, key=lambda x: other(x))")["f"]
    assert structure.similarity(a, b) >= 0.99
    # ...but a lambda's DEFAULT argument value is evaluated in the enclosing scope, so it carries flow.
    uses = structure.fingerprint_source("def g(e):\n    return (lambda a=helper(): a)")["g"]
    ignores = structure.fingerprint_source("def g(e):\n    return (lambda a=0: a)")["g"]
    assert structure.similarity(uses, ignores) < 1.0
