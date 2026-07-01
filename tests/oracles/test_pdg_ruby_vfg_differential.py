"""White-box differential oracle: the Ruby STATEMENT/PDG read-projection vs. the VFG sibling.

The Ruby companion to the Java/C#/C++/Rust differential oracles. The recurring §5c defect class is
ONE bug: the PDG's read/write projection (`collect`/`bind_place` in `structure_ruby._build_pdg`)
silently diverging from the VFG (`_build_vfg`), which walks the same tree-sitter CST independently.
This oracle cross-checks the two builders over a generated corpus so the whole class regresses loudly.

Name-attributable via a single free variable `v` (a method parameter):
  * VFG: params become `PARAM` nodes; a param is READ iff its PARAM node has an outgoing edge.
  * PDG: ENTRY (node 0) seeds every param's reaching-def, so with exactly ONE param any `(0, _, 'D')`
    edge means that param was read.

Hard invariant (the exact direction the recurring bugs violated):  VFG-reads(v) ⟹ PDG-reads(v).
The reverse (PDG reads more than the VFG) is allowed — the VFG copy-propagates / discards dead values.

Ruby is expression-oriented: control constructs in VALUE position fold into the enclosing statement's
reads. Companion families pin the store side (bindings must reach later uses) and the precision side
(a call's method NAME must NOT be read by EITHER builder).
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_ruby  # noqa: E402


def _pdg_reads_v(body: str) -> bool:
    src = f"def f(v)\n{body}\nend"
    _labels, edges = structure_ruby.pdg_source(src)["f"]
    return any(s == 0 and k == "D" for s, _d, k in edges)


def _vfg_reads_v(body: str) -> bool:
    src = f"def f(v)\n{body}\nend"
    labels, edges = structure_ruby.vfg_source(src)["f"]
    param_idxs = {i for i, lab in enumerate(labels) if lab == "PARAM"}
    return any(s in param_idxs for s, _d, _k in edges)


_WRAPPERS = {
    "id": lambda x: x,
    "add": lambda x: f"({x} + 1)",
    "neg": lambda x: f"(-{x})",
    "paren": lambda x: f"(({x}))",
    "ternary": lambda x: f"(cond ? {x} : 0)",
    "recv": lambda x: f"{x}.m",
    "callarg": lambda x: f"callee({x})",
    "index": lambda x: f"holder[{x}]",
    "array": lambda x: f"[{x}, 1]",
    "range": lambda x: f"({x}..10)",
    "interp": lambda x: f'"val #{{{x}}}"',
}


def _body_from(expr: str) -> str:
    # bind the wrapped expression, then read it back — the copy must thread through.
    return f"  r = {expr}\n  z = r\n  z"


@pytest.mark.parametrize("name", sorted(_WRAPPERS))
def test_single_wrapper_read_not_dropped(name):
    expr = _WRAPPERS[name]("v")
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread v through {name} (copy-prop/dead-value)")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in wrapper {name!r}: {expr}"


_COMPOSE = ["add", "neg", "paren", "ternary", "callarg", "index", "array", "recv"]


@pytest.mark.parametrize("outer,inner", list(itertools.product(_COMPOSE, _COMPOSE)))
def test_composed_wrappers_read_not_dropped(outer, inner):
    expr = _WRAPPERS[outer](_WRAPPERS[inner]("v"))
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread v through {outer}∘{inner}")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in {outer}∘{inner}: {expr}"


# --- value-position control folding: v read inside an if/case/while used as a value ---------------

@pytest.mark.parametrize("body", [
    "  x = if v then 1 else 2 end\n  x",
    "  x = case v\n  when 1 then 9\n  else 0\n  end\n  x",
    "  x = (v > 0 ? v : -v)\n  x",
    "  x = [v, v + 1].first\n  x",
])
def test_value_position_control_reads(body):
    assert _vfg_reads_v(body), f"(sanity) VFG should read v: {body}"
    assert _pdg_reads_v(body), f"PDG dropped a value-position read: {body}"


# --- store side: a binding must reach a later use --------------------------------------------------

def test_assignment_reaches_use():
    _l, e = structure_ruby.pdg_source("def f(a)\n  x = a + 1\n  y = x + 2\n  y\nend")["f"]
    assert (1, 2, "D") in e, "assignment did not reach its use"
    assert (2, 3, "D") in e, "second assignment did not reach its use"


# --- precision side: a call's method NAME must NOT be read by EITHER builder ----------------------

_NONVALUE_V = {
    "method_name": "  obj.v\n  0",
    "method_name_args": "  obj.v(1, 2)\n  0",
    "bare_call_name": "  v()\n  0",
}


@pytest.mark.parametrize("name", sorted(_NONVALUE_V))
def test_method_name_not_read_by_either_builder(name):
    body = _NONVALUE_V[name]
    # NB: `v()` / `obj.v` — v is the method NAME, not the value; neither builder reads the param.
    assert not _pdg_reads_v(body), f"{name}: PDG read a method name as the param value"
    assert not _vfg_reads_v(body), f"{name}: VFG read a method name as the param value"
