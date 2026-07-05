"""GraphBLAS algebra layer: must agree with the pure-Python reference."""

from __future__ import annotations

import pytest

import stitchgraph as sg
from stitchgraph.core import reach
from stitchgraph.core.model import Edge, Node, NodeKind, Relation

algebra = pytest.importorskip("stitchgraph.core.algebra")
if not algebra.HAS_GRAPHBLAS:
    pytest.skip("graphblas not installed", allow_module_level=True)


def _chain_graph() -> sg.Store:
    """a -> b -> c -> d ; e isolated ; b -> e (so e is reachable from a)."""
    store = sg.Store(":memory:")
    for name in "abcde":
        store.add_node(Node(f"m.py::{name}", NodeKind.FUNCTION, name))
    def call(s, d):
        store.add_edge(Edge(f"m.py::{s}", Relation.CALLS, d, dst_id=f"m.py::{d}"))
    for s, d in [("a","b"),("b","c"),("c","d"),("b","e")]:
        call(s, d)
    store.commit()
    return store


def _pure_reach(store, seeds):
    # bypass the graphblas dispatch to get the reference BFS
    adj = reach._adjacency(store, reach.LIVENESS_RELATIONS)
    seen, frontier = set(seeds), list(seeds)
    while frontier:
        nxt = frontier.pop()
        for w in adj.get(nxt, ()):
            if w not in seen:
                seen.add(w)
                frontier.append(w)
    return seen


def test_graphblas_reach_matches_reference():
    with _chain_graph() as store:
        gb_set = algebra.reachable_from(store, {"m.py::a"})
        ref = _pure_reach(store, {"m.py::a"})
        assert gb_set == ref == {"m.py::a", "m.py::b", "m.py::c", "m.py::d", "m.py::e"}


def test_graphblas_isolated_seed():
    with _chain_graph() as store:
        assert algebra.reachable_from(store, {"m.py::d"}) == {"m.py::d"}


def test_reverse_reachable_blast_radius():
    with _chain_graph() as store:
        # who can reach c?  a and b (not c itself).
        deps = algebra.reverse_reachable_from(store, {"m.py::c"})
        assert deps == {"m.py::a", "m.py::b"}


def test_pagerank_ranks_sink_highest():
    with _chain_graph() as store:
        ranks = algebra.pagerank(store)
        assert ranks  # non-empty
        top = max(ranks, key=ranks.get)
        # d and c accumulate rank (everything flows toward them)
        assert top in {"m.py::d", "m.py::c"}


def test_transitive_fan_in_ranks_sink_highest():
    with _chain_graph() as store:
        tfi = algebra.transitive_fan_in(store)
        # d is reachable from a, b, c -> highest transitive fan-in; a from none.
        assert tfi["m.py::d"] == 3          # a, b, c reach d
        assert tfi.get("m.py::a", 0) == 0   # nothing reaches a


def test_reach_dispatch_uses_graphblas_consistently():
    with _chain_graph() as store:
        # reach.reachable_from should route through graphblas and still be correct
        assert reach.reachable_from(store, {"m.py::a"}) == _pure_reach(store, {"m.py::a"})


def _homonym_graph() -> sg.Store:
    """`hub` has 3 EXTRACTED dependers; `homonym` has 6 AMBIGUOUS widening arms.
    Raw centrality ranks the homonym first; confident-only must invert that."""
    from stitchgraph.core.envelope import Provenance

    store = sg.Store(":memory:")
    for name in ["hub", "homonym", *(f"c{i}" for i in range(6))]:
        store.add_node(Node(f"m.py::{name}", NodeKind.FUNCTION, name))
    for i in range(3):
        store.add_edge(Edge(f"m.py::c{i}", Relation.CALLS, "hub",
                            dst_id="m.py::hub", provenance=Provenance.EXTRACTED))
    for i in range(6):
        store.add_edge(Edge(f"m.py::c{i}", Relation.CALLS, "homonym",
                            dst_id="m.py::homonym", provenance=Provenance.AMBIGUOUS))
    store.commit()
    return store


def test_transitive_fan_in_confident_only_discounts_homonyms():
    """v3.32.0: the hub metrics rank over EXTRACTED edges by default — the same
    discount confident_fan_in applies. Falsified by the raw variant: with
    confident_only=False the homonym's 6 ambiguous arms win."""
    with _homonym_graph() as store:
        confident = algebra.transitive_fan_in(store)
        raw = algebra.transitive_fan_in(store, confident_only=False)
        assert confident["m.py::hub"] > confident.get("m.py::homonym", 0)
        assert raw["m.py::homonym"] > raw["m.py::hub"]


def test_pagerank_confident_only_discounts_homonyms():
    with _homonym_graph() as store:
        confident = algebra.pagerank(store)
        raw = algebra.pagerank(store, confident_only=False)
        assert confident["m.py::hub"] > confident["m.py::homonym"]
        assert raw["m.py::homonym"] > raw["m.py::hub"]


def test_liveness_sweeps_stay_raw():
    """An AMBIGUOUS edge must still propagate liveness — reachability never uses
    the confident-only matrix (precision-over-recall on dead code)."""
    with _homonym_graph() as store:
        reached = algebra.reachable_from(store, {"m.py::c5"})
        assert "m.py::homonym" in reached  # only an AMBIGUOUS edge leads there
