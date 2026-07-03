"""Completeness oracle for the Rust body fingerprint (v3.4.0) — the white-box guard, ported.

Same method as the Python / JS / Go oracles, per `docs/BODY_MATRIX_LESSONS.md`: for every
value-bearing Rust construct, two source variants differing ONLY by an inner `helper()` (a CALL node)
vs `0` (a CONST node) MUST produce different fingerprints. If the construct is silently dropped by
`core/structure_rust._build_vfg`, the two collapse to the same fingerprint (similarity 1.0) and the
oracle fails — catching the whole "dropped node type" class deterministically.

Note on the introspective guard: tree-sitter has no small stmt/expr supertype set, so the Python
oracle's `ast.__subclasses__()` enumeration does not port. The **generic fallback** in
`structure_rust._build_vfg` (an unhandled node still descends into its sub-expressions) is the
structural "nothing silently vanishes" guarantee, and this metamorphic battery is the behavioral
check that the value-bearing positions are actually walked.

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_rust as sr  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = sr.fingerprint_source(src_a)
    b = sr.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    if a[name] == b[name]:
        return 1.0  # identical fingerprints == construct dropped; avoid the cosine float-rounding
        #             blind spot: self-cosine of a large WL vector rounds to 0.999...98 < 1.0
    return similarity(a[name], b[name])


# {probe} sits in a value-bearing position of one statement/control construct.
_STMT: dict[str, str] = {
    "TrailingExpr": "fn t(a: i32) -> i32 { {probe} }",
    "Return": "fn t(a: i32) -> i32 { return {probe}; }",
    "Let": "fn t(a: i32) -> i32 { let x = {probe}; x }",
    # function-local `const`/`static` item initializer is evaluated in the body (e.g. a `const fn`
    # call) — its value flow must be walked, not skipped as an opaque item (v3.7.0 panel).
    "ConstItem": "fn t(a: i32) -> i32 { const X: i32 = {probe}; X }",
    "StaticItem": "fn t(a: i32) -> i32 { static X: i32 = {probe}; X }",
    "LetTuple": "fn t(a: i32) -> i32 { let (x, y) = ({probe}, 1); x + y }",
    "Assign": "fn t(a: i32) -> i32 { let mut x = 0; x = {probe}; x }",
    "CompoundAssign": "fn t(a: i32) -> i32 { let mut x = 0; x += {probe}; x }",
    "ExprStmt": "fn t(a: i32) { sink({probe}); }",
    "If-cond": "fn t(a: i32) -> i32 { if {probe} > 0 { 1 } else { 0 } }",
    "If-then": "fn t(a: i32) -> i32 { if a > 0 { {probe} } else { 0 } }",
    "If-else": "fn t(a: i32) -> i32 { if a > 0 { 1 } else { {probe} } }",
    "Match-disc": "fn t(a: i32) -> i32 { match {probe} { _ => 0 } }",
    "Match-arm": "fn t(a: i32) -> i32 { match a { 1 => {probe}, _ => 0 } }",
    "Match-guard": "fn t(a: i32) -> i32 { match a { n if {probe} > 0 => 1, _ => 0 } }",
    "For-iter": "fn t(a: i32) { for x in {probe} { sink(x); } }",
    "For-body": "fn t(a: Vec<i32>) { for x in a { sink({probe}); } }",
    "While-cond": "fn t(a: i32) { while {probe} > 0 { break; } }",
    "While-body": "fn t(a: i32) { while a > 0 { sink({probe}); break; } }",
    "Loop-body": "fn t(a: i32) { loop { sink({probe}); break; } }",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_STMT[label].replace("{probe}", "helper()"),
               _STMT[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_rust._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "fn t(a: i32) -> i32 { f({probe}) }",
    "Call-callee": "fn t(a: i32) -> i32 { ({probe})(a) }",
    "Method-receiver": "fn t(a: i32) -> i32 { ({probe}).foo() }",
    "Method-arg": "fn t(a: i32) -> i32 { a.foo({probe}) }",
    "Field": "fn t(a: i32) -> i32 { ({probe}).field }",
    "Index-operand": "fn t(a: i32) -> i32 { ({probe})[0] }",
    "Index-index": "fn t(a: &[i32]) -> i32 { a[{probe}] }",
    "Binary-left": "fn t(a: i32) -> i32 { {probe} + 1 }",
    "Binary-right": "fn t(a: i32) -> i32 { a + {probe} }",
    "Unary": "fn t(a: i32) -> i32 { -{probe} }",
    "Reference": "fn t(a: i32) -> i32 { *(&{probe}) }",
    "Try": "fn t(a: R) -> i32 { ({probe})? }",
    "Cast": "fn t(a: i32) -> i64 { ({probe}) as i64 }",
    "Range": "fn t(a: i32) { for i in {probe}..10 { sink(i); } }",
    "Tuple": "fn t(a: i32) -> (i32, i32) { ({probe}, 1) }",
    "Array": "fn t(a: i32) -> [i32; 2] { [{probe}, 1] }",
    "Struct-field": "fn t(a: i32) -> S { S { f: {probe} } }",
    "Macro-arg": "fn t(a: i32) { println!(\"{}\", {probe}); }",
    "Vec-macro": "fn t(a: i32) -> Vec<i32> { vec![{probe}, 1] }",
    "Await": "async fn t(a: F) -> i32 { ({probe}).await }",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_EXPR[label].replace("{probe}", "helper()"),
               _EXPR[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_trailing_expression_equals_explicit_return():
    # Rust is expression-oriented: a block's trailing expression IS its value, so `{ x }` must
    # fingerprint like `{ return x; }`. This is the load-bearing Rust-specific invariant.
    tail = sr.fingerprint_source("fn f(a: i32, b: i32) -> i32 { a + b }")["f"]
    explicit = sr.fingerprint_source("fn f(a: i32, b: i32) -> i32 { return a + b; }")["f"]
    assert similarity(tail, explicit) >= 0.99


def test_compound_assignment_rebinds_like_explicit():
    # `x += e` must rebind x to the result and use the base operator, threading across statements
    # exactly like `x = x + e` — the same invariance the Python/JS/Go layers guarantee.
    aug = sr.fingerprint_source(
        "fn f(x: i32, e: i32) -> i32 { let mut z = x; z += e; z += e; z }")["f"]
    explicit = sr.fingerprint_source(
        "fn f(x: i32, e: i32) -> i32 { let mut z = x; z = z + e; z = z + e; z }")["f"]
    assert similarity(aug, explicit) >= 0.99


def test_cast_carries_operand_not_type():
    # `x as T` is value flow from x, not from the target type: a cast must fingerprint like the bare
    # operand (the type carries no flow, but the operand must NOT be lost).
    cast = sr.fingerprint_source("fn t(a: i32) -> i64 { use_it(a) as i64 }")["t"]
    plain = sr.fingerprint_source("fn t(a: i32) -> i64 { use_it(a) }")["t"]
    assert similarity(cast, plain) >= 0.99


def test_method_receiver_self_is_seeded_like_a_parameter():
    # `fn m(&self, …)` — `self` is in scope like a parameter; a method using self must carry value
    # flow that one ignoring it does not.
    uses = sr.fingerprint_source("impl T { fn m(&self) -> i32 { self.x } }")["T.m"]
    ignores = sr.fingerprint_source("impl T { fn m(&self) -> i32 { 0 } }")["T.m"]
    assert similarity(uses, ignores) < 1.0


def test_nested_closure_is_opaque():
    # A nested closure is one NESTED leaf, not its body (matching the other frontends and the Rust
    # extractor). Two functions differing only INSIDE a closure body fingerprint the same.
    a = sr.fingerprint_source("fn t(a: i32) -> i32 { let c = || foo(); c() }")["t"]
    b = sr.fingerprint_source("fn t(a: i32) -> i32 { let c = || bar(); c() }")["t"]
    assert similarity(a, b) >= 0.99
