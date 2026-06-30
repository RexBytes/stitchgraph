"""Completeness oracle for the C# body fingerprint (v3.6.0) — the white-box guard, ported.

Same method as the Python / JS / Go / Rust / C-C++ / Java oracles, per `docs/BODY_MATRIX_LESSONS.md`:
for every value-bearing C# construct, two source variants differing ONLY by an inner `helper()` (a
CALL node) vs `0` (a CONST node) MUST produce different fingerprints. If the construct is silently
dropped by `core/structure_csharp._build_vfg`, the two collapse to the same fingerprint (similarity
1.0) and the oracle fails — catching the whole "dropped node type" class deterministically.

The **generic fallback** in `structure_csharp._build_vfg` is the structural "nothing silently vanishes"
guarantee; this metamorphic battery is the behavioral check that the value-bearing positions are
actually walked. Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_csharp as sc  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _wrap(body: str) -> str:
    return "class T { int t(int a){ " + body + " } }"


def _sim(src_a: str, src_b: str, name: str = "T.t") -> float:
    a = sc.fingerprint_source(src_a)
    b = sc.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    if a[name] == b[name]:
        return 1.0  # identical fingerprints == construct dropped; avoid the cosine float-rounding
        #             blind spot: self-cosine of a large WL vector rounds to 0.999...98 < 1.0
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
    "For-update2": "for (int i = 0; i < 1; i++, sink({probe})) {} return 0;",
    "For-init2": "for (int i = 0, j = ({probe}); i < 1; i++) { sink(j); } return 0;",
    "CatchFilter": "try { sink(1); } catch (E e) when (chk({probe})) {} return 0;",
    "For-body": "for (int i = 0; i < 1; i++) { sink({probe}); } return 0;",
    "Foreach-iter": "foreach (var x in ({probe})) { sink(x); } return 0;",
    "Foreach-body": "foreach (var x in items) { sink({probe}); } return 0;",
    "While-cond": "while ({probe} > 0) { break; } return 0;",
    "While-body": "while (a > 0) { sink({probe}); break; } return 0;",
    "DoWhile-body": "do { sink({probe}); } while (a > 0); return 0;",
    "Switch-disc": "switch ({probe}) { default: return 0; }",
    "Switch-case": "switch (a) { case 1: return {probe}; default: return 0; }",
    "SwitchExpr-arm": "return a switch { 1 => {probe}, _ => 0 };",
    "Throw": "if (a > 0) throw new E({probe}); return 0;",
    "Lock": "lock (gate) { sink({probe}); } return 0;",
    "Using": "using (var r = open({probe})) { sink(r); } return 0;",
    "TryCatch-body": "try { sink({probe}); } catch (Exception e) {} return 0;",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_wrap(_STMT[label].replace("{probe}", "helper()")),
               _wrap(_STMT[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_csharp._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "return f({probe});",
    "Call-recv": "return ({probe}).Foo(a);",
    "Member": "return ({probe}).X;",
    "Index-recv": "return ({probe})[0];",
    "Index-idx": "return arr[{probe}];",
    "Binary-left": "return {probe} + 1;",
    "Binary-right": "return a + {probe};",
    "Unary": "return -{probe};",
    "Update": "int x = {probe}; x++; return x;",
    "Ternary-cond": "return {probe} > 0 ? 1 : 2;",
    "Ternary-then": "return a > 0 ? {probe} : 2;",
    "Ternary-else": "return a > 0 ? 1 : {probe};",
    "Cast": "return (long)({probe});",
    "New-arg": "return new S({probe});",
    "NewArray-size": "var z = new int[{probe}]; return z[0];",
    "ArrayInit": "var z = new int[] { {probe}, 1 }; return z[0];",
    "Await": "return await Task({probe});",
    "Interpolation": "return $\"v={ {probe} }\".Length;",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_wrap(_EXPR[label].replace("{probe}", "helper()")),
               _wrap(_EXPR[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_compound_assignment_rebinds_like_explicit():
    aug = sc.fingerprint_source(
        "class T { int f(int x, int e){ int z = x; z += e; z += e; return z; } }")["T.f"]
    explicit = sc.fingerprint_source(
        "class T { int f(int x, int e){ int z = x; z = z + e; z = z + e; return z; } }")["T.f"]
    assert similarity(aug, explicit) >= 0.99


def test_cast_carries_operand_not_type():
    cast = sc.fingerprint_source("class T { long t(int a){ return (long)(use(a)); } }")["T.t"]
    plain = sc.fingerprint_source("class T { long t(int a){ return use(a); } }")["T.t"]
    assert similarity(cast, plain) >= 0.99


def test_constructor_is_keyed_and_carries_flow():
    fps = sc.fingerprint_source("class C { int f; C(int z){ this.f = compute(z); } }")
    assert "C.C" in fps, "constructor not keyed as C.C"
    ignores = sc.fingerprint_source("class C { int f; C(int z){ this.f = 0; } }")["C.C"]
    assert similarity(fps["C.C"], ignores) < 1.0


def test_namespace_is_not_part_of_key():
    fps = sc.fingerprint_source("namespace App.Sub { class C { int m(int a){ return a; } } }")
    assert "C.m" in fps and "App.Sub.C.m" not in fps


def test_local_function_keyed_under_method():
    # The extractor keys a local function `Enclosing.local` — the body matrix must agree.
    fps = sc.fingerprint_source(
        "class C { void Outer(int a){ int Inner(int y){ return compute(y); } Inner(a); } }")
    assert "C.Outer" in fps and "C.Outer.Inner" in fps
    uses = fps["C.Outer.Inner"]
    ignores = sc.fingerprint_source(
        "class C { void Outer(int a){ int Inner(int y){ return 0; } Inner(a); } }")["C.Outer.Inner"]
    assert similarity(uses, ignores) < 1.0


def test_expression_bodied_method_walked():
    # `int M(int a) => helper(a);` carries flow exactly like a `{ return helper(a); }` body.
    uses = sc.fingerprint_source("class C { int M(int a) => helper(a); }")["C.M"]
    ignores = sc.fingerprint_source("class C { int M(int a) => 0; }")["C.M"]
    assert similarity(uses, ignores) < 1.0


def test_nested_lambda_is_opaque():
    a = sc.fingerprint_source(
        "class T { void t(int a){ System.Action g = () => foo(); use(g); } }")["T.t"]
    b = sc.fingerprint_source(
        "class T { void t(int a){ System.Action g = () => bar(); use(g); } }")["T.t"]
    assert similarity(a, b) >= 0.99
