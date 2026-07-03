"""White-box differential oracle: the C# STATEMENT/PDG read-projection vs. the VFG sibling.

The C# companion to `test_pdg_java_vfg_differential.py` / `test_pdg_cpp_vfg_differential.py`. The
recurring §5c defect class (learned across Python/JS/Go/Rust/C++/Java) is ONE bug: the PDG's
read/write projection (`collect`/`bind_place` in `structure_csharp._build_pdg`) silently diverging
from the VFG (`_build_vfg`), which walks the same tree-sitter CST independently. This oracle
cross-checks the two builders over a generated corpus so the whole class regresses loudly.

Name-attributable via a single free variable `v`:
  * VFG: params become `PARAM` nodes; a param is READ iff its PARAM node has an outgoing edge.
  * PDG: ENTRY (node 0) seeds every param's reaching-def, so with exactly ONE param any `(0, _, 'D')`
    edge means that param was read.

Hard invariant (the exact direction the recurring bugs violated):  VFG-reads(v) ⟹ PDG-reads(v).
The reverse (PDG reads more than the VFG) is allowed — the VFG copy-propagates / discards dead values.

Companion families pin the store side (bindings must reach later uses) and the precision side (names
in non-value positions — TYPEs, the member NAME in a `.` access, the call method NAME, LABELs — must
NOT be read by EITHER builder — the typed-language hazard front-loaded from the C++/Java rounds).
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_csharp  # noqa: E402


def _pdg_reads_v(body: str) -> bool:
    src = f"class C {{ int f(int v) {{ {body} }} }}"
    _labels, edges = structure_csharp.pdg_source(src)["C.f"]
    return any(s == 0 and k == "D" for s, _d, k in edges)


def _vfg_reads_v(body: str) -> bool:
    src = f"class C {{ int f(int v) {{ {body} }} }}"
    labels, edges = structure_csharp.vfg_source(src)["C.f"]
    param_idxs = {i for i, lab in enumerate(labels) if lab == "PARAM"}
    return any(s in param_idxs for s, _d, _k in edges)


_WRAPPERS = {
    "id": lambda x: x,
    "add": lambda x: f"({x} + 1)",
    "neg": lambda x: f"(-{x})",
    "cast": lambda x: f"((long){x})",
    "paren": lambda x: f"(({x}))",
    "ternary": lambda x: f"(cond ? {x} : 0)",
    "field": lambda x: f"{x}.Field",
    "recv": lambda x: f"{x}.M()",
    "callarg": lambda x: f"Callee({x})",
    "index": lambda x: f"Holder()[{x}]",
    "is": lambda x: f"({x} is string)",
}


def _body_from(expr: str) -> str:
    return f"int r = {expr}; int z = r; return z;"


@pytest.mark.parametrize("name", sorted(_WRAPPERS))
def test_single_wrapper_read_not_dropped(name):
    expr = _WRAPPERS[name]("v")
    assert _pdg_reads_v(_body_from(expr)), f"PDG DROPPED a read in wrapper {name!r}: {expr}"


_COMPOSE = ["add", "neg", "cast", "paren", "ternary", "callarg", "index", "field", "recv"]


@pytest.mark.parametrize("outer,inner", list(itertools.product(_COMPOSE, _COMPOSE)))
def test_composed_wrappers_read_not_dropped(outer, inner):
    expr = _WRAPPERS[outer](_WRAPPERS[inner]("v"))
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread v through {outer}∘{inner} (copy-prop/dead-value)")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in {outer}∘{inner}: {expr}"


# --- store side: a binding introduced by a declarator / loop var must reach a later use ------------

def test_local_declaration_reaches_use():
    _l, e = structure_csharp.pdg_source(
        "class C { int f(int a){ int x = a + 1; int y = x + 2; return y; } }")["C.f"]
    assert (1, 2, "D") in e, "declaration did not reach its use"
    assert (2, 3, "D") in e, "second declaration did not reach its use"


def test_foreach_binding_reaches_use():
    _l, e = structure_csharp.pdg_source(
        "class C { int f(int[] xs){ int s = 0; foreach (var q in xs) { s += q; } return s; } }")["C.f"]
    assert any(k == "D" for _s, _d, k in e), "foreach binding produced no data dependence"


# --- precision side: a name in a NON-value position must NOT be read by EITHER builder --------------

_NONVALUE_V = {
    # v as a TYPE name (a local declaration type / an `is` type)
    "type_in_decl": "v x = null; return 0;",
    "is_type": "bool b = obj is v; return 0;",
    "generic_type_arg": "System.Collections.Generic.List<v> xs = null; return 0;",
    "cast_type": "object x = (v) obj; return 0;",
    # v as a call's method NAME (the receiver/args are values; the method name is not)
    "method_name": "obj.v(); return 0;",
    "method_name_args": "obj.v(1, 2); return 0;",
    # v as a member/field name
    "member_name": "int z = obj.v; return z;",
    # v as a statement LABEL / goto target
    "label": "v: Foo(); goto v;",
}


@pytest.mark.parametrize("name", sorted(_NONVALUE_V))
def test_non_value_position_read_by_neither_builder(name):
    body = _NONVALUE_V[name]
    assert not _pdg_reads_v(body), f"{name}: PDG read a non-value token as the param value"
    assert not _vfg_reads_v(body), f"{name}: VFG read a non-value token as the param value"
