"""Tests for the forward-looking POD-based coverage queries (v3.22.0): `select_tests` (which tests to
run for a change — runtime × static), `co_change` (what code moves together), and `find_coupling`
(implicit coupling: co-run but no static edge). Pure set math over the inert coverage matrix — no
numpy; advisory and read-only (stitchgraph never executes code, never mutates the graph)."""
from __future__ import annotations

import json

import stitchgraph as sg
from stitchgraph.core.model import Edge, Node, NodeKind, Relation


def _graph():
    """A tiny store: test_a -> helper -> target (static); test_c -> target; test_b unrelated.
    target and sibling have NO edge between them."""
    st = sg.Store(":memory:")
    for fid, name in [("m.py::target", "target"), ("m.py::helper", "helper"),
                      ("m.py::sibling", "sibling"), ("m.py::other", "other")]:
        st.add_node(Node(id=fid, kind=NodeKind.FUNCTION, name=name, location="m.py:1:0"))
    for tid, name in [("t/test_x.py::test_a", "test_a"), ("t/test_y.py::test_b", "test_b"),
                      ("t/test_z.py::test_c", "test_c")]:
        st.add_node(Node(id=tid, kind=NodeKind.FUNCTION, name=name, location=f"{tid}:1:0",
                         roles=frozenset({"test"})))
    def e(s, d):
        return Edge(src=s, relation=Relation.CALLS, dst_symbol=d.split("::")[-1], dst_id=d)
    st.add_edge(e("t/test_x.py::test_a", "m.py::helper"))
    st.add_edge(e("m.py::helper", "m.py::target"))
    st.add_edge(e("t/test_z.py::test_c", "m.py::target"))
    st.commit()
    return st


def _cov(tmp_path, tests):
    p = tmp_path / "cov.json"
    p.write_text(json.dumps({"format": "stitchgraph-coverage-v1", "tests": tests}))
    return str(p)


def test_select_tests_fuses_runtime_and_static(tmp_path):
    st = _graph()
    # test_a ran target (also static via helper); test_b ran target (no static edge); test_c is
    # static-only (reaches target but its coverage row doesn't include target).
    cov = _cov(tmp_path, {
        "t/test_x.py::test_a": ["m.py::target", "m.py::helper"],
        "t/test_y.py::test_b": ["m.py::target"],
        "t/test_z.py::test_c": ["m.py::other"],
    })
    r = sg.select_tests(st, "m.py::target", cov)
    assert r.ok and r.provenance.value == "extracted"
    assert set(r.result["ran_it"]) == {"t/test_x.py::test_a", "t/test_y.py::test_b"}
    assert r.result["both"] == ["t/test_x.py::test_a"]
    assert r.result["runtime_only"] == ["t/test_y.py::test_b"]     # ran it, static graph missed it
    assert r.result["static_only"] == ["t/test_z.py::test_c"]      # reachable but never ran it
    assert set(r.result["run_these"]) == {
        "t/test_x.py::test_a", "t/test_y.py::test_b", "t/test_z.py::test_c"}


def test_select_tests_normalizes_param_and_phase_keys(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        "t/test_x.py::test_a[case1]|run": ["m.py::target"],
        "t/test_x.py::test_a[case2]|run": ["m.py::target"],
    })
    r = sg.select_tests(st, "m.py::target", cov)
    # both parametrized rows collapse to the one base test id, which matches the static test node
    assert r.result["ran_it"] == ["t/test_x.py::test_a"]
    assert r.result["both"] == ["t/test_x.py::test_a"]


def test_select_tests_unrun_symbol_is_static_only(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {"t/test_x.py::test_a": ["m.py::other"]})  # nothing ran target
    r = sg.select_tests(st, "m.py::target", cov)
    assert r.ok and r.needs_review and r.result["ran_it"] == []
    # run_these is the static blast radius only: both test_a (via helper) and test_c reach target
    assert r.result["run_these"] == ["t/test_x.py::test_a", "t/test_z.py::test_c"]


def test_select_tests_bad_input_refuses(tmp_path):
    st = _graph()
    assert not sg.select_tests(st, "m.py::target", "/no/such.json").ok
    assert not sg.select_tests(st, "nope", str(_cov(tmp_path, {"t::a": ["m.py::target"]}))).ok
    assert not sg.select_tests(st, "m.py::target", 123).ok          # type: ignore[arg-type]


