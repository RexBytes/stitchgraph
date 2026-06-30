"""Completeness oracle for the C/C++ body fingerprint (v3.5.0) — the white-box guard, ported.

Same method as the Python / JS / Go / Rust oracles, per `docs/BODY_MATRIX_LESSONS.md`: for every
value-bearing C/C++ construct, two source variants differing ONLY by an inner `helper()` (a CALL
node) vs `0` (a CONST node) MUST produce different fingerprints. If the construct is silently dropped
by `core/structure_cpp._build_vfg`, the two collapse to the same fingerprint (similarity 1.0) and the
oracle fails — catching the whole "dropped node type" class deterministically. (It already caught one
during development: the cpp grammar keeps a subscript index under `indices` -> subscript_argument_list,
not C's `index` field, so `a[helper()]` initially collapsed; the generic fallback didn't reach it
because subscript_expression had an explicit — wrong-field — handler.)

Note on the introspective guard: tree-sitter has no small stmt/expr supertype set, so the Python
oracle's `ast.__subclasses__()` enumeration does not port. The **generic fallback** in
`structure_cpp._build_vfg` is the structural "nothing silently vanishes" guarantee; this metamorphic
battery is the behavioral check that the value-bearing positions are actually walked. One grammar
(`cpp`) parses both C and C++.

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_cpp as sc  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = sc.fingerprint_source(src_a)
    b = sc.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    return similarity(a[name], b[name])


_STMT: dict[str, str] = {
    "Return": "int t(int a){ return {probe}; }",
    "Decl": "int t(int a){ int x = {probe}; return x; }",
    "Decl-multi": "int t(int a){ int x = {probe}, y = 1; return x + y; }",
    "Assign": "int t(int a){ int x = 0; x = {probe}; return x; }",
    "CompoundAssign": "int t(int a){ int x = 0; x += {probe}; return x; }",
    "ShiftAssign": "int t(int a){ int x = 0; x <<= {probe}; return x; }",
    "ExprStmt": "void t(int a){ sink({probe}); }",
    "If-cond": "int t(int a){ if ({probe} > 0) { return 1; } return 0; }",
    "If-body": "int t(int a){ if (a > 0) { return {probe}; } return 0; }",
    "Else-body": "int t(int a){ if (a > 0) { return 1; } else { return {probe}; } return 0; }",
    "For-init": "int t(int a){ for (int i = {probe}; i < 1; i++) {} return 0; }",
    "For-cond": "void t(int a){ for (int i = 0; {probe} > i; i++) {} }",
    "For-update": "void t(int a){ for (int i = 0; i < 1; i = ({probe})) {} }",
    "For-body": "void t(int a){ for (int i = 0; i < 1; i++) { sink({probe}); } }",
    "RangeFor-iter": "void t(V a){ for (auto x : {probe}) { sink(x); } }",
    "RangeFor-body": "void t(V a){ for (auto x : a) { sink({probe}); } }",
    "While-cond": "void t(int a){ while ({probe} > 0) { break; } }",
    "While-body": "void t(int a){ while (a > 0) { sink({probe}); break; } }",
    "DoWhile-body": "void t(int a){ do { sink({probe}); } while (a > 0); }",
    "Switch-disc": "int t(int a){ switch ({probe}) { default: return 0; } }",
    "Switch-case": "int t(int a){ switch (a) { case 1: return {probe}; default: return 0; } }",
    "IfInit": "int t(int a){ if (int x = {probe}; x) { return 1; } return 0; }",
    "SwitchInit": "int t(int a){ switch (int x = {probe}; x) { default: return 0; } }",
    "RangeForInit": "void t(V v){ for (int n = {probe}; auto x : v) { sink(x); } }",
    "VlaSize": "void t(int a){ int arr[{probe}]; sink(arr); }",
    "LambdaInitCapture": "void t(int a){ auto g = [z = {probe}](){ return z; }; sink(g); }",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_STMT[label].replace("{probe}", "helper()"),
               _STMT[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_cpp._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "int t(int a){ return f({probe}); }",
    "Call-callee": "int t(int a){ return ({probe})(a); }",
    "Field-dot": "int t(S a){ return ({probe}).x; }",
    "Field-arrow": "int t(S* a){ return ({probe})->x; }",
    "Index-arg": "int t(int* a){ return ({probe})[0]; }",
    "Index-idx": "int t(int* a){ return a[{probe}]; }",
    "Binary-left": "int t(int a){ return {probe} + 1; }",
    "Binary-right": "int t(int a){ return a + {probe}; }",
    "Unary": "int t(int a){ return -{probe}; }",
    "Update": "int t(int a){ int x = {probe}; x++; return x; }",
    "Deref": "int t(int* a){ return *({probe}); }",
    "AddrOf": "int t(int a){ return *(&{probe}); }",
    "Ternary-cond": "int t(int a){ return {probe} ? 1 : 2; }",
    "Ternary-then": "int t(int a){ return a ? {probe} : 2; }",
    "Cast": "long t(int a){ return (long)({probe}); }",
    "Comma": "int t(int a){ return ({probe}, 1); }",
    "InitList": "S t(int a){ return (S){ {probe}, 1 }; }",
    "New-arg": "S* t(int a){ return new S({probe}); }",
    "NewArray-size": "int* t(int a){ return new int[{probe}]; }",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_EXPR[label].replace("{probe}", "helper()"),
               _EXPR[label].replace("{probe}", "0"))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_compound_assignment_rebinds_like_explicit():
    # `x += e` must rebind x to the result and use the base operator, threading across statements
    # exactly like `x = x + e` — the invariance every frontend guarantees.
    aug = sc.fingerprint_source("int f(int x, int e){ int z = x; z += e; z += e; return z; }")["f"]
    explicit = sc.fingerprint_source(
        "int f(int x, int e){ int z = x; z = z + e; z = z + e; return z; }")["f"]
    assert similarity(aug, explicit) >= 0.99


def test_cast_carries_operand_not_type():
    # A C-style cast `(T)x` is value flow from x; the target type carries none. A cast over a call
    # must fingerprint like the bare call.
    cast = sc.fingerprint_source("long t(int a){ return (long)(use_it(a)); }")["t"]
    plain = sc.fingerprint_source("long t(int a){ return use_it(a); }")["t"]
    assert similarity(cast, plain) >= 0.99


def test_sizeof_is_a_compile_time_constant():
    # `sizeof(expr)` never EVALUATES its operand (C/C++ semantics) — it is a compile-time constant.
    # Collapsing it to CONST is intentional, NOT a dropped sub-expression: sizeof(helper()) and
    # sizeof(0) are genuinely equivalent (helper() is not called). Documents the approximation.
    a = sc.fingerprint_source("int t(int a){ return sizeof(helper()); }")["t"]
    b = sc.fingerprint_source("int t(int a){ return sizeof(0); }")["t"]
    assert similarity(a, b) >= 0.99


def test_out_of_line_method_keyed_bare():
    # `int Foo::m(...)` is keyed by the bare last component `m` (matching the extractor's out-of-line
    # scheme), not `Foo.m`.
    fps = sc.fingerprint_source("int Foo::compute(int y) { return this->base + y; }")
    assert "compute" in fps and "Foo.compute" not in fps


def test_inline_method_keyed_qualified():
    # An inline class method is keyed `Class.method` (and `this` use carries value flow).
    uses = sc.fingerprint_source("class Foo { int m(int x){ return this->f + x; } };")["Foo.m"]
    ignores = sc.fingerprint_source("class Foo { int m(int x){ return 0; } };")["Foo.m"]
    assert similarity(uses, ignores) < 1.0


def test_reference_return_function_is_captured():
    # R185 (opus): a reference-return function/method (`T& f()`, `V& grow()`, `int& operator+=()`) was
    # silently dropped — the cpp grammar's `reference_declarator` doesn't field-name its inner
    # function_declarator, so the name-unwrap returned None and the function never became a key.
    # It must be captured (a key) and its body must carry value flow.
    free = sc.fingerprint_source("int& f(int x){ return r; }")
    assert "f" in free, "reference-return free function dropped"
    method = sc.fingerprint_source("struct V{ int n; V& grow(int x){ n += x; return *this; } };")
    assert "V.grow" in method, "reference-return method dropped"
    # body carries flow: using the arg differs from ignoring it.
    uses = sc.fingerprint_source("int& f(int x){ return use(x); }")["f"]
    ignores = sc.fingerprint_source("int& f(int x){ return r; }")["f"]
    assert similarity(uses, ignores) < 1.0


def test_constructor_member_initializer_list_is_walked():
    # R186 (opus): a constructor's member-initializer-list (`S(int x): n(compute(x)) {}`) is a SIBLING
    # of the body (a `field_initializer_list`, not inside the compound_statement), so walking only the
    # body silently dropped it — `n(compute(x))` and `n(0)` fingerprinted identically. The init
    # expression must carry value flow.
    uses = sc.fingerprint_source("struct S{ int n; S(int x): n(compute(x)) {} };")["S.S"]
    ignores = sc.fingerprint_source("struct S{ int n; S(int x): n(0) {} };")["S.S"]
    assert similarity(uses, ignores) < 1.0, (
        "constructor member-initializer-list dropped: a CALL vs a CONST init produced identical "
        "fingerprints — the field_initializer_list sibling is not being walked")
    # Multiple members, braced init, and a member read in the body all thread through too.
    a = sc.fingerprint_source(
        "struct S{ int n,m; S(int x): n{helper(x)}, m{1} { use(n); } };")["S.S"]
    b = sc.fingerprint_source(
        "struct S{ int n,m; S(int x): n{0}, m{1} { use(n); } };")["S.S"]
    assert similarity(a, b) < 1.0


def test_nested_lambda_is_opaque():
    # A C++ lambda nested in a function body is one NESTED leaf, not its body (matching the other
    # frontends). Two functions differing only inside a lambda body fingerprint the same.
    a = sc.fingerprint_source("void t(int a){ auto g = [](){ return foo(); }; use(g); }")["t"]
    b = sc.fingerprint_source("void t(int a){ auto g = [](){ return bar(); }; use(g); }")["t"]
    assert similarity(a, b) >= 0.99
