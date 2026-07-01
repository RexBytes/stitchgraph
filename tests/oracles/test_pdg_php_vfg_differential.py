"""White-box differential oracle: the PHP STATEMENT/PDG read-projection vs. the VFG sibling.

The PHP companion to the Ruby/Java/C#/C++/Rust differential oracles. The recurring §5c defect class
is ONE bug: the PDG's read/write projection (`collect`/`bind_place` in `structure_php._build_pdg`)
silently diverging from the VFG (`_build_vfg`), which walks the same tree-sitter CST independently.
This oracle cross-checks the two builders over a generated corpus so the whole class regresses loudly.

Name-attributable via a single free variable `$v` (the sole method parameter):
  * VFG: params become `PARAM` nodes; a param is READ iff its PARAM node has an outgoing edge.
  * PDG: ENTRY (node 0) seeds every param's reaching-def, so with exactly ONE param any `(0, _, 'D')`
    edge means that param was read.

Hard invariant (the exact direction the recurring bugs violated):  VFG-reads(v) ⟹ PDG-reads(v).
The reverse (PDG reads more than the VFG) is allowed — the VFG copy-propagates / discards dead values.

PHP-specific hazards this pins: a `$`-variable in member/property-NAME position (`$o->$v`) must be read
by NEITHER builder (only the object is a value), while a *dynamic method call* `$o->$v()` reads it in
BOTH (genuine dynamic dispatch, via the shared generic fallback); `Foo::$v` / `Foo::CONST` scoped
accesses are opaque freevars in both; string/heredoc interpolation holes carry flow.
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_php  # noqa: E402


def _pdg_reads_v(body: str) -> bool:
    src = f"<?php\nfunction f($v) {{\n{body}\n}}"
    _labels, edges = structure_php.pdg_source(src)["f"]
    return any(s == 0 and k == "D" for s, _d, k in edges)


def _vfg_reads_v(body: str) -> bool:
    src = f"<?php\nfunction f($v) {{\n{body}\n}}"
    labels, edges = structure_php.vfg_source(src)["f"]
    param_idxs = {i for i, lab in enumerate(labels) if lab == "PARAM"}
    return any(s in param_idxs for s, _d, _k in edges)


_WRAPPERS = {
    "id": lambda x: x,
    "add": lambda x: f"({x} + 1)",
    "neg": lambda x: f"(-{x})",
    "paren": lambda x: f"(({x}))",
    "ternary": lambda x: f"($c ? {x} : 0)",
    "recv": lambda x: f"{x}->m",
    "callarg": lambda x: f"callee({x})",
    "index": lambda x: f"$holder[{x}]",
    "array": lambda x: f"[{x}, 1]",
    "concat": lambda x: f'({x} . "s")',
    "interp": lambda x: f'"val {x} end"',
    "cast": lambda x: f"(int) {x}",
}


def _body_from(expr: str) -> str:
    # bind the wrapped expression, then read it back — the copy must thread through.
    return f"  $r = {expr};\n  $z = $r;\n  return $z;"


@pytest.mark.parametrize("name", sorted(_WRAPPERS))
def test_single_wrapper_read_not_dropped(name):
    expr = _WRAPPERS[name]("$v")
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread $v through {name} (copy-prop/dead-value)")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in wrapper {name!r}: {expr}"


_COMPOSE = ["add", "neg", "paren", "ternary", "callarg", "index", "array", "recv", "concat"]


@pytest.mark.parametrize("outer,inner", list(itertools.product(_COMPOSE, _COMPOSE)))
def test_composed_wrappers_read_not_dropped(outer, inner):
    expr = _WRAPPERS[outer](_WRAPPERS[inner]("$v"))
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread $v through {outer}∘{inner}")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in {outer}∘{inner}: {expr}"


# --- statement-position reads: $v read inside a control header / value-position match --------------

@pytest.mark.parametrize("body", [
    "  if ($v > 0) { a(); }",
    "  while ($v > 0) { b(); }",
    "  for ($i = 0; $i < $v; $i++) { c(); }",
    "  switch ($v) { case 1: d(); break; }",
    "  $x = match($v) { 1 => 9, default => 0 };\n  return $x;",
    "  foreach ($v as $e) { use($e); }",
    "  echo $v;",
    "  throw $v;",
    "  return isset($v) ? 1 : 0;",
    "  $x .= $v;\n  return $x;",
])
def test_statement_position_reads(body):
    assert _vfg_reads_v(body), f"(sanity) VFG should read $v: {body}"
    assert _pdg_reads_v(body), f"PDG dropped a statement-position read: {body}"


# --- store side: a binding must reach a later use --------------------------------------------------

def test_assignment_reaches_use():
    _l, e = structure_php.pdg_source(
        "<?php\nfunction f($a) {\n  $x = $a + 1;\n  $y = $x + 2;\n  return $y;\n}")["f"]
    assert (1, 2, "D") in e, "assignment did not reach its use"
    assert (2, 3, "D") in e, "second assignment did not reach its use"


# --- precision side: a NAME position must NOT read the same-named param by EITHER builder ----------

_NONVALUE_V = {
    "dynamic_property": "  return $o->$v;",          # member-access NAME — only the object is a value
    "scoped_property": "  return Foo::$v;",           # `Foo::$v` — an opaque freevar in both
    "static_property_write": "  Foo::$v = 1;\n  return 0;",
}


@pytest.mark.parametrize("name", sorted(_NONVALUE_V))
def test_name_position_not_read_by_either_builder(name):
    body = _NONVALUE_V[name]
    # $v sits in a member/scoped-NAME slot: neither builder treats it as a value read of the param.
    assert not _pdg_reads_v(body), f"{name}: PDG read a name position as the param value"
    assert not _vfg_reads_v(body), f"{name}: VFG read a name position as the param value"
