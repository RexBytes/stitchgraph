"""Completeness oracle for the PHP body fingerprint (v3.7.0) — the white-box guard, ported.

Same method as the other per-language oracles, per `docs/BODY_MATRIX_LESSONS.md`: for every
value-bearing PHP construct, two source variants differing ONLY by an inner `helper()` (a CALL) vs `0`
(a CONST) MUST produce different fingerprints, else the construct is being dropped by
`core/structure_php._build_vfg`. The metamorphic predicate compares fingerprints for EXACT equality
(not `similarity < 1.0`, which can pass on a byte-identical/dropped fingerprint of a large body).

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_php as sp  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _wrap(body: str) -> str:
    return "<?php\nfunction t($a) {\n" + body + "\n}\n"


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = sp.fingerprint_source(src_a)
    b = sp.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    if a[name] == b[name]:
        return 1.0  # identical fingerprints == construct dropped; dodge the cosine rounding blind spot
    return similarity(a[name], b[name])


_STMT: dict[str, str] = {
    "Return": "return {probe};",
    "Assign": "$x = {probe}; return $x;",
    "AugAssign": "$x = 0; $x += {probe}; return $x;",
    "ConcatAssign": "$x = ''; $x .= {probe}; return $x;",
    "ExprStmt": "sink({probe}); return 0;",
    "Echo": "echo {probe};",
    "If-cond": "if ({probe} > 0) { return 1; } return 0;",
    "If-body": "if ($a > 0) { return {probe}; } return 0;",
    "ElseIf-cond": "if ($a > 9) { return 1; } elseif ({probe} > 0) { return 2; } return 0;",
    "Else-body": "if ($a > 0) { return 1; } else { return {probe}; }",
    "For-init": "for ($i = {probe}; $i < 1; $i++) {} return 0;",
    "For-cond": "for ($i = 0; {probe} > $i; $i++) {} return 0;",
    "For-update": "for ($i = 0; $i < 1; $i = ({probe})) {} return 0;",
    "For-body": "for ($i = 0; $i < 1; $i++) { sink({probe}); } return 0;",
    "Foreach-iter": "foreach (({probe}) as $x) { sink($x); } return 0;",
    "Foreach-body": "foreach ($xs as $x) { sink({probe}); } return 0;",
    "While-cond": "while ({probe} > 0) { break; } return 0;",
    "While-body": "while ($a > 0) { sink({probe}); break; } return 0;",
    "Switch-disc": "switch ({probe}) { default: return 0; }",
    "Switch-case": "switch ($a) { case 1: return {probe}; default: return 0; }",
    "Throw": "if ($a > 0) throw new E({probe}); return 0;",
    "TryCatch": "try { sink({probe}); } catch (E $e) {} return 0;",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_wrap(_STMT[label].replace("{probe}", "helper()")),
               _wrap(_STMT[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST inside this statement produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_php._build_vfg")


_EXPR: dict[str, str] = {
    "Call-arg": "return f({probe});",
    "Call-recv": "return ({probe})->foo($a);",
    "Member": "return ({probe})->x;",
    "Index-obj": "return ({probe})[0];",
    "Index-idx": "return $arr[{probe}];",
    "Binary-left": "return {probe} + 1;",
    "Binary-right": "return $a + {probe};",
    "Unary": "return -({probe});",
    "Ternary-cond": "return {probe} > 0 ? 1 : 2;",
    "Ternary-then": "return $a > 0 ? {probe} : 2;",
    "Ternary-else": "return $a > 0 ? 1 : {probe};",
    "Cast": "return (int)({probe});",
    "New-arg": "return new S({probe});",
    "AnonClassArg": "$o = new class({probe}) {}; return $o;",
    "AnonClassArgExtends": "$o = new class({probe}) extends B {}; return $o;",
    "ArrayElem": "$z = [{probe}, 1]; return $z[0];",
    "ArrayPair": "$z = ['k' => {probe}]; return $z['k'];",
    "Interp": "return \"v={$a}{$b}\" . {probe};",
    "InterpHole": "$b = {probe}; return \"v={$b}\";",
    "HeredocHole": "$b = {probe}; return <<<EOT\nv={$b}\nEOT;",
    "MatchArm": "return match($a) { 1 => {probe}, default => 0 };",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expression_is_walked(label):
    sim = _sim(_wrap(_EXPR[label].replace("{probe}", "helper()")),
               _wrap(_EXPR[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expression position produced identical fingerprints "
        f"(sim={sim}) — the expression type is dropping a child sub-expression")


def test_augmented_assignment_rebinds_like_explicit():
    aug = sp.fingerprint_source(
        "<?php\nfunction f($x, $e) { $z = $x; $z += $e; $z += $e; return $z; }")["f"]
    explicit = sp.fingerprint_source(
        "<?php\nfunction f($x, $e) { $z = $x; $z = $z + $e; $z = $z + $e; return $z; }")["f"]
    assert similarity(aug, explicit) >= 0.99


def test_class_method_keyed_namespace_excluded():
    fps = sp.fingerprint_source(
        "<?php\nnamespace App\\Sub;\nclass C { function m($a) { return $a; } }")
    assert "C.m" in fps and "App.Sub.C.m" not in fps and "App\\Sub\\C.m" not in fps


def test_top_level_function_keyed_bare():
    fps = sp.fingerprint_source("<?php\nfunction free_fn($z) { return $z + 1; }")
    assert "free_fn" in fps


def test_constructor_keyed_and_carries_flow():
    fps = sp.fingerprint_source(
        "<?php\nclass C { private $f; function __construct($z) { $this->f = compute($z); } }")
    assert "C.__construct" in fps
    ignores = sp.fingerprint_source(
        "<?php\nclass C { private $f; function __construct($z) { $this->f = 0; } }")["C.__construct"]
    assert similarity(fps["C.__construct"], ignores) < 1.0


def test_closure_is_opaque():
    a = sp.fingerprint_source("<?php\nfunction t($a) { $g = function() { return foo(); }; use_it($g); }")["t"]
    b = sp.fingerprint_source("<?php\nfunction t($a) { $g = function() { return bar(); }; use_it($g); }")["t"]
    assert similarity(a, b) >= 0.99
