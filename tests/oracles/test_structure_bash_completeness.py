"""Completeness oracle for the Bash body fingerprint (v3.7.0) — the white-box guard, ported.

Same method as the other per-language oracles, per `docs/BODY_MATRIX_LESSONS.md`, adapted to Bash's
**command-oriented** grammar: for every value-bearing position, two source variants differing ONLY by
an inner `$(helper)` (a command-substitution CALL) vs `0` (a CONST) MUST produce different
fingerprints. If the position is silently dropped by `core/structure_bash._build_vfg`, the two collapse
to a byte-identical fingerprint and the oracle fails.

The metamorphic predicate compares fingerprints for EXACT equality (not `similarity < 1.0`): cosine
self-similarity of a large WL vector rounds to 0.999…98 < 1.0, so a threshold check could pass on a
dropped (byte-identical) fingerprint. The generic fallback in `_build_vfg.ev` is the structural
"nothing silently vanishes" guarantee; this battery is the behavioral check.

Requires the tree-sitter extra (skipped in the core/no-extras job).
"""
from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_bash as sb  # noqa: E402
from stitchgraph.core.structure import similarity  # noqa: E402


def _wrap(body: str) -> str:
    return "t() {\n" + body + "\n}\n"


def _sim(src_a: str, src_b: str, name: str = "t") -> float:
    a = sb.fingerprint_source(src_a)
    b = sb.fingerprint_source(src_b)
    assert name in a, f"fingerprint_source did not capture {name!r} in: {src_a!r}"
    assert name in b
    if a[name] == b[name]:
        return 1.0  # identical fingerprints == position dropped; dodge the cosine rounding blind spot
    return similarity(a[name], b[name])


# Command / statement positions. The probe is a command substitution `$(helper)` (a CALL) vs `0`.
_STMT: dict[str, str] = {
    "Assign": "x={probe}\necho $x",
    "LocalAssign": "local x={probe}\necho $x",
    "DeclareAssign": "declare x={probe}\necho $x",
    "CommandArg": "echo {probe}",
    "PrefixAssign": "FOO={probe} run\necho done",
    "If-cond-test": "if [[ {probe} -gt 0 ]]; then echo 1; fi",
    "If-cond-cmd": "if {probe}; then echo 1; fi",
    "If-body": "if [[ $a -gt 0 ]]; then echo {probe}; fi",
    "Elif-cond": "if [[ $a -gt 9 ]]; then echo 1; elif [[ {probe} -gt 0 ]]; then echo 2; fi",
    "Else-body": "if [[ $a -gt 0 ]]; then echo 1; else echo {probe}; fi",
    "For-iter": "for x in {probe}; do echo $x; done",
    "For-body": "for x in a b; do echo {probe}; done",
    "CStyleFor-cond": "for ((i=0; $(({probe} + 1)) < 1; i++)); do echo 1; done",
    "While-cond-test": "while [[ {probe} -gt 0 ]]; do break; done",
    "While-cond-cmd": "while {probe}; do break; done",
    "While-body": "while [[ $a -gt 0 ]]; do echo {probe}; break; done",
    "Until-cond": "until [[ {probe} -gt 0 ]]; do break; done",
    "Case-disc": "case {probe} in 1) echo 1;; esac",
    "Case-body": "case $a in 1) echo {probe};; esac",
    "Pipeline": "echo {probe} | cat",
    "Subshell": "( echo {probe} )",
    "Redirect": "echo {probe} > /dev/null",
    "SubscriptLHS": "arr[{probe}]=x\necho done",
}


@pytest.mark.parametrize("label", sorted(_STMT))
def test_value_bearing_statement_is_walked(label):
    sim = _sim(_wrap(_STMT[label].replace("{probe}", "$(helper)")),
               _wrap(_STMT[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this position produced identical fingerprints "
        f"(sim={sim}) — the construct is being dropped by structure_bash._build_vfg")


# Expansion / substitution value positions.
_EXPR: dict[str, str] = {
    "CmdSubst": "x=$(echo {probe})\necho $x",
    "Arith": "x=$(( {probe} + 1 ))\necho $x",
    "StringHole": 'echo "v={probe}"',
    "ExpansionDefault": "x=${y:-{probe}}\necho $x",
    "ConcatArg": "echo pre{probe}post",
    "CalleeSubst": "$(helper_name {probe})",
    "SubscriptIdx": "v=${arr[{probe}]}\necho $v",
    "SubscriptIdxArg": "echo ${arr[{probe}]}",
}


@pytest.mark.parametrize("label", sorted(_EXPR))
def test_value_bearing_expansion_is_walked(label):
    sim = _sim(_wrap(_EXPR[label].replace("{probe}", "$(helper)")),
               _wrap(_EXPR[label].replace("{probe}", "0")))
    assert sim < 1.0, (
        f"{label}: a CALL vs a CONST in this expansion position produced identical fingerprints "
        f"(sim={sim}) — the expansion type is dropping a child sub-expression")


def test_dynamic_callee_is_walked():
    # A command whose *name* is itself a command substitution — `$(get_cmd) arg` — must carry the
    # callee's CALL, not collapse to an opaque free word.
    uses = sb.fingerprint_source("t() {\n$(resolve_helper) arg\n}")["t"]
    ignores = sb.fingerprint_source("t() {\nplaincmd arg\n}")["t"]
    assert similarity(uses, ignores) < 1.0


def test_assignment_copy_propagates():
    aug = sb.fingerprint_source("f() {\n z=$x\n z=$(g $z)\n z=$(g $z)\n echo $z\n}")["f"]
    explicit = sb.fingerprint_source("f() {\n z=$x\n w=$(g $z)\n v=$(g $w)\n echo $v\n}")["f"]
    assert similarity(aug, explicit) >= 0.9


def test_function_keyed_bare():
    fps = sb.fingerprint_source("compute() {\n local x=$(helper)\n echo $x\n}")
    assert "compute" in fps


def test_posix_function_keyword_form():
    fps = sb.fingerprint_source("function do_work {\n echo hi\n}")
    assert "do_work" in fps


def test_nested_function_is_opaque():
    # A function defined inside another body is an opaque NESTED leaf; the outer key is unaffected by
    # the inner body's contents.
    a = sb.fingerprint_source("t() {\n inner() { foo; }\n inner\n}")["t"]
    b = sb.fingerprint_source("t() {\n inner() { bar; }\n inner\n}")["t"]
    assert similarity(a, b) >= 0.99
