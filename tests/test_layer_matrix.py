"""End-to-end tests for the layered code-property matrix (design §5c): `get_matrix(layer=...)`.

The CALL layer is the shipped inter-procedural relation submatrix (now tagged `layer="call"`); the
EXPRESSION layer drills into a SINGLE function's intra-procedural value-flow graph, on demand and
advisory. Statement/PDG is reserved. These pin the drill-down, the layer tag, and the refusal paths.
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


def test_statement_layer_is_reserved(tmp_path):
    store, calc_id = _index_one(tmp_path)
    m = sg.get_matrix(store, calc_id, layer="statement")
    assert not m.ok and "reserved" in " ".join(m.review_reasons).lower()


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
