"""Completeness oracle for the Go body fingerprint (v3.3.0) — the white-box guard, ported.

Same method as the Python and JS oracles (`test_structure_completeness.py` /
`test_structure_js_completeness.py`), per `docs/BODY_MATRIX_LESSONS.md`: for every value-bearing Go
construct, two source variants differing ONLY by an inner `helper()` (a CALL node) vs `0` (a CONST
node) MUST produce different fingerprints. If the construct is silently dropped by
`core/structure_go._build_vfg`, the two collapse to the same fingerprint (similarity 1.0) and the
oracle fails — catching the whole "dropped node type" class deterministically instead of one
adversarial-review finding at a time.

Note on the introspective guard: the Python oracle also enumerates `ast.{stmt,expr}.__subclasses__()`
and asserts each is classified. tree-sitter has no clean equivalent — hundreds of named node kinds in
the Go grammar, no small stmt/expr supertype set — so that guard does not port. Here the **generic
fallback** in `structure_go._build_vfg` (an unhandled node still descends into its sub-expressions)
is the structural "nothing silently vanishes" guarantee, and this metamorphic battery is the
behavioral check that the value-bearing positions are actually walked.

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_go as sg  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = sg.fingerprint_source(src_a)
    b = sg.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    return similarity(a[name], b[name])


# {probe} sits in a value-bearing position of one statement construct. helper() (CALL) vs 0 (CONST)
# must change the fingerprint iff the construct is walked.
_STMT: dict[str, str] = {
    "Return": "package m\nfunc t(a int) int { return {probe} }",
    "Return-multi": "package m\nfunc t(a int) (int, int) { return {probe}, 1 }",
    "ShortVarDecl": "package m\nfunc t(a int) int { x := {probe}; return x }",
    "VarDecl": "package m\nfunc t(a int) int { var x = {probe}; return x }",
    "Assign": "package m\nfunc t(a int) int { x := 0; x = {probe}; return x }",
    "AugAssign": "package m\nfunc t(a int) int { x := 0; x += {probe}; return x }",
    "If-init": "package m\nfunc t(a int) int { if x := {probe}; x > 0 { return 1 }; return 0 }",
    "If-cond": "package m\nfunc t(a int) int { if {probe} > 0 { return 1 }; return 0 }",
    "If-body": "package m\nfunc t(a int) int { if a > 0 { return {probe} }; return 0 }",
    "Else-body": "package m\nfunc t(a int) int { if a > 0 { return 1 } else { return {probe} }; return 0 }",
    "For-init": "package m\nfunc t(a int) { for i := {probe}; i < 1; i++ { sink(i) } }",
    "For-cond": "package m\nfunc t(a int) { for i := 0; {probe} > i; i++ { sink(i) } }",
    "For-post": "package m\nfunc t(a int) { for i := 0; i < 1; i = {probe} { sink(i) } }",
    "For-body": "package m\nfunc t(a int) { for i := 0; i < 1; i++ { sink({probe}) } }",
    "For-cond-only": "package m\nfunc t(a int) { for {probe} > 0 { break } }",
    "Range-iter": "package m\nfunc t(a int) { for k := range {probe} { sink(k) } }",
    "Range-body": "package m\nfunc t(a []int) { for _, v := range a { sink({probe}); _ = v } }",
    "Switch-disc": "package m\nfunc t(a int) int { switch {probe} { default: return 0 } }",
    "Switch-case-value": "package m\nfunc t(a int) int { switch a { case {probe}: return 1; default: return 0 } }",
    "Switch-case-body": "package m\nfunc t(a int) int { switch a { case 1: return {probe}; default: return 0 } }",
    "Send-channel": "package m\nfunc t(a int) { ({probe}) <- 1 }",
    "Send-value": "package m\nfunc t(ch chan int) { ch <- {probe} }",
    "Go-call": "package m\nfunc t(a int) { go sink({probe}) }",
    "Defer-call": "package m\nfunc t(a int) { defer sink({probe}) }",
    "ExprStmt": "package m\nfunc t(a int) { sink({probe}) }",
    "Labeled": "package m\nfunc t(a int) { L: sink({probe}) }",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_STMT[label].replace("{probe}", "helper()"),
               _STMT[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_go._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "package m\nfunc t(a int) int { return f({probe}) }",
    "Call-callee": "package m\nfunc t(a int) int { return ({probe})(a) }",
    "Variadic-arg": "package m\nfunc t(a []int) int { return f({probe}...) }",
    "Selector-operand": "package m\nfunc t(a int) int { return ({probe}).F }",
    "Index-operand": "package m\nfunc t(a int) int { return ({probe})[0] }",
    "Index-index": "package m\nfunc t(a []int) int { return a[{probe}] }",
    "Slice-operand": "package m\nfunc t(a int) []int { return ({probe})[1:2] }",
    "Slice-start": "package m\nfunc t(a []int) []int { return a[{probe}:2] }",
    "Slice-end": "package m\nfunc t(a []int) []int { return a[1:{probe}] }",
    "Binary-left": "package m\nfunc t(a int) int { return {probe} + 1 }",
    "Binary-right": "package m\nfunc t(a int) int { return a + {probe} }",
    "Unary": "package m\nfunc t(a int) int { return -{probe} }",
    "Deref": "package m\nfunc t(a *int) int { return *({probe}) }",
    "Receive": "package m\nfunc t(ch chan int) int { return <-({probe}) }",
    "Composite-elem": "package m\nfunc t(a int) []int { return []int{ {probe} } }",
    "Composite-map-key": "package m\nfunc t(a int) map[int]int { return map[int]int{ {probe}: 1 } }",
    "Composite-map-value": "package m\nfunc t(a int) map[int]int { return map[int]int{ 1: {probe} } }",
    "TypeAssert-operand": "package m\nfunc t(a interface{}) interface{} { return ({probe}).(int) }",
    "Convert-operand": "package m\nfunc t(a int) int64 { return int64({probe}) }",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_EXPR[label].replace("{probe}", "helper()"),
               _EXPR[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_augmented_assignment_rebinds_like_explicit():
    # `x += e` must rebind x to the result and use the base operator, so it threads across statements
    # exactly like `x = x + e` — the same invariance the Python and JS layers guarantee.
    aug = sg.fingerprint_source(
        "package m\nfunc f(x, e int) int { z := x; z += e; z += e; return z }")["f"]
    explicit = sg.fingerprint_source(
        "package m\nfunc f(x, e int) int { z := x; z = z + e; z = z + e; return z }")["f"]
    assert similarity(aug, explicit) >= 0.99


def test_type_assertion_carries_no_value_flow():
    # `x.(T)` is value flow from x, not from the asserted type: a type-asserted expression must
    # fingerprint like the bare operand (the type carries no flow, but the operand must NOT be lost).
    asserted = sg.fingerprint_source(
        "package m\nfunc t(a interface{}) interface{} { return use(a).(int) }")["t"]
    plain = sg.fingerprint_source(
        "package m\nfunc t(a interface{}) interface{} { return use(a) }")["t"]
    assert similarity(asserted, plain) >= 0.99


def test_switch_case_value_not_double_walked():
    # A compound case value (`case g():`) must be walked ONCE. The byte-span skip in _do_case_body
    # prevents re-walking the case value as a body statement (which would add spurious nodes — the
    # JS R173 lesson, pre-empted here).
    fp = sg.fingerprint_source(
        "package m\nfunc t(a int) int { switch a { case g(): return 1; default: return 0 } }")["t"]
    # the case value g() is a single CALL fed into the CASE; if the byte-span skip failed it would be
    # re-walked as a body statement and 0:CALL would be 2.
    assert fp.get("0:CALL", 0) == 1


def test_method_receiver_is_seeded_like_a_parameter():
    # `func (r *T) M()` — the receiver r is in scope like a parameter; using it must carry value flow
    # (a receiver-using method differs from one that ignores it).
    uses = sg.fingerprint_source("package m\nfunc (r *T) M() int { return r.x }")["M"]
    ignores = sg.fingerprint_source("package m\nfunc (r *T) M() int { return 0 }")["M"]
    assert similarity(uses, ignores) < 1.0


def test_nested_func_literal_is_opaque():
    # A nested closure is one NESTED leaf, not its body (matching Python lambdas / JS nested fns and
    # the Go extractor, which does not mint closures as nodes). Two functions whose only difference is
    # *inside* a closure body fingerprint the same.
    a = sg.fingerprint_source("package m\nfunc t() func() int { return func() int { return foo() } }")["t"]
    b = sg.fingerprint_source("package m\nfunc t() func() int { return func() int { return bar() } }")["t"]
    assert similarity(a, b) >= 0.99