def test_base_test_id_normalizes_tricky_params():
    """Panel (v3.22.0): the phase suffix is stripped before the param group, and the param group is
    matched greedily — so a param containing '|' or nested brackets still collapses to the base id."""
    from stitchgraph.core.coverage_query import base_test_id
    assert base_test_id("t/test.py::test_re[a|b]|run") == "t/test.py::test_re"
    assert base_test_id("t/test.py::test_re[c|d]|run") == "t/test.py::test_re"
    assert base_test_id("t/test.py::test_x[a[b]]") == "t/test.py::test_x"
    assert base_test_id("t/test.py::TestC::test_m[x|y]|setup") == "t/test.py::TestC.test_m"
    assert base_test_id("t/test.py::test_plain") == "t/test.py::test_plain"


def test_co_change_ranks_coactivating_functions(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        "t/test_x.py::t1": ["m.py::target", "m.py::sibling"],
        "t/test_x.py::t2": ["m.py::target", "m.py::sibling"],
        "t/test_x.py::t3": ["m.py::target", "m.py::helper"],
    })
    r = sg.co_change(st, "m.py::target", cov)
    assert r.ok
    ranked = [(c["function"], c["shared_tests"]) for c in r.result["co_changing"]]
    # sibling shares 2 tests with target, helper shares 1 → sibling ranks first
    assert ranked[0][0] == "m.py::sibling" and ranked[0][1] == 2
    assert ("m.py::helper", 1) in ranked


def test_find_coupling_flags_edgeless_coactivation(tmp_path):
    st = _graph()
    # target & sibling co-run in 4 tests with NO edge between them (hidden); target & helper co-run
    # too but ARE statically linked (helper->target edge) → must be excluded.
    tests = {}
    for i in range(4):
        tests[f"t/test_x.py::t{i}"] = ["m.py::target", "m.py::sibling", "m.py::helper"]
    r = sg.find_coupling(st, _cov(tmp_path, tests), min_shared=3)
    assert r.ok and r.needs_review
    pairs = {frozenset((p["a"], p["b"])) for p in r.result["pairs"]}
    assert frozenset(("m.py::target", "m.py::sibling")) in pairs      # edgeless co-activation
    assert frozenset(("m.py::target", "m.py::helper")) not in pairs   # has a call edge → excluded
    assert frozenset(("m.py::helper", "m.py::target")) not in pairs


def test_find_coupling_bad_input_refuses(tmp_path):
    st = _graph()
    assert not sg.find_coupling(st, "/no/such.json").ok
    assert not sg.find_coupling(st, 123).ok                          # type: ignore[arg-type]


def test_select_tests_changeset_unions(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        "t/test_x.py::test_a": ["m.py::target"],
        "t/test_y.py::test_b": ["m.py::sibling"],
    })
    r = sg.select_tests(st, "m.py::target, m.py::sibling", cov)
    assert r.ok and set(r.result["symbols"]) == {"m.py::target", "m.py::sibling"}
    assert set(r.result["ran_it"]) == {"t/test_x.py::test_a", "t/test_y.py::test_b"}
    # an unresolvable changeset member is noted, not fatal
    r2 = sg.select_tests(st, "m.py::target, m.py::nope", cov)
    assert r2.ok and r2.result["unresolved"] == ["m.py::nope"] and r2.needs_review


def test_test_order_is_fail_fast_cover(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        "t::wide": ["m.py::target", "m.py::helper", "m.py::sibling"],
        "t::narrow": ["m.py::target"],
        "t::other": ["m.py::other"],
    })
    r = sg.test_order(st, cov)
    assert r.ok and r.result["order"][0]["test"] == "t::wide"      # most new coverage first
    assert r.result["order"][0]["new_functions"] == 3
    # minimal prefix covers all executed functions; remaining tests add zero
    assert r.result["minimal_count"] == 2                          # wide (3) + other (1) = all 4
    assert r.result["order"][-1]["new_functions"] == 0


def test_redundant_tests_groups_identical_profiles(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        "t::a": ["m.py::target", "m.py::helper"],
        "t::b": ["m.py::target", "m.py::helper"],   # identical profile to a
        "t::c": ["m.py::sibling"],
    })
    r = sg.redundant_tests(st, cov)
    assert r.ok and r.needs_review
    assert r.result["groups"][0]["tests"] == ["t::a", "t::b"]
    assert r.result["redundant_tests"] == 1


