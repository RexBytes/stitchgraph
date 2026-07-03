"""Regression pins for the body-matrix drift fixes from the 2026-07-03 external review (F5a-F5e).

Each of these was a per-language divergence from behaviour a sibling frontend already got right —
the cost of nine hand-synchronized structure_* files (finding D2). Every case here reproduces the
review's empirical failure and pins the fix in BOTH directions where applicable.
"""
from __future__ import annotations

import pytest

from stitchgraph.core.structure import fingerprint_source as py_fp
from stitchgraph.core.structure import pdg_source as py_pdg
from stitchgraph.core.structure import similarity


# -- F5a: JS classic for-loop must bind its loop variable --------------------------------
def test_js_for_initializer_binds_loop_variable():
    """`for (let i = 0; …)` routed the initializer through ev(), which has no declaration
    case, so `i` read FREE everywhere and the identical hoisted-init form scored ~0.52
    (Java scores the same shape 1.0)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core import structure_js as sj
    a = "function f(a, n) { let s = 0; for (let i = 0; i < n; i++) { s = s + a[i]; } return s; }"
    b = "function f(a, n) { let s = 0; let i = 0; for (; i < n; i++) { s = s + a[i]; } return s; }"
    fa = sj.fingerprint_source(a, "javascript")["f"]
    fb = sj.fingerprint_source(b, "javascript")["f"]
    assert similarity(fa, fb) > 0.9, "for-init vs hoisted-init must read as the same shape"
    # and a genuinely different loop variable use still differs from a constant body
    c = "function f(a, n) { let s = 0; for (let i = 0; i < n; i++) { s = s + 1; } return s; }"
    fc = sj.fingerprint_source(c, "javascript")["f"]
    assert similarity(fa, fc) < 1.0


# -- F5b: Bash multi-command if condition must not be truncated --------------------------
def test_bash_multi_command_if_condition_not_dropped():
    """`condition` is a REPEATED field: `if cmd1; cmd2; then` dropped cmd2 entirely, so a
    function with an extra guard fingerprinted IDENTICAL (1.0) to one without — a silent
    false clone (the repeated-field hazard fixed for Java in R197)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core import structure_bash as sb
    a = 'f() {\n  if validate "$1"; then\n    echo ok\n  fi\n}\n'
    b = 'f() {\n  if validate "$1"; log_check "$1"; then\n    echo ok\n  fi\n}\n'
    fa = sb.fingerprint_source(a)["f"]
    fb = sb.fingerprint_source(b)["f"]
    assert similarity(fa, fb) < 1.0, "an extra guard command must change the fingerprint"
    # PDG: the second guard's variable read must reach the If header
    src = 'f() {\n  x=$1\n  y=$2\n  if check "$x"; audit "$y"; then\n    echo ok\n  fi\n}\n'
    labels, edges = sb.pdg_source(src)["f"]
    d = {(s, t) for s, t, k in edges if k == "D"}
    if_id = labels.index("If")
    assert (1, if_id) in d and (2, if_id) in d, "both guards' reads must reach the If node"


# -- F5c: Python walrus must bind its target ----------------------------------------------
def test_python_walrus_binding_matches_two_line_form():
    """ast.NamedExpr had no ev() case: the Store-ctx target dropped the binding, so
    `if (x := f()):` read x FREE everywhere and scored ~0.32 against its equivalent."""
    a = "def f():\n    if (x := g()):\n        return x + 1\n    return 0\n"
    b = "def f():\n    x = g()\n    if x:\n        return x + 1\n    return 0\n"
    assert similarity(py_fp(a)["f"], py_fp(b)["f"]) == 1.0


# -- F5d: PHP foreach key => value pair must bind both names ------------------------------
def test_php_foreach_pair_binds_key_and_value():
    """The key/value form parses as a `pair` node that bind() didn't handle, so BOTH loop
    variables read FREE in the body (documented then as a symmetric gap; now closed in both
    builders)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core import structure_php as sp
    src = '<?php function f($m) { $s = ""; foreach ($m as $k => $v) { $s = $s . $k . $v; } return $s; }'
    labels, edges = sp.pdg_source(src, "php")["f"]
    d = {(s, t) for s, t, k in edges if k == "D"}
    fe = labels.index("ForEach")
    assert any(s == fe for s, _ in d), "the pair-bound loop variables must flow from ForEach"
    # VFG: the loop variables now resolve through ITERVAR, not FREE
    vfg_labels, _ = sp.vfg_source(src, "php")["f"]
    assert "ITERVAR" in vfg_labels


# -- F5e: Python PDG must not read through lambda bodies -----------------------------------
def test_python_pdg_lambda_body_is_opaque():
    """header_names used ast.walk, descending into Lambda bodies — creating data edges for
    lambda-captured names that no tree-sitter sibling produces (they all stop at
    _FUNC_NODES) and that Python's own VFG (lambda = opaque NESTED) doesn't."""
    src = "def f(items, factor):\n    key = lambda v: v * factor\n    return sorted(items, key=key)\n"
    labels, edges = py_pdg(src)["f"]
    d = {(s, t) for s, t, k in edges if k == "D"}
    assert (0, 1) not in d, "lambda-captured `factor` must not leak into the Assign header"
    assert (1, 2) in d, "the real key->Return edge must survive"


# -- F6: tree-sitter references must not bind to MODULE nodes ------------------------------
def test_treesitter_calls_do_not_bind_to_module_nodes(tmp_path):
    """`by_lang` included MODULE nodes, so `helper()` in a Ruby/JS/Go repo bound to
    `helper.rb`'s module node — violating the `_ref_edges` invariant the Python extractor
    and the store enforce (panels R13B/R31A), inflating module fan_in and breaking
    incremental==full convergence for tree-sitter languages (review 2026-07-03, F6).
    Imports still resolve to modules."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    import stitchgraph as sg
    from stitchgraph.core.model import NodeKind, Relation
    (tmp_path / "helper.rb").write_text("def run_it\n  1\nend\n")
    (tmp_path / "main.rb").write_text("def helper\n  2\nend\n\ndef go\n  helper()\nend\ngo\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        mod_ids = {n["id"] for n in store.conn.execute(
            "SELECT id FROM nodes WHERE kind = ?", (NodeKind.MODULE.value,))}
        bad = [dict(r) for r in store.conn.execute(
            "SELECT src, relation, dst_id FROM edges WHERE dst_id IS NOT NULL AND relation != ?",
            (Relation.IMPORTS.value,)) if r["dst_id"] in mod_ids]
        assert not bad, f"non-IMPORTS edges bound to MODULE nodes: {bad}"
