"""Completeness oracle for the Java body fingerprint (v3.6.0) — the white-box guard, ported.

Same method as the Python / JS / Go / Rust / C-C++ oracles, per `docs/BODY_MATRIX_LESSONS.md`: for
every value-bearing Java construct, two source variants differing ONLY by an inner `helper()` (a CALL
node) vs `0` (a CONST node) MUST produce different fingerprints. If the construct is silently dropped
by `core/structure_java._build_vfg`, the two collapse to the same fingerprint (similarity 1.0) and the
oracle fails — catching the whole "dropped node type" class deterministically.

Note on the introspective guard: tree-sitter has no small stmt/expr supertype set, so the Python
oracle's `ast.__subclasses__()` enumeration does not port. The **generic fallback** in
`structure_java._build_vfg` is the structural "nothing silently vanishes" guarantee; this metamorphic
battery is the behavioral check that the value-bearing positions are actually walked.

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_java as sj  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _wrap(body: str) -> str:
    return "class T { int t(int a){ " + body + " } }"


def _sim(src_a: str, src_b: str, name: str = "T.t") -> float:
    a = sj.fingerprint_source(src_a)
    b = sj.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    return similarity(a[name], b[name])


_STMT: dict[str, str] = {
    "Return": "return {probe};",
    "Decl": "int x = {probe}; return x;",
    "Decl-multi": "int x = {probe}, y = 1; return x + y;",
    "Assign": "int x = 0; x = {probe}; return x;",
    "CompoundAssign": "int x = 0; x += {probe}; return x;",
    "ShiftAssign": "int x = 0; x <<= {probe}; return x;",
    "ExprStmt": "sink({probe}); return 0;",
    "If-cond": "if ({probe} > 0) { return 1; } return 0;",
    "If-body": "if (a > 0) { return {probe}; } return 0;",
    "Else-body": "if (a > 0) { return 1; } else { return {probe}; }",
    "For-init": "for (int i = {probe}; i < 1; i++) {} return 0;",
    "For-cond": "for (int i = 0; {probe} > i; i++) {} return 0;",
    "For-update": "for (int i = 0; i < 1; i = ({probe})) {} return 0;",
    "For-body": "for (int i = 0; i < 1; i++) { sink({probe}); } return 0;",
    "EnhancedFor-iter": "for (int x : ({probe})) { sink(x); } return 0;",
    "EnhancedFor-body": "for (int x : items) { sink({probe}); } return 0;",
    "While-cond": "while ({probe} > 0) { break; } return 0;",
    "While-body": "while (a > 0) { sink({probe}); break; } return 0;",
    "DoWhile-body": "do { sink({probe}); } while (a > 0); return 0;",
    "Switch-disc": "switch ({probe}) { default: return 0; }",
    "Switch-case": "switch (a) { case 1: return {probe}; default: return 0; }",
    "SwitchArrow-body": "return switch (a) { case 1 -> {probe}; default -> 0; };",
    "Throw": "if (a > 0) throw new E({probe}); return 0;",
    "Synchronized": "synchronized (lock) { sink({probe}); } return 0;",
    "Labeled": "outer: { sink({probe}); } return 0;",
    "TryResource": "try (var r = open({probe})) { sink(r); } catch (Exception e) {} return 0;",
    "TryCatch-body": "try { sink({probe}); } catch (Exception e) {} return 0;",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_wrap(_STMT[label].replace("{probe}", "helper()")),
               _wrap(_STMT[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_java._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "return f({probe});",
    "Call-recv": "return ({probe}).foo(a);",
    "Field": "return ({probe}).x;",
    "Index-arr": "return ({probe})[0];",
    "Index-idx": "return arr[{probe}];",
    "Binary-left": "return {probe} + 1;",
    "Binary-right": "return a + {probe};",
    "Unary": "return -{probe};",
    "Update": "int x = {probe}; x++; return x;",
    "Ternary-cond": "return {probe} > 0 ? 1 : 2;",
    "Ternary-then": "return a > 0 ? {probe} : 2;",
    "Ternary-else": "return a > 0 ? 1 : {probe};",
    "Cast": "return (long)({probe});",
    "InstanceOf": "return ({probe}) instanceof String ? 1 : 0;",
    "New-arg": "return new S({probe});",
    "NewArray-size": "int[] z = new int[{probe}]; return z[0];",
    "ArrayInit": "int[] z = { {probe}, 1 }; return z[0];",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_wrap(_EXPR[label].replace("{probe}", "helper()")),
               _wrap(_EXPR[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_compound_assignment_rebinds_like_explicit():
    aug = sj.fingerprint_source(
        "class T { int f(int x, int e){ int z = x; z += e; z += e; return z; } }")["T.f"]
    explicit = sj.fingerprint_source(
        "class T { int f(int x, int e){ int z = x; z = z + e; z = z + e; return z; } }")["T.f"]
    assert similarity(aug, explicit) >= 0.99


def test_cast_carries_operand_not_type():
    cast = sj.fingerprint_source("class T { long t(int a){ return (long)(use(a)); } }")["T.t"]
    plain = sj.fingerprint_source("class T { long t(int a){ return use(a); } }")["T.t"]
    assert similarity(cast, plain) >= 0.99


def test_constructor_is_keyed_and_carries_flow():
    fps = sj.fingerprint_source("class C { int f; C(int z){ this.f = compute(z); } }")
    assert "C.C" in fps, "constructor not keyed as C.C"
    uses = fps["C.C"]
    ignores = sj.fingerprint_source("class C { int f; C(int z){ this.f = 0; } }")["C.C"]
    assert similarity(uses, ignores) < 1.0


def test_nested_class_method_keyed_dotted():
    fps = sj.fingerprint_source("class Outer { class Inner { int m(int z){ return z; } } }")
    assert "Outer.Inner.m" in fps and "Inner.m" not in fps


def test_interface_default_method_keyed():
    fps = sj.fingerprint_source("interface S { default int area(int w){ return w * w; } }")
    assert "S.area" in fps


def test_nested_lambda_is_opaque():
    # A lambda nested in a method body is one NESTED leaf, not its body. Two methods differing only
    # inside a lambda body fingerprint the same.
    a = sj.fingerprint_source("class T { void t(int a){ Runnable g = () -> foo(); use(g); } }")["T.t"]
    b = sj.fingerprint_source("class T { void t(int a){ Runnable g = () -> bar(); use(g); } }")["T.t"]
    assert similarity(a, b) >= 0.99
