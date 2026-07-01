"""End-to-end tests for the layered code-property matrix (design §5c): `get_matrix(layer=...)`.

The CALL layer is the shipped inter-procedural relation submatrix (now tagged `layer="call"`); the
EXPRESSION layer drills into a SINGLE function's intra-procedural value-flow graph (all 12 languages);
the STATEMENT layer drills into its program-dependence graph (Python + the JS family so far). All
on demand and advisory. These pin the drill-down, the layer tag, and the refusal paths.
"""
from __future__ import annotations

from pathlib import Path

import stitchgraph as sg


def _index_one(tmp_path: Path) -> tuple[sg.Store, str]:
    (tmp_path / "m.py").write_text(
        "def calc(a, b):\n"
        "    x = helper(a)\n"
        "    y = x + b\n"
        "    return y * 2\n\n"
        "def other(a):\n"
        "    return a\n"
    )
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    calc_id = next(nid for nid in store.all_node_ids() if nid.endswith("::calc"))
    return store, calc_id


def test_call_layer_is_tagged_and_default(tmp_path):
    store, _ = _index_one(tmp_path)
    m = sg.get_matrix(store, "m.py", "CALLS")
    assert m.ok and m.result["layer"] == "call"  # default layer unchanged, now tagged


def test_expression_layer_drills_into_one_function(tmp_path):
    store, calc_id = _index_one(tmp_path)
    m = sg.get_matrix(store, calc_id, layer="expression")
    assert m.ok, m.review_reasons
    r = m.result
    assert r["layer"] == "expression"
    assert r["function"] == "calc"
    # value-flow operations of `x=helper(a); y=x+b; return y*2`
    assert "CALL" in r["labels"] and any(lbl.startswith("BINOP") for lbl in r["labels"])
    assert r["n"] == len(r["labels"]) and r["cells"]
    # every cell references valid node indices and a data/control kind
    for c in r["cells"]:
        assert 0 <= c["src"] < r["n"] and 0 <= c["dst"] < r["n"] and c["k"] in ("d", "c")
    assert m.meta.get("layer") == "expression"


def test_expression_layer_refuses_multi_function_scope(tmp_path):
    store, _ = _index_one(tmp_path)
    m = sg.get_matrix(store, "m.py", layer="expression")  # m.py has calc AND other
    assert not m.ok
    assert "single function" in " ".join(m.review_reasons).lower()


def test_expression_layer_refuses_when_no_function(tmp_path):
    store, _ = _index_one(tmp_path)
    m = sg.get_matrix(store, "does/not/exist.py", layer="expression")
    assert not m.ok


def test_statement_layer_drills_the_pdg(tmp_path):
    store, calc_id = _index_one(tmp_path)
    m = sg.get_matrix(store, calc_id, layer="statement")
    assert m.ok, m.review_reasons
    r = m.result
    assert r["layer"] == "statement"
    assert r["function"] == "calc"
    assert r["labels"][0] == "ENTRY"                       # PDG entry node carries the params
    assert {c["k"] for c in r["cells"]} <= {"C", "D"}      # control / data dependence
    for c in r["cells"]:
        assert 0 <= c["src"] < r["n"] and 0 <= c["dst"] < r["n"]


def test_statement_layer_refuses_multi_function_scope(tmp_path):
    store, _ = _index_one(tmp_path)
    m = sg.get_matrix(store, "m.py", layer="statement")   # calc AND other
    assert not m.ok and "single function" in " ".join(m.review_reasons).lower()


def test_statement_layer_drills_a_js_function(tmp_path):
    # The STATEMENT layer covers the JS family (js/ts/tsx) as well as Python (v3.10.0).
    (tmp_path / "app.ts").write_text(
        "export function classify(x: number): string {\n"
        "  const a = x + 1;\n"
        "  if (a > 0) { return 'pos'; }\n"
        "  return 'neg';\n"
        "}\n"
    )
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    jid = [n for n in store.all_node_ids() if n.endswith("app.ts::classify")]
    if jid:  # only if the tree-sitter extra indexed the TS file
        m = sg.get_matrix(store, jid[0], layer="statement")
        assert m.ok, m.review_reasons
        r = m.result
        assert r["layer"] == "statement" and r["labels"][0] == "ENTRY"
        assert {c["k"] for c in r["cells"]} <= {"C", "D"}
        assert "If" in r["labels"] and r["labels"].count("Return") == 2


def test_statement_layer_refuses_unsupported_language(tmp_path):
    # A language without a STATEMENT frontend yet (Go) refuses cleanly; no crash.
    (tmp_path / "m.go").write_text("package m\nfunc f(a int) int { return a }\n")
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    gid = [n for n in store.all_node_ids() if n.endswith("::f") and ".go" in n]
    if gid:  # only if the tree-sitter extra indexed the Go file
        m = sg.get_matrix(store, gid[0], layer="statement")
        msg = " ".join(m.review_reasons).lower()
        assert not m.ok and "supported-language" in msg and "js/ts/tsx" in msg


def test_statement_layer_never_affects_liveness(tmp_path):
    store, calc_id = _index_one(tmp_path)
    before = sg.find_stale(store)
    sg.get_matrix(store, calc_id, layer="statement")
    after = sg.find_stale(store)
    assert before.result == after.result


def test_unknown_layer_refused(tmp_path):
    store, calc_id = _index_one(tmp_path)
    m = sg.get_matrix(store, calc_id, layer="bogus")
    assert not m.ok


def test_expression_layer_never_affects_liveness(tmp_path):
    # The expression layer is advisory: drilling into a body must not change what find_stale reports
    # (cardinal rule — the body matrix never feeds liveness).
    store, calc_id = _index_one(tmp_path)
    before = sg.find_stale(store)
    sg.get_matrix(store, calc_id, layer="expression")
    after = sg.find_stale(store)
    assert before.result == after.result
