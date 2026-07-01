"""White-box differential oracle: the Rust STATEMENT/PDG read-projection vs. the VFG sibling.

Every §5c-p3 Rust PDG defect found in review (R222 self/super/crate reads, R223 value-position
control/block body reads, R224 let-else else-block reads, R225 let-chain plain-clause reads, R226
spurious macro-name read, R228 struct-pattern shorthand bindings) was ONE class of bug: the PDG's
read/write projection (`collect`/`add_target`/`_cond_reads`/`cond_edges` in `structure_rust._build_pdg`)
silently diverging from the VFG (`_build_vfg`), which walks the same AST independently. Rather than
catch these one panel at a time, this oracle cross-checks the two builders over a generated corpus so
the whole class regresses loudly.

The cross-check is name-attributable via a single free variable:
  * VFG: params become `PARAM` nodes; a param is READ iff its PARAM node has an outgoing edge.
  * PDG: ENTRY (node 0) seeds every param's reaching-def, so with exactly ONE param any `(0, _, 'D')`
    edge means that param was read.

Hard invariant (the exact direction the recurring bugs violated):  VFG-reads(v) ⟹ PDG-reads(v).
A PDG that drops a consumed read the VFG captures fails here. The reverse direction (PDG reads more
than the VFG) is allowed — the VFG copy-propagates / discards dead values, so it legitimately
under-reads in places the PDG does not (verified in review); asserting `==` there would false-fail.

Two companion families pin the store side (bindings must reach later uses) and the precision side
(names in non-value positions must NOT be read).
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_rust  # noqa: E402


def _pdg_reads_v(body: str) -> bool:
    """True iff the single param `v` is read by the PDG (an ENTRY-sourced data edge exists)."""
    src = f"fn f(v: i32) -> i32 {{ {body} }}"
    _labels, edges = structure_rust.pdg_source(src)["f"]
    return any(s == 0 and k == "D" for s, _d, k in edges)


def _vfg_reads_v(body: str) -> bool:
    """True iff the single param `v` is read by the VFG (its PARAM node has an outgoing edge)."""
    src = f"fn f(v: i32) -> i32 {{ {body} }}"
    labels, edges = structure_rust.vfg_source(src)["f"]
    param_idxs = {i for i, lab in enumerate(labels) if lab == "PARAM"}
    return any(s in param_idxs for s, _d, _k in edges)


# Value-position wrappers: each embeds its argument in a position that READS it. Composing them
# (below, to depth 2) generates construct *combinations* no hand-written corpus would enumerate —
# e.g. a read buried inside a match arm inside a value-position if inside a block.
_WRAPPERS = {
    "id": lambda x: x,
    "add": lambda x: f"({x} + 1)",
    "neg": lambda x: f"(-{x})",
    "ref": lambda x: f"(&{x})",
    "cast": lambda x: f"({x} as i64)",
    "paren": lambda x: f"(({x}))",
    "block": lambda x: f"{{ {x} }}",
    "block_let": lambda x: f"{{ let _t = {x}; _t }}",
    "if": lambda x: f"if cond() {{ {x} }} else {{ {x} }}",
    "iflet": lambda x: f"if let Some(_p) = opt() {{ {x} }} else {{ {x} }}",
    "iflet_chain": lambda x: f"if let Some(_p) = opt() && {x} > 0 {{ 1 }} else {{ 2 }}",
    "match": lambda x: f"match sel() {{ _ => {x} }}",
    "match_block": lambda x: f"match sel() {{ _ => {{ {x} }} }}",
    "loop_break": lambda x: f"loop {{ break {x}; }}",
    "tuple": lambda x: f"({x}, 0)",
    "array": lambda x: f"[{x}, 0]",
    "structlit": lambda x: f"S {{ field: {x} }}",
    "field": lambda x: f"{x}.field",
    "methodcall": lambda x: f"{x}.method()",
    "callarg": lambda x: f"callee({x})",
    "index": lambda x: f"holder()[{x} as usize]",
    "macro_arg": lambda x: f"vec![{x}]",
    "try": lambda x: f"maybe({x})",
    "range": lambda x: f"({x}..10)",
}


def _body_from(expr: str) -> str:
    # bind the wrapped expression, then read it back — so the value is live and the read of `v` (if
    # the wrappers preserve it, which they all do) must thread through to `_r`.
    return f"let _r = {expr}; let _z = _r; _z"


@pytest.mark.parametrize("name", sorted(_WRAPPERS))
def test_single_wrapper_read_not_dropped(name):
    # curated: every wrapper embeds its argument in a position that READS it, so the PDG must record
    # a read of v. (The VFG is a weaker ground truth here — it copy-props / drops some dead values,
    # e.g. `loop { break v }` — so we assert the PDG directly and use the VFG only in the composed
    # family below, where it acts as an independent lower bound.)
    expr = _WRAPPERS[name]("v")
    body = _body_from(expr)
    assert _pdg_reads_v(body), f"PDG DROPPED a read in wrapper {name!r}: {expr}"


_COMPOSE = ["block", "block_let", "if", "iflet", "match", "match_block", "loop_break", "tuple",
            "array", "structlit", "add", "cast", "paren", "callarg", "macro_arg"]


@pytest.mark.parametrize("outer,inner", list(itertools.product(_COMPOSE, _COMPOSE)))
def test_composed_wrappers_read_not_dropped(outer, inner):
    # nest one value-position construct inside another; the read of v is now two levels deep.
    expr = _WRAPPERS[outer](_WRAPPERS[inner]("v"))
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread v through {outer}∘{inner} (copy-prop/dead-value); "
                    "no ground truth to compare")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in {outer}∘{inner}: {expr}"


def test_self_receiver_read_not_dropped():
    # self/super/crate are seeded at ENTRY like params; a method that reads `self` must show it.
    src = "struct C { n: i32 } impl C { fn m(&self) -> i32 { self.n + 1 } }"
    _l, e = structure_rust.pdg_source(src)["C.m"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "self receiver read dropped"


# --- store side: a binding introduced by a pattern must reach a later use (a D edge 1 -> 2) --------

_BINDINGS = {
    "plain": ("q", "q"),
    "tuple": ("(a, b)", "a + b"),
    "nested_tuple": ("(a, (b, c))", "a + b + c"),
    "struct_shorthand": ("P { x, y }", "x + y"),
    "struct_single": ("P { x }", "x"),
    "struct_rest": ("P { x, .. }", "x"),
    "struct_renamed": ("P { x: a }", "a"),
    "struct_ref": ("P { ref x, mut y }", "x + y"),
    "tuple_struct": ("Wrap(a, b)", "a + b"),
    "slice": ("[a, b]", "a + b"),
    "ref_pat": ("ref a", "a"),
    "mut_pat": ("mut a", "a"),
}


# --- function-local const/static: an evaluated initializer binds a name (like `let`) — the binding
# must reach a later use, and the declared name must NOT be projected as a value read of itself. The
# VFG's `_walk_block` already special-cases these; the JS/Go PDGs thread them too (R231 opus).
@pytest.mark.parametrize("kw", ["const", "static"])
def test_local_const_binding_reaches_use(kw):
    src = f"fn f() -> i32 {{ {kw} K: i32 = 7; let _z = K; _z }}"
    _labels, edges = structure_rust.pdg_source(src)["f"]
    # node 1 = the const/static decl, node 2 = the use `let _z = K` — the binding must reach it.
    assert (1, 2, "D") in edges, f"local {kw} binding did not reach its use"


@pytest.mark.parametrize("kw", ["const", "static"])
def test_local_const_name_not_read_as_value(kw):
    # a param shadowing the declared name must NOT be chained through the decl: the decl DEFINES the
    # name, it does not READ it, so ENTRY's param must not reach the decl node via that name.
    src = f"fn f(K: i32) -> i32 {{ {kw} K: i32 = 7; let _z = K; _z }}"
    _labels, edges = structure_rust.pdg_source(src)["f"]
    assert (0, 1, "D") not in edges, (
        f"local {kw} declared name spuriously read as a value at its own declaration"
    )


def test_local_const_initializer_read_not_dropped():
    # the initializer is a real value position — a param used there IS read (soundness preserved).
    src = "fn f(v: i32) -> i32 { const K: i32 = v + 1; let _z = K; _z }"
    _labels, edges = structure_rust.pdg_source(src)["f"]
    assert any(s == 0 and k == "D" for s, _d, k in edges), "const initializer read dropped"


@pytest.mark.parametrize("name", sorted(_BINDINGS))
def test_pattern_binding_reaches_use(name):
    pat, use = _BINDINGS[name]
    src = f"fn f(p: P) -> i32 {{ let {pat} = p; let _z = {use}; _z }}"
    _labels, edges = structure_rust.pdg_source(src)["f"]
    # node 1 = the pattern `let`, node 2 = the use `let` — the binding must reach it.
    assert (1, 2, "D") in edges, f"binding {name!r} ({pat}) did not reach its use ({use})"


# --- precision side: a name in a NON-value position must NOT be read (no phantom def-use edge) ------

_SPURIOUS = {
    # (setup-let-name, body-using-that-name-in-a-non-value-position)
    "macro_name": ("matches", "let _z = matches!(sel(), Some(_));"),
    "field_name": ("field", "let _z = holder().field;"),
    "method_name": ("method", "let _z = holder().method();"),
    # a loop/block label is a control target, not a value — a local sharing its name must not be read
    "break_label": ("outer", "let _z = 'outer: loop { break 'outer; };"),
    "break_label_value": ("outer", "let _z = 'outer: loop { break 'outer 1; };"),
    "continue_label": ("lbl", "'lbl: loop { if cond() { continue 'lbl; } break; }"),
    "block_label_value": ("blk", "let _z = 'blk: { 5 };"),
    "block_label_stmt": ("blk", "'blk: { g(); }"),
    "block_label_break": ("blk", "let _z = 'blk: { break 'blk 1; };"),
    # a lifetime in a turbofish type-argument list is a type position, not a value
    "lifetime_turbofish": ("lt", "let _z = foo::<'lt>();"),
}


@pytest.mark.parametrize("name", sorted(_SPURIOUS))
def test_non_value_position_name_is_not_read(name):
    binding, stmt = _SPURIOUS[name]
    src = f"fn f() -> i32 {{ let {binding} = 1; {stmt} 0 }}"
    _labels, edges = structure_rust.pdg_source(src)["f"]
    # node 1 = `let <binding> = 1`. If the name were read as a value in a later statement, a D edge
    # would originate from node 1. It must not — the name sits in a macro/field/method position.
    assert not any(s == 1 and k == "D" for s, _d, k in edges), (
        f"{name}: a name in a non-value position was read as a value (phantom edge)"
    )


# The precision side must hold for BOTH builders, not just the PDG. Using the VFG as a lower-bound
# ground truth (VFG-reads ⟹ PDG-reads) is only sound if the VFG itself does not spuriously read a
# non-value token — otherwise it would demand a read the correct PDG rightly omits. So assert neither
# builder reads a control/type token (label / lifetime) even when it collides with the param name.
_NONVALUE_V = {
    "loop_label": "'v: loop { break 'v; }",
    "continue_label": "'v: loop { if cond() { continue 'v; } break; }",
    "block_label": "let _z = 'v: { 5 }; 0",
    "block_label_stmt": "'v: { g(); } 0",
    "lifetime_turbofish": "let _z = foo::<'v>(); 0",
    "type_turbofish": "let _z = foo::<v>(); 0",  # a turbofish TYPE arg named v is a type, not a value
    # an `if let`/`while let` pattern binding (and struct field-pattern name) is a fresh binding, not
    # a read of the outer value it collides with
    "iflet_binding": "if let Some(v) = mk() { 0 } else { 1 }",
    "whilelet_binding": "while let Some(v) = mk() { } 0",
    "iflet_field_binding": "if let S { v: _x } = mk() { 0 } else { 1 }",
}


@pytest.mark.parametrize("name", sorted(_NONVALUE_V))
def test_non_value_token_read_by_neither_builder(name):
    body = _NONVALUE_V[name]
    assert not _pdg_reads_v(body), f"{name}: PDG read a non-value token as the param value"
    assert not _vfg_reads_v(body), f"{name}: VFG read a non-value token as the param value"
