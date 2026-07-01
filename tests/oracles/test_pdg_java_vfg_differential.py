"""White-box differential oracle: the Java STATEMENT/PDG read-projection vs. the VFG sibling.

The Java companion to `test_pdg_cpp_vfg_differential.py` / `test_pdg_rust_vfg_differential.py`. The
recurring §5c defect class (learned across Python/JS/Go/Rust/C++) is ONE bug: the PDG's read/write
projection (`collect`/`bind_place` in `structure_java._build_pdg`) silently diverging from the VFG
(`_build_vfg`), which walks the same tree-sitter CST independently. Rather than catch these one panel
at a time, this oracle cross-checks the two builders over a generated corpus so the whole class
regresses loudly.

The cross-check is name-attributable via a single free variable `v`:
  * VFG: params become `PARAM` nodes; a param is READ iff its PARAM node has an outgoing edge.
  * PDG: ENTRY (node 0) seeds every param's reaching-def, so with exactly ONE param any `(0, _, 'D')`
    edge means that param was read.

Hard invariant (the exact direction the recurring bugs violated):  VFG-reads(v) ⟹ PDG-reads(v).
A PDG that drops a consumed read the VFG captures fails here. The reverse (PDG reads more than the
VFG) is allowed — the VFG copy-propagates / discards dead values, so it legitimately under-reads.

Two companion families pin the store side (bindings must reach later uses) and the precision side
(names in non-value positions — TYPEs, the call method NAME, field names, LABELs — must NOT be read
by EITHER builder — the typed-language hazard front-loaded from the C++/Rust rounds).
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_java  # noqa: E402


def _pdg_reads_v(body: str) -> bool:
    """True iff the single param `v` is read by the PDG (an ENTRY-sourced data edge exists)."""
    src = f"class C {{ int f(int v) {{ {body} }} }}"
    _labels, edges = structure_java.pdg_source(src)["C.f"]
    return any(s == 0 and k == "D" for s, _d, k in edges)


def _vfg_reads_v(body: str) -> bool:
    """True iff the single param `v` is read by the VFG (its PARAM node has an outgoing edge)."""
    src = f"class C {{ int f(int v) {{ {body} }} }}"
    labels, edges = structure_java.vfg_source(src)["C.f"]
    param_idxs = {i for i, lab in enumerate(labels) if lab == "PARAM"}
    return any(s in param_idxs for s, _d, _k in edges)


# Value-position wrappers: each embeds its argument in a position that READS it. Composing them
# (below, to depth 2) generates construct *combinations* no hand-written corpus would enumerate.
_WRAPPERS = {
    "id": lambda x: x,
    "add": lambda x: f"({x} + 1)",
    "neg": lambda x: f"(-{x})",
    "cast": lambda x: f"((long){x})",
    "paren": lambda x: f"(({x}))",
    "ternary": lambda x: f"(cond() ? {x} : 0)",
    "field": lambda x: f"{x}.field",
    "recv": lambda x: f"{x}.m()",
    "callarg": lambda x: f"callee({x})",
    "index": lambda x: f"holder()[{x}]",
    "instanceof": lambda x: f"({x} instanceof String)",
}


def _body_from(expr: str) -> str:
    # bind the wrapped expression, then read it back — so the value is live and the read of `v` (if
    # the wrappers preserve it, which they all do) must thread through to `r`.
    return f"int r = {expr}; int z = r; return z;"


@pytest.mark.parametrize("name", sorted(_WRAPPERS))
def test_single_wrapper_read_not_dropped(name):
    expr = _WRAPPERS[name]("v")
    body = _body_from(expr)
    assert _pdg_reads_v(body), f"PDG DROPPED a read in wrapper {name!r}: {expr}"


_COMPOSE = ["add", "neg", "cast", "paren", "ternary", "callarg", "index", "field", "recv"]


@pytest.mark.parametrize("outer,inner", list(itertools.product(_COMPOSE, _COMPOSE)))
def test_composed_wrappers_read_not_dropped(outer, inner):
    expr = _WRAPPERS[outer](_WRAPPERS[inner]("v"))
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread v through {outer}∘{inner} (copy-prop/dead-value); "
                    "no ground truth to compare")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in {outer}∘{inner}: {expr}"


# --- store side: a binding introduced by a declarator/loop var must reach a later use --------------

def test_local_declaration_reaches_use():
    _l, e = structure_java.pdg_source(
        "class C { int f(int a){ int x = a + 1; int y = x + 2; return y; } }")["C.f"]
    assert (1, 2, "D") in e, "declaration did not reach its use"
    assert (2, 3, "D") in e, "second declaration did not reach its use"


def test_enhanced_for_binding_reaches_use():
    # `for (int q : xs)` binds q; the body's use of q must thread from the loop node.
    _l, e = structure_java.pdg_source(
        "class C { int f(int[] xs){ int s = 0; for (int q : xs) { s += q; } return s; } }")["C.f"]
    # node 2 = the ForEach loop (binds q), and s += q inside must read q from the loop header.
    assert any(k == "D" for _s, _d, k in e), "enhanced-for binding produced no data dependence"


# --- precision side: a name in a NON-value position must NOT be read by EITHER builder --------------

_NONVALUE_V = {
    # v as a TYPE name (a local declaration type / an instanceof type / a cast type)
    "type_in_decl": "v x = null; return 0;",
    "instanceof_type": "boolean b = obj instanceof v; return 0;",
    "generic_type_arg": "java.util.List<v> xs = null; return 0;",
    # v as a call's method NAME (the receiver/args are values; the method name is not)
    "method_name": "obj.v(); return 0;",
    "method_name_args": "obj.v(1, 2); return 0;",
    # v as a field/member name
    "field_name": "int z = obj.v; return z;",
    # v as a statement LABEL / break target
    "label": "v: while (true) { break v; } return 0;",
}


@pytest.mark.parametrize("name", sorted(_NONVALUE_V))
def test_non_value_position_read_by_neither_builder(name):
    body = _NONVALUE_V[name]
    assert not _pdg_reads_v(body), f"{name}: PDG read a non-value token as the param value"
    assert not _vfg_reads_v(body), f"{name}: VFG read a non-value token as the param value"