def test_find_core_ranks_by_activation_frequency(tmp_path):
    st = _graph()
    cov = _cov(tmp_path, {
        "t::a": ["m.py::target", "m.py::helper"],
        "t::b": ["m.py::target"],
        "t::c": ["m.py::target", "m.py::sibling"],
    })
    r = sg.find_core(st, cov, limit=3)
    assert r.ok
    top = r.result["core"][0]
    assert top["function"] == "m.py::target" and top["test_count"] == 3 and top["fraction"] == 1.0


def test_find_gaps_partitions_untested_and_excludes_exercised(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "m.py").write_text(
        "def live():\n    return 1\n\ndef helper():\n    return live()\n\n"
        "def orphan():\n    return 2\n\nhelper()\n")
    st = sg.Store(":memory:")
    sg.reindex(st, str(d))
    cov = _cov(tmp_path, {f"t::t{i}": ["m.py::live"] for i in range(4)})  # only live() exercised
    r = sg.find_gaps(st, cov)
    assert r.ok and r.needs_review
    buckets = set(r.result["untested_live"]) | set(r.result["untested_dead"])
    assert "m.py::live" not in buckets                       # exercised → not a gap
    assert {"m.py::helper", "m.py::orphan"} <= buckets       # untested → in one bucket each
    # every function is either tested or in exactly one untested bucket
    assert (r.result["tested"] + len(r.result["untested_live"])
            + len(r.result["untested_dead"])) == r.result["total_functions"]
    assert len(set(r.result["untested_live"]) & set(r.result["untested_dead"])) == 0


def test_tier_a_bad_input_refuses(tmp_path):
    st = _graph()
    for op in (sg.find_gaps, sg.test_order, sg.redundant_tests, sg.find_core):
        assert not op(st, "/no/such.json").ok
        assert not op(st, 123).ok               # type: ignore[arg-type]


def test_coverage_drift_reports_gained_and_lost(tmp_path):
    st = _graph()
    old = _cov(tmp_path, {"t::a": ["m.py::target", "m.py::helper"]})
    new_p = tmp_path / "new.json"
    new_p.write_text(json.dumps({"format": "stitchgraph-coverage-v1",
                                 "tests": {"t::a": ["m.py::target", "m.py::sibling"]}}))
    r = sg.coverage_drift(st, old, str(new_p))
    assert r.ok
    assert r.result["gained_coverage"] == ["m.py::sibling"]   # newly exercised
    assert r.result["lost_coverage"] == ["m.py::helper"]      # no longer exercised
    assert r.result["gained"] == 1 and r.result["lost"] == 1


def test_coverage_drift_bad_input_refuses(tmp_path):
    st = _graph()
    good = _cov(tmp_path, {"t::a": ["m.py::target"]})
    assert not sg.coverage_drift(st, "/no/old.json", good).ok
    assert not sg.coverage_drift(st, good, "/no/new.json").ok
    assert not sg.coverage_drift(st, 123, good).ok            # type: ignore[arg-type]


def test_runtime_risk_bad_input_refuses(tmp_path):
    st = _graph()
    assert not sg.runtime_risk(st, "/no/such.json").ok        # unusable coverage
    assert not sg.runtime_risk(st, 123).ok                    # type: ignore[arg-type]
    # a non-git path refuses cleanly
    good = _cov(tmp_path, {"t::a": ["m.py::target"]})
    assert not sg.runtime_risk(st, good, path=str(tmp_path)).ok


def test_cardinal_query_ops_lazy_and_liveness_untouched(tmp_path):
    import subprocess
    import sys
    code = ("import sys, stitchgraph; "
            "assert 'stitchgraph.core.coverage_query' not in sys.modules; "
            "print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr

    d = tmp_path / "proj"
    d.mkdir()
    (d / "m.py").write_text("def a():\n    return b()\n\ndef b():\n    return 1\n")
    store = sg.Store(":memory:")
    sg.reindex(store, str(d))
    before = sg.find_stale(store).result
    cov = _cov(tmp_path, {"t/test_x.py::t1": ["m.py::a", "m.py::b"],
                          "t/test_x.py::t2": ["m.py::a", "m.py::b"],
                          "t/test_x.py::t3": ["m.py::a", "m.py::b"]})
    sg.select_tests(store, "m.py::a", cov)
    sg.co_change(store, "m.py::a", cov)
    sg.find_coupling(store, cov)
    sg.find_gaps(store, cov)
    sg.test_order(store, cov)
    sg.redundant_tests(store, cov)
    sg.find_core(store, cov)
    sg.coverage_drift(store, cov, cov)
    assert sg.find_stale(store).result == before
