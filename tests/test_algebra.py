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
