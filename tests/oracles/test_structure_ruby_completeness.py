"""Completeness oracle for the Ruby body fingerprint (v3.7.0) — the white-box guard, ported.

Same method as the other per-language oracles, per `docs/BODY_MATRIX_LESSONS.md`: for every
value-bearing Ruby construct, two source variants differing ONLY by an inner `helper()` (a CALL) vs
`0` (a CONST) MUST produce different fingerprints. If the construct is silently dropped by
`core/structure_ruby._build_vfg`, the two collapse to an identical fingerprint and the oracle fails.

The metamorphic predicate compares fingerprints for EXACT equality (not `similarity < 1.0`): cosine
self-similarity of a large WL vector rounds to 0.999…98 < 1.0, so a threshold check could pass on a
byte-identical (i.e. dropped) fingerprint. The generic fallback in `_build_vfg.ev` is the structural
"nothing silently vanishes" guarantee; this battery is the behavioral check.

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_ruby as sr  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _wrap(body: str) -> str:
    return "def t(a)\n" + body + "\nend\n"


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = sr.fingerprint_source(src_a)
    b = sr.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    if a[name] == b[name]:
        return 1.0  # identical fingerprints == construct dropped; avoid the cosine float-rounding
        #             blind spot: self-cosine of a large WL vector rounds to 0.999...98 < 1.0
    return similarity(a[name], b[name])


_STMT: dict[str, str] = {
    "Trailing": "{probe}",
    "Assign": "x = {probe}\nx",
    "OpAssign": "x = 0\nx += {probe}\nx",
    "ExprStmt": "sink({probe})\n0",
    "If-cond": "if {probe} > 0\n 1\nelse\n 2\nend",
    "If-then": "if a > 0\n {probe}\nelse\n 2\nend",
    "If-else": "if a > 0\n 1\nelse\n {probe}\nend",
    "Elsif-cond": "if a > 9\n 1\nelsif {probe} > 0\n 2\nelse\n 3\nend",
    "Elsif-body": "if a > 9\n 1\nelsif a > 0\n {probe}\nelse\n 3\nend",
    "Unless": "unless {probe} > 0\n 1\nelse\n 2\nend",
    "IfMod": "sink({probe}) if a > 0\n0",
    "Case-disc": "case {probe}\nwhen 1 then 1\nelse 0\nend",
    "Case-when": "case a\nwhen 1 then {probe}\nelse 0\nend",
    # `when` takes COMMA-SEPARATED values, each a `===`-evaluated expression and a REPEATED `pattern`
    # field. child_by_field_name (first-only) dropped every value past the first (v3.7.0 panel).
    "Case-when-2nd": "case a\nwhen 1, {probe} then 1\nelse 0\nend",
    "Case-when-3rd": "case a\nwhen 1, 2, {probe} then 1\nelse 0\nend",
    "While-cond": "while {probe} > 0 do break end\n0",
    "While-body": "while a > 0 do sink({probe}); break end\n0",
    "Until": "until {probe} > 0 do break end\n0",
    "WhileMod": "x = 0\nx = {probe} while a > 9\nx",
    "For-iter": "for x in ({probe})\n sink(x)\nend\n0",
    "For-body": "for x in items\n sink({probe})\nend\n0",
    "Return": "return {probe}",
    "BeginRescueElse": "begin\n risky\nrescue\n 1\nelse\n {probe}\nend",
    "BeginEnsure": "begin\n sink({probe})\nensure\n cleanup\nend\n0",
    "ParenSeq": "(sink({probe}); 0)",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_wrap(_STMT[label].replace("{probe}", "helper()")),
               _wrap(_STMT[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_ruby._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "f({probe})",
    "Call-recv": "({probe}).foo(a)",
    "Index-obj": "({probe})[0]",
    "Index-idx": "arr[{probe}]",
    "Binary-left": "{probe} + 1",
    "Binary-right": "a + {probe}",
    "Unary": "-({probe})",
    "Ternary-cond": "{probe} > 0 ? 1 : 2",
    "Ternary-then": "a > 0 ? {probe} : 2",
    "Ternary-else": "a > 0 ? 1 : {probe}",
    "Range": "({probe})..10",
    "ArrayElem": "[{probe}, 1]",
    "HashValue": "{ k: {probe} }",
    "StringInterp": "\"v=#{ {probe} }\"",
    "IndexAssign": "h = {}\nh[k] = {probe}\nh",
    "AttrAssign": "obj.attr = {probe}\n0",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_wrap(_EXPR[label].replace("{probe}", "helper()")),
               _wrap(_EXPR[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_default_parameter_value_is_walked():
    # A CALL vs a CONST in a parameter's default value must change the fingerprint (it carries flow).
    uses = sr.fingerprint_source("def f(a, b = helper())\n a\nend")["f"]
    ignores = sr.fingerprint_source("def f(a, b = 0)\n a\nend")["f"]
    assert similarity(uses, ignores) < 1.0


def test_compound_assignment_rebinds_like_explicit():
    aug = sr.fingerprint_source("def f(x, e)\n z = x\n z += e\n z += e\n z\nend")["f"]
    explicit = sr.fingerprint_source("def f(x, e)\n z = x\n z = z + e\n z = z + e\n z\nend")["f"]
    assert similarity(aug, explicit) >= 0.99


def test_module_class_method_keyed_dotted():
    fps = sr.fingerprint_source("module M\n class C\n  def m(x); x; end\n end\nend")
    assert "M.C.m" in fps and "C.m" not in fps


def test_singleton_method_keyed_bare_with_prefix():
    fps = sr.fingerprint_source("module M\n def self.top(a); helper(a); end\nend")
    assert "M.top" in fps
    uses = fps["M.top"]
    ignores = sr.fingerprint_source("module M\n def self.top(a); 0; end\nend")["M.top"]
    assert similarity(uses, ignores) < 1.0


def test_top_level_def_keyed_bare():
    fps = sr.fingerprint_source("def free_fn(z)\n z + 1\nend")
    assert "free_fn" in fps


def test_block_is_opaque():
    # A block / do…end passed to a call is one opaque NESTED leaf (a closure). Two methods differing
    # only inside a block body fingerprint the same.
    a = sr.fingerprint_source("def t(a)\n [1].each { |x| foo(x) }\nend")["t"]
    b = sr.fingerprint_source("def t(a)\n [1].each { |x| bar(x) }\nend")["t"]
    assert similarity(a, b) >= 0.99
