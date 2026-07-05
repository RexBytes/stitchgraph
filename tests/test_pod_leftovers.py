"""v3.33.0 POD leftovers (research/11 A5/B4/C3): test-anchored co_change,
find_coupling common-caller annotation + scope filter, audit_graph (the call-graph
precision audit against runtime ground truth), and behaviour-mode find_similar."""
from __future__ import annotations

import json

import pytest

import stitchgraph as sg
from stitchgraph.core.model import Edge, Node, NodeKind, Relation


def _graph() -> sg.Store:
    """test_a --CALLS--> target --CALLS--> helper ; dyn is called by NOTHING statically
    (a getattr-dispatched function); sib_a/sib_b share the common caller `hub`."""
    store = sg.Store(":memory:")

    def fn(name, roles=frozenset()):
        store.add_node(Node(f"m.py::{name}", NodeKind.FUNCTION, name, roles=set(roles)))

    for name in ["target", "helper", "dyn", "hub", "sib_a", "sib_b"]:
        fn(name)
    store.add_node(Node("t/test_x.py::test_a", NodeKind.FUNCTION, "test_a",
                        roles={"test"}))
    store.add_node(Node("t/test_x.py::test_b", NodeKind.FUNCTION, "test_b",
                        roles={"test"}))

    def call(s, d):
        store.add_edge(Edge(f"{s}", Relation.CALLS, d.split("::")[-1], dst_id=d))

    call("t/test_x.py::test_a", "m.py::target")
    call("m.py::target", "m.py::helper")
    call("m.py::hub", "m.py::sib_a")
    call("m.py::hub", "m.py::sib_b")
    store.commit()
    return store


def _cov(tmp_path, tests):
    p = tmp_path / "cov.json"
    p.write_text(json.dumps({"format": "stitchgraph-coverage-v1", "tests": tests}))
    return str(p)


def test_co_change_anchored_on_a_test_reports_its_true_coverage(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        "t/test_x.py::test_a[1]": ["m.py::target", "m.py::helper"],
        "t/test_x.py::test_a[2]": ["m.py::target", "m.py::dyn"],
        "t/test_x.py::test_b": ["m.py::sib_a"],
    })
    r = sg.co_change(st, "t/test_x.py::test_a", cov)
    assert r.ok
    # parametrized rows collapse onto the base test; the union is what it covers
    assert r.result["covers"] == ["m.py::dyn", "m.py::helper", "m.py::target"]
    assert r.result["coverage_rows"] == 2


def test_audit_graph_finds_the_resolver_gap(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        # test_a executed target+helper (both statically reachable) AND dyn
        # (getattr-dispatched: NO static path) -> dyn is the missed function
        "t/test_x.py::test_a": ["m.py::target", "m.py::helper", "m.py::dyn"],
        "t/ghost.py::test_ghost": ["m.py::target"],  # no node -> unmatched
    })
    r = sg.audit_graph(st, cov)
    assert r.ok
    assert r.result["tests_audited"] == 1
    assert r.result["tests_unmatched"] == 1
    assert r.result["recall"] == round(2 / 3, 3)
    assert r.result["missed_functions"] == [
        {"function": "m.py::dyn", "tests_missing_it": 1}]
    assert r.needs_review  # missed functions -> resolver-gap review

    # falsification arm: wire the static edge and the miss disappears
    st.add_edge(Edge("t/test_x.py::test_a", Relation.CALLS, "dyn", dst_id="m.py::dyn"))
    st.commit()
    r2 = sg.audit_graph(st, cov)
    assert r2.result["recall"] == 1.0 and not r2.result["missed_functions"]


def test_find_coupling_common_callers_and_scope(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        f"t/test_x.py::test_{i}": ["m.py::sib_a", "m.py::sib_b"] for i in range(3)
    })
    r = sg.find_coupling(st, cov, min_shared=3)
    assert r.ok and r.result["pairs"], r.result
    pair = r.result["pairs"][0]
    assert {pair["a"], pair["b"]} == {"m.py::sib_a", "m.py::sib_b"}
    # the shared dispatcher EXPLAINS the co-activation — that's the annotation's job
    assert pair["common_callers"] == ["m.py::hub"]
    assert pair["cross_file"] is False
    # scope filter: these are same-file siblings, so cross_file_only sees nothing
    r2 = sg.find_coupling(st, cov, min_shared=3, scope="cross_file")
    assert r2.result["pairs"] == []
    assert not sg.find_coupling(st, cov, scope="bogus").ok


def test_find_similar_behavior_mode(tmp_path):
    pytest.importorskip("numpy")
    st = _graph()
    # target and helper co-activate in every test; dyn activates alone -> in mode
    # space target's nearest neighbour is helper, not dyn
    rows = {f"t/test_x.py::test_{i}": ["m.py::target", "m.py::helper"] for i in range(4)}
    rows["t/test_x.py::test_solo"] = ["m.py::dyn"]
    cov = _cov(tmp_path, rows)
    r = sg.find_similar(st, "m.py::target", mode="behavior", coverage=cov)
    assert r.ok and r.result[0]["id"] == "m.py::helper"
    assert all(item["id"] != "m.py::dyn" or item["score"] <
               r.result[0]["score"] for item in r.result)
    # never-executed symbol refuses honestly
    assert not sg.find_similar(st, "m.py::hub", mode="behavior", coverage=cov).ok
