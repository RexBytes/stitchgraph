"""Completeness oracle for the JS/TS body fingerprint (v3.2.0) — the white-box guard, ported.

Same method as the Python oracle (`test_structure_completeness.py`), per `docs/BODY_MATRIX_LESSONS.md`:
for every value-bearing JS construct, two source variants differing ONLY by an inner `helper()` (a
CALL node) vs `0` (a CONST node) MUST produce different fingerprints. If the construct is silently
dropped by `core/structure_js._build_vfg`, the two collapse to the same fingerprint (similarity 1.0)
and the oracle fails — catching the whole "dropped node type" class deterministically instead of one
adversarial-review finding at a time.

Note on the introspective guard: the Python oracle also enumerates `ast.{stmt,expr}.__subclasses__()`
and asserts each is classified. tree-sitter has no clean equivalent — hundreds of named node kinds
across the js/ts/tsx grammars, no small stmt/expr supertype set — so that guard does not port. Here
the **generic fallback** in `structure_js._build_vfg` (an unhandled node still descends into its
sub-expressions) is the structural "nothing silently vanishes" guarantee, and this metamorphic
battery is the behavioral check that the value-bearing positions are actually walked.

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_js as sj  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = sj.fingerprint_source(src_a)
    b = sj.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    if a[name] == b[name]:
        return 1.0  # identical fingerprints == construct dropped; avoid the cosine float-rounding
        #             blind spot: self-cosine of a large WL vector rounds to 0.999...98 < 1.0
    return similarity(a[name], b[name])


# {probe} sits in a value-bearing position of one statement construct. helper() (CALL) vs 0 (CONST)
# must change the fingerprint iff the construct is walked.
_STMT: dict[str, str] = {
    "Return": "function t(a){ return {probe}; }",
    "VarDecl-use": "function t(a){ let x = {probe}; return x; }",
    "Assign": "function t(a){ let x; x = {probe}; return x; }",
    "AugAssign": "function t(a){ let x = 0; x += {probe}; return x; }",
    "If-test": "function t(a){ if ({probe}) return 1; return 0; }",
    "If-body": "function t(a){ if (a) return {probe}; return 0; }",
    "Else-body": "function t(a){ if (a) return 1; else return {probe}; }",
    "For-init": "function t(a){ for (let i = {probe}; i < 1; i++){} }",
    "For-cond": "function t(a){ for (let i = 0; {probe}; i++){} }",
    "For-body": "function t(a){ for (let i = 0; i < 1; i++){ return {probe}; } }",
    "ForOf-iter": "function t(a){ for (const x of {probe}){} }",
    "ForOf-body": "function t(a){ for (const x of a){ return {probe}; } }",
    "ForIn-iter": "function t(a){ for (const k in {probe}){} }",
    "While-test": "function t(a){ while ({probe}) break; }",
    "While-body": "function t(a){ while (a){ return {probe}; } }",
    "DoWhile-body": "function t(a){ do { return {probe}; } while (a); }",
    "Switch-disc": "function t(a){ switch ({probe}){ default: return 0; } }",
    "Switch-case": "function t(a){ switch (a){ case 1: return {probe}; } }",
    "Switch-case-value": "function t(a){ switch (a){ case {probe}: return 1; default: return 0; } }",
    "Try-body": "function t(a){ try { return {probe}; } catch (e) {} }",
    "Catch-body": "function t(a){ try {} catch (e) { return {probe}; } }",
    "Finally-body": "function t(a){ try {} finally { return {probe}; } }",
    "Throw": "function t(a){ throw {probe}; }",
    "ExprStmt": "function t(a){ sink({probe}); }",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_STMT[label].replace("{probe}", "helper()"),
               _STMT[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_js._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "function t(a){ return f({probe}); }",
    "Call-callee": "function t(a){ return ({probe})(); }",
    "OptionalCall-arg": "function t(a){ return f?.({probe}); }",
    "Member-object": "function t(a){ return ({probe}).attr; }",
    "Subscript-object": "function t(a){ return ({probe})[0]; }",
    "Subscript-index": "function t(a){ return a[{probe}]; }",
    "Binary-left": "function t(a){ return {probe} + 1; }",
    "Binary-right": "function t(a){ return a + {probe}; }",
    "Logical": "function t(a){ return a && {probe}; }",
    "Unary": "function t(a){ return -{probe}; }",
    "Update": "function t(a){ let x = {probe}; x++; return x; }",
    "Ternary-cond": "function t(a){ return {probe} ? 1 : 2; }",
    "Ternary-then": "function t(a){ return a ? {probe} : 2; }",
    "Ternary-else": "function t(a){ return a ? 1 : {probe}; }",
    "Array-elt": "function t(a){ return [{probe}]; }",
    "Object-value": "function t(a){ return { k: {probe} }; }",
    "Object-computed-key": "function t(a){ return { [{probe}]: 1 }; }",
    "Spread": "function t(a){ return [...{probe}]; }",
    "New-arg": "function t(a){ return new C({probe}); }",
    "New-callee": "function t(a){ return new ({probe})(); }",
    "Sequence": "function t(a){ return ({probe}, 1); }",
    "Await": "async function t(a){ return await {probe}; }",
    "Yield": "function* t(a){ yield {probe}; }",
    "TemplateSub": "function t(a){ return `x${  {probe}  }y`; }",
}


# TS-only cast forms (parse under the TypeScript grammar, not JS). {probe} sits in the cast OPERAND
# — the value-bearing position — so helper() vs 0 must change the fingerprint. Pins R174 (opus): the
# `as`/`satisfies` operand is the FIRST child (`operand <kw> type`), so descending to the last child
# would keep the no-flow type and DROP the operand. `<T>x` is operand-LAST (regression guard).
_TS_CAST: dict[str, str] = {
    "AsCast-operand": "function t(a){ return ({probe}) as number; }",
    "SatisfiesCast-operand": "function t(a){ return ({probe}) satisfies T; }",
    "TypeAssertion-operand": "function t(a){ return <number>({probe}); }",
}


@pytest.mark.parametrize("label", sorted(_TS_CAST))
def test_ts_cast_operand_is_walked(label):
    src = _TS_CAST[label]
    a = sj.fingerprint_source(src.replace("{probe}", "helper()"), lang="typescript")
    b = sj.fingerprint_source(src.replace("{probe}", "0"), lang="typescript")
    assert "t" in a and "t" in b, f"{label}: TS source did not parse a function 't'"
    sim = similarity(a["t"], b["t"])
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this cast operand produced identical fingerprints "
        f"(sim={sim}) — the cast is dropping its operand and keeping the type node")


def test_ts_cast_carries_no_value_flow():
    # `x as T` / `x satisfies T` must fingerprint exactly like the bare `x` (TS ≡ JS): the cast adds
    # no value flow, but it must NOT remove the operand's flow either.
    js = sj.fingerprint_source("function t(a){ return use(a); }")["t"]
    for cast in ("use(a) as number", "use(a) satisfies T", "<number>use(a)"):
        ts = sj.fingerprint_source(f"function t(a){{ return {cast}; }}", lang="typescript")["t"]
        assert similarity(ts, js) >= 0.99, f"{cast!r} did not fingerprint like its untyped twin"


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_EXPR[label].replace("{probe}", "helper()"),
               _EXPR[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_augmented_assignment_rebinds_like_explicit():
    # R172 (opus): `x += e` must rebind x to the result and use the base operator, so it threads
    # across statements exactly like `x = x + e` — the same invariance the Python layer guarantees.
    # The metamorphic battery only checks the RHS is walked, not the cross-statement write-back.
    aug = sj.fingerprint_source("function f(x, e){ let z = x; z += e; z += e; return z; }")["f"]
    explicit = sj.fingerprint_source("function f(x, e){ let z = x; z = z + e; z = z + e; return z; }")["f"]
    assert similarity(aug, explicit) >= 0.99


def test_switch_compound_case_value_not_double_walked():
    # R173 (sonnet): a compound case value (`case g():`) must be walked ONCE. The old `st is val`
    # skip was a no-op (tree-sitter returns a fresh wrapper per call), so the value was re-walked as
    # a body statement, adding spurious nodes. Pin by byte-span skip: no stray ARGUMENTS / property
    # node appears.
    call = sj.fingerprint_source("function f(x){ switch (x){ case g(): return 1; } }")["f"]
    assert "0:ARGUMENTS" not in call
    member = sj.fingerprint_source("function f(x){ switch (x){ case a.b: return 1; } }")["f"]
    assert "0:PROPERTY_IDENTIFIER" not in member


def test_typescript_annotations_carry_no_value_flow():
    # TS type annotations are not value flow: a typed function must fingerprint like its untyped JS
    # twin. Pins that the TS-specific wrapper nodes are seen through, not treated as operations.
    ts = sj.fingerprint_source("function t(a: number, b: Box<string>): number { return use(a, b); }",
                               lang="typescript")["t"]
    js = sj.fingerprint_source("function t(a, b){ return use(a, b); }")["t"]
    assert similarity(ts, js) >= 0.99
