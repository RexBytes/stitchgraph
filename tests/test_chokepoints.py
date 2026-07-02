"""Tests for articulation-point criticality — `reach.articulation_points` + the `find_chokepoints`
operation (design §6, promoted from research/06-spectral). Advisory structural criticality: cut
vertices whose removal fragments the graph, ranked by blast radius. Never feeds liveness.
"""
from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core import reach
from stitchgraph.core.model import Edge, Node, NodeKind, Relation


def _store(nodes, edges):
    s = sg.Store(":memory:")
    for n in nodes:
        s.add_node(Node(id=f"m.py::{n}", kind=NodeKind.FUNCTION, name=n, location="m.py:1:0"))
    for u, v in edges:
        s.add_edge(Edge(src=f"m.py::{u}", dst_id=f"m.py::{v}", dst_symbol=v, relation=Relation.CALLS))
    return s


def test_articulation_points_chain_and_star():
    # a-b-c-d-e-{f,g,h} (8 nodes): b,c,d,e are cut vertices; the leaves a,f,g,h are not.
    s = _store("abcdefgh", [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"),
                            ("e", "f"), ("e", "g"), ("e", "h")])
    ap = reach.articulation_points(s)
    assert set(k.split("::")[-1] for k in ap) == {"b", "c", "d", "e"}
    # blast radius = nodes cut off from the MAIN body (the largest surviving component), so it is
    # `(total-1) - max(piece)`, NOT the raw far-side subtree (R263: the raw-subtree version inflated
    # and inverted the ranking). Removing b splits into {a}=1 and {c..h}=6 → cut off = 1; etc.
    assert ap["m.py::b"] == 1 and ap["m.py::c"] == 2 and ap["m.py::d"] == 3 and ap["m.py::e"] == 3


def test_articulation_points_none_in_a_cycle():
    # a triangle has no cut vertex — removing any single node leaves the rest connected.
    s = _store("abc", [("a", "b"), ("b", "c"), ("c", "a")])
    assert reach.articulation_points(s) == {}


def test_articulation_points_deterministic():
    s = _store("abcdefgh", [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"),
                            ("e", "f"), ("e", "g"), ("e", "h")])
    assert reach.articulation_points(s) == reach.articulation_points(s)


def test_find_chokepoints_operation_ranks_by_blast_radius():
    s = _store("abcdefgh", [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"),
                            ("e", "f"), ("e", "g"), ("e", "h")])
    r = sg.find_chokepoints(s)
    assert r.ok and r.provenance.value == "extracted"
    blasts = [it["blast_radius"] for it in r.result]
    assert blasts == sorted(blasts, reverse=True)  # ranked, descending
    # d and e both cut off 3; the deepest bridges rank above the near-leaf b (blast 1).
    assert r.result[0]["blast_radius"] == 3 and r.result[0]["name"] in ("d", "e")
    assert r.meta.get("chokepoints") == 4


def test_blast_radius_is_symmetric_on_a_chain():
    # R263 regression: on a symmetric chain the blast radius must PEAK AT THE CENTRE (a central cut
    # vertex strands ~half the graph), not grow monotonically toward a leaf. The pre-fix code
    # returned a staircase and reported a near-leaf node as the top chokepoint.
    s = _store("0123456789", [(str(i), str(i + 1)) for i in range(9)])
    ap = {k.split("::")[-1]: v for k, v in reach.articulation_points(s).items()}
    assert ap == {"1": 1, "2": 2, "3": 3, "4": 4, "5": 4, "6": 3, "7": 2, "8": 1}
    assert max(ap.values()) == 4 and ap["4"] == ap["5"] == 4  # peak at the two centre nodes


def test_find_chokepoints_limit_and_bad_input():
    s = _store("abcdefgh", [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"),
                            ("e", "f"), ("e", "g"), ("e", "h")])
    assert len(sg.find_chokepoints(s, limit=2).result) == 2
    # bad limit / empty store never raise, always a Result
    assert sg.find_chokepoints(s, limit="nope").ok      # type: ignore[arg-type]
    assert sg.find_chokepoints(s, limit=0).ok
    assert sg.find_chokepoints(sg.Store(":memory:")).ok
    assert sg.find_chokepoints(sg.Store(":memory:")).result == []


def test_find_chokepoints_excludes_pseudo_nodes():
    # a Module node that is a graph cut vertex must not be labelled a chokepoint (code entities
    # only, matching orient/scan) — parity with panel R14A.
    s = sg.Store(":memory:")
    s.add_node(Node(id="pkg::mod", kind=NodeKind.MODULE, name="mod", location="mod.py:1:0"))
    for n in "abc":
        s.add_node(Node(id=f"mod.py::{n}", kind=NodeKind.FUNCTION, name=n, location="mod.py:1:0"))
    # mod bridges a<->{b,c}; if it were code it'd be a chokepoint, but it's a Module.
    for u, v in [("mod.py::a", "pkg::mod"), ("pkg::mod", "mod.py::b"), ("pkg::mod", "mod.py::c")]:
        s.add_edge(Edge(src=u, dst_id=v, dst_symbol=v.split("::")[-1], relation=Relation.CALLS))
    assert all(it["id"] != "pkg::mod" for it in sg.find_chokepoints(s).result)


def test_find_chokepoints_never_affects_liveness():
    # cardinal rule: an advisory criticality read must not change what find_stale reports.
    import tempfile
    from pathlib import Path
    d = tempfile.mkdtemp()
    Path(d, "m.py").write_text(
        "def a():\n    return b()\n\ndef b():\n    return c()\n\ndef c():\n    return 1\n"
        "def orphan():\n    return 0\n")
    store = sg.Store(":memory:")
    sg.reindex(store, d)
    before = sg.find_stale(store)
    sg.find_chokepoints(store)
    after = sg.find_stale(store)
    assert before.result == after.result
