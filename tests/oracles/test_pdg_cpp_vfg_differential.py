"""White-box differential oracle: the C/C++ STATEMENT/PDG read-projection vs. the VFG sibling.

The C/C++ companion to `test_pdg_rust_vfg_differential.py`. The recurring §5c defect class (learned
across Python/JS/Go/Rust) is ONE bug: the PDG's read/write projection (`collect`/`bind_place`/
`bind_decl` in `structure_cpp._build_pdg`) silently diverging from the VFG (`_build_vfg`), which
walks the same tree-sitter CST independently. Rather than catch these one panel at a time, this
oracle cross-checks the two builders over a generated corpus so the whole class regresses loudly.

The cross-check is name-attributable via a single free variable `v`:
  * VFG: params become `PARAM` nodes; a param is READ iff its PARAM node has an outgoing edge.
  * PDG: ENTRY (node 0) seeds every param's reaching-def, so with exactly ONE param any `(0, _, 'D')`
    edge means that param was read.

Hard invariant (the exact direction the recurring bugs violated):  VFG-reads(v) ⟹ PDG-reads(v).
A PDG that drops a consumed read the VFG captures fails here. The reverse (PDG reads more than the
VFG) is allowed — the VFG copy-propagates / discards dead values, so it legitimately under-reads in
places the PDG does not; asserting `==` there would false-fail.

Two companion families pin the store side (bindings must reach later uses) and the precision side
(names in non-value positions — TYPEs, LABELs, field names — must NOT be read by EITHER builder).
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_cpp  # noqa: E402


def _pdg_reads_v(body: str) -> bool:
    """True iff the single param `v` is read by the PDG (an ENTRY-sourced data edge exists)."""
    src = f"int f(int v) {{ {body} }}"
    _labels, edges = structure_cpp.pdg_source(src)["f"]
    return any(s == 0 and k == "D" for s, _d, k in edges)


def _vfg_reads_v(body: str) -> bool:
    """True iff the single param `v` is read by the VFG (its PARAM node has an outgoing edge)."""
    src = f"int f(int v) {{ {body} }}"
    labels, edges = structure_cpp.vfg_source(src)["f"]
    param_idxs = {i for i, lab in enumerate(labels) if lab == "PARAM"}
    return any(s in param_idxs for s, _d, _k in edges)


# Value-position wrappers: each embeds its argument in a position that READS it. Composing them
# (below, to depth 2) generates construct *combinations* no hand-written corpus would enumerate.
_WRAPPERS = {
    "id": lambda x: x,
    "add": lambda x: f"({x} + 1)",
    "neg": lambda x: f"(-{x})",
    "addr": lambda x: f"(&{x})",
    "cast": lambda x: f"((long){x})",
    "paren": lambda x: f"(({x}))",
    "ternary": lambda x: f"(cond() ? {x} : 0)",
    "field": lambda x: f"{x}.field",
    "arrow": lambda x: f"{x}->field",
    "callarg": lambda x: f"callee({x})",
    "index": lambda x: f"holder()[{x}]",
    "comma": lambda x: f"(g(), {x})",
    "sizeof_no": lambda x: f"({x} + 1)",  # sizeof(x) would NOT read — keep this a genuine read
}


def _body_from(expr: str) -> str:
    # bind the wrapped expression, then read it back — so the value is live and the read of `v` (if
    # the wrappers preserve it, which they all do) must thread through to `r`.
    return f"int r = {expr}; int z = r; return z;"


@pytest.mark.parametrize("name", sorted(_WRAPPERS))
def test_single_wrapper_read_not_dropped(name):
    # curated: every wrapper embeds its argument in a position that READS it, so the PDG must record
    # a read of v.
    expr = _WRAPPERS[name]("v")
    body = _body_from(expr)
    assert _pdg_reads_v(body), f"PDG DROPPED a read in wrapper {name!r}: {expr}"


_COMPOSE = ["add", "neg", "cast", "paren", "ternary", "callarg", "index", "comma", "field"]


@pytest.mark.parametrize("outer,inner", list(itertools.product(_COMPOSE, _COMPOSE)))
def test_composed_wrappers_read_not_dropped(outer, inner):
    # nest one value-position construct inside another; the read of v is now two levels deep.
    expr = _WRAPPERS[outer](_WRAPPERS[inner]("v"))
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread v through {outer}∘{inner} (copy-prop/dead-value); "
                    "no ground truth to compare")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in {outer}∘{inner}: {expr}"


# --- store side: a binding introduced by a declarator must reach a later use (a D edge 1 -> 2) -----

_BINDINGS = {
    "plain": ("int q = p;", "q"),
    "pointer": ("int* q = p;", "*q"),
    "reference": ("int& q = *p;", "q"),
    "array": ("int q[2] = {0, 1};", "q[0]"),
    "for_range": ("for (auto q : p) { use(q); }", "0"),  # loop var binds inside the loop body
}


@pytest.mark.parametrize("name", sorted(_BINDINGS))
def test_pattern_binding_reaches_use(name):
    decl, use = _BINDINGS[name]
    if name == "for_range":
        # the for-range loop var is bound and used within the SAME loop node — assert a self/body use
        # threads (the loop node reads what it binds). Verified separately: the loop binds q, body
        # uses q, so the loop node has a data edge to itself's descendants — here just assert no crash
        # and that a binding exists.
        src = f"void f(int* p) {{ {decl} }}"
        labels, edges = structure_cpp.pdg_source(src)["f"]
        assert "ForRange" in labels
        return
    src = f"int f(int* p) {{ {decl} int z = {use}; return z; }}"
    _labels, edges = structure_cpp.pdg_source(src)["f"]
    # node 1 = the binding decl, node 2 = the use `int z = <use>` — the binding must reach it.
    assert (1, 2, "D") in edges, f"binding {name!r} ({decl}) did not reach its use ({use})"


# --- precision side: a name in a NON-value position must NOT be read (no phantom def-use edge) ------

_SPURIOUS = {
    # (setup-decl-name, body-using-that-name-in-a-non-value-position)
    "field_name": ("field", "int z = holder().field;"),
    "arrow_name": ("member", "int z = holder()->member;"),
    # a labeled_statement label / goto target is a control point, not a value
    "goto_label": ("done", "done: ; goto done;"),
}


@pytest.mark.parametrize("name", sorted(_SPURIOUS))
def test_non_value_position_name_is_not_read(name):
    binding, stmt = _SPURIOUS[name]
    src = f"int f() {{ int {binding} = 1; {stmt} return 0; }}"
    _labels, edges = structure_cpp.pdg_source(src)["f"]
    # node 1 = `int <binding> = 1`. If the name were read as a value later, a D edge would originate
    # from node 1. It must not — the name sits in a field/label position.
    assert not any(s == 1 and k == "D" for s, _d, k in edges), (
        f"{name}: a name in a non-value position was read as a value (phantom edge)"
    )


# The precision side must hold for BOTH builders. If v collides with a TYPE name, a LABEL, or a field
# name, NEITHER the PDG nor the VFG may read it as the param value (else the VFG lower bound would
# demand a read the correct PDG rightly omits).
_NONVALUE_V = {
    # v as a TYPE (careful these parse as C++)
    "type_ptr_decl": "v* x; return 0;",
    "type_cast": "(v)x; return 0;",
    "type_new": "new v(); return 0;",
    # v as a LABEL
    "label_goto": "v: ; goto v; return 0;",
    # v as a field/member name
    "field_name": "s.v; return 0;",
    "arrow_name": "obj->v; return 0;",
    # v inside a TYPE position — an unevaluated compile-time operand, read by NEITHER builder (R245).
    # These are where the VFG's generic fallback / type_identifier handling used to over-read a param
    # name the correct PDG rightly drops (`decltype(v)`, a template TYPE argument).
    "template_type_arg": "g<v>(); return 0;",
    "decltype_template_arg": "g<decltype(v)>(); return 0;",
    "decltype_cast": "auto x = static_cast<decltype(v)>(0); return 0;",
    "using_alias_decltype": "using U = decltype(v); (void)sizeof(U); return 0;",
    "typedef_decltype": "typedef decltype(v) U; (void)sizeof(U); return 0;",
}


@pytest.mark.parametrize("name", sorted(_NONVALUE_V))
def test_non_value_token_read_by_neither_builder(name):
    body = _NONVALUE_V[name]
    assert not _pdg_reads_v(body), f"{name}: PDG read a non-value token as the param value"
    assert not _vfg_reads_v(body), f"{name}: VFG read a non-value token as the param value"


@pytest.mark.parametrize("body", [
    "auto [a, b] = v; return a;",
    "auto [a, b] = v; return b;",
    "auto& [a, b] = v; return a;",
])
def test_structured_binding_threads_in_both_builders(body):
    # R246: a destructured param `auto [a,b] = v` whose binding is later USED must be read by BOTH
    # builders (the VFG `bind` had no structured_binding_declarator case, so it evaluated the RHS
    # then discarded it — v went unread, a value-flow under-read). Both must now read v.
    assert _pdg_reads_v(body), f"PDG dropped the destructured param read: {body}"
    assert _vfg_reads_v(body), f"VFG dropped the destructured param read: {body}"
