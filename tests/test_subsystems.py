"""Tests for spectral subsystem decomposition — `core/spectral.decompose` + the `find_subsystems`
operation (design §6, promoted from research/06-spectral). Advisory structural decomposition: it
partitions the call graph into its natural subsystems and auto-labels each. Never feeds liveness.

numpy is required (a hard test dep via [dev]); scipy may or may not be present, so the tests exercise
BOTH the sparse (scipy) and dense (numpy) solver paths by toggling `spectral.HAS_SCIPY`.
"""
from __future__ import annotations

import pytest

import stitchgraph as sg
from stitchgraph.core import spectral
from stitchgraph.core.model import Edge, Node, NodeKind, Relation

pytest.importorskip("numpy")


def _clique(prefix, n):
    return [f"m.py::{prefix}{i}" for i in range(n)]


def _two_communities():
    """Two 6-cliques joined by a single bridge edge — a textbook 2-way spectral split."""
    s = sg.Store(":memory:")
    a, b = _clique("a", 6), _clique("b", 6)
    for nid in a + b:
        s.add_node(Node(id=nid, kind=NodeKind.FUNCTION, name=nid.split("::")[-1],
                        location="m.py:1:0"))
    def link(u, v):
        s.add_edge(Edge(src=u, dst_id=v, dst_symbol=v.split("::")[-1], relation=Relation.CALLS))
    for grp in (a, b):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                link(grp[i], grp[j])
    link(a[0], b[0])  # the bridge
    return s, set(a), set(b)


def _purity(clusters, group_a):
    """Each cluster should be (almost) entirely one community."""
    tot = hit = 0
    for c in clusters:
        members = c["members"]
        in_a = sum(1 for m in members if m in group_a)
        hit += max(in_a, len(members) - in_a)
        tot += len(members)
    return hit / tot if tot else 0.0


@pytest.mark.parametrize("use_scipy", [True, False])
def test_recovers_two_planted_communities(use_scipy, monkeypatch):
    if use_scipy and not spectral.HAS_SCIPY:
        pytest.skip("scipy not installed")
    monkeypatch.setattr(spectral, "HAS_SCIPY", use_scipy)
    s, a, b = _two_communities()
    clusters, meta = spectral.decompose(s, k=2)
    assert len(clusters) == 2
    assert _purity(clusters, a) == 1.0  # the two cliques are cleanly separated
    assert meta["solver"] == ("scipy" if use_scipy else "numpy-dense")


def test_find_subsystems_operation_shape_and_determinism():
    s, _a, _b = _two_communities()
    r = sg.find_subsystems(s, k=2)
    assert r.ok and r.provenance.value == "extracted" and r.meta["k"] == 2
    assert len(r.result) == 2
    for c in r.result:
        assert c["size"] >= 1 and isinstance(c["label"], str) and c["exemplars"]
    # ranked largest-first
    assert [c["size"] for c in r.result] == sorted((c["size"] for c in r.result), reverse=True)
    # deterministic
    r2 = sg.find_subsystems(s, k=2)
    assert [(c["label"], c["size"]) for c in r.result] == [(c["label"], c["size"]) for c in r2.result]


def test_auto_k_picks_a_reasonable_count():
    s, _a, _b = _two_communities()
    r = sg.find_subsystems(s)  # k=None → eigengap
    assert r.ok and 2 <= r.meta["k"] <= 12


def test_labels_are_distinctive_tokens():
    # cluster A functions are all named parse_*, cluster B all render_* — labels should reflect that.
    s = sg.Store(":memory:")
    a = [f"m.py::parse_{w}" for w in ("args", "opts", "flags", "value", "token", "line")]
    b = [f"m.py::render_{w}" for w in ("html", "text", "cell", "row", "page", "node")]
    for nid in a + b:
        s.add_node(Node(id=nid, kind=NodeKind.FUNCTION, name=nid.split("::")[-1], location="m.py:1:0"))
    def link(u, v):
        s.add_edge(Edge(src=u, dst_id=v, dst_symbol=v.split("::")[-1], relation=Relation.CALLS))
    for grp in (a, b):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                link(grp[i], grp[j])
    link(a[0], b[0])
    r = sg.find_subsystems(s, k=2)
    labels = " ".join(c["label"] for c in r.result)
    assert "parse" in labels and "render" in labels


def test_find_subsystems_bad_input_and_tiny_graph_never_raise():
    assert sg.find_subsystems(sg.Store(":memory:")).ok            # empty
    s, _a, _b = _two_communities()
    assert sg.find_subsystems(s, k="nope").ok                     # type: ignore[arg-type]
    assert sg.find_subsystems(s, k=1).ok                          # k<2 → auto
    assert sg.find_subsystems(s, k=True).ok                       # bool not treated as k
    # a 3-node graph is below the minimum → empty clusters, still ok
    tiny = sg.Store(":memory:")
    for n in "abc":
        tiny.add_node(Node(id=f"t.py::{n}", kind=NodeKind.FUNCTION, name=n, location="t.py:1:0"))
    tiny.add_edge(Edge(src="t.py::a", dst_id="t.py::b", dst_symbol="b", relation=Relation.CALLS))
    assert sg.find_subsystems(tiny).ok


def test_dense_fallback_refuses_over_cap_without_scipy(monkeypatch):
    monkeypatch.setattr(spectral, "HAS_SCIPY", False)
    monkeypatch.setattr(spectral, "_DENSE_CAP", 8)
    s, _a, _b = _two_communities()  # 12-node giant > cap
    r = sg.find_subsystems(s)
    assert not r.ok and "spectral" in " ".join(r.review_reasons)


def test_find_subsystems_never_affects_liveness():
    import tempfile
    from pathlib import Path
    d = tempfile.mkdtemp()
    Path(d, "m.py").write_text(
        "def a():\n    return b()\n\ndef b():\n    return c()\n\ndef c():\n    return 1\n"
        "def orphan():\n    return 0\n")
    store = sg.Store(":memory:")
    sg.reindex(store, d)
    before = sg.find_stale(store)
    sg.find_subsystems(store)
    after = sg.find_stale(store)
    assert before.result == after.result
