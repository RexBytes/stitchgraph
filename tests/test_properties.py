"""Property / invariant tests (Hypothesis).

These assert *promises* over many generated inputs rather than one happy path:
- the GraphBLAS reachability sweep agrees with the pure-Python reference on every
  random graph (two independent implementations must never diverge);
- find_stale's precision invariant: nothing reachable from a root is ever flagged;
- the Result envelope's provenance-gates-urgency and needs_review contracts;
- node-id round-tripping.
"""

from __future__ import annotations

import pytest

import stitchgraph as sg
from stitchgraph.core import reach
from stitchgraph.core.envelope import REVIEW_THRESHOLD, Provenance, Result, Urgency
from stitchgraph.core.model import Edge, Node, NodeKind, Relation

pytest.importorskip("hypothesis")  # optional dev dep — core-only runs skip these
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# A random directed graph: n nodes (m0..m{n-1}), a set of distinct edges.
_graphs = st.integers(min_value=1, max_value=8).flatmap(
    lambda n: st.tuples(
        st.just(n),
        st.sets(st.tuples(st.integers(0, n - 1), st.integers(0, n - 1)), max_size=20),
    )
)


def _build(n: int, edges: set[tuple[int, int]]) -> sg.Store:
    store = sg.Store(":memory:")
    for i in range(n):
        store.add_node(Node(f"m.py::f{i}", NodeKind.FUNCTION, f"f{i}"))
    for a, b in edges:
        store.add_edge(Edge(f"m.py::f{a}", Relation.CALLS, f"f{b}", dst_id=f"m.py::f{b}"))
    store.commit()
    return store


def _pure_reach(store, seeds):
    adj = reach._adjacency(store, reach.LIVENESS_RELATIONS)
    seen, frontier = set(seeds), list(seeds)
    while frontier:
        for w in adj.get(frontier.pop(), ()):
            if w not in seen:
                seen.add(w)
                frontier.append(w)
    return seen


@settings(max_examples=120, deadline=None)
@given(_graphs, st.integers(0, 7))
def test_graphblas_reachability_matches_reference(graph, seed):
    pytest_algebra = __import__("stitchgraph.core.algebra", fromlist=["x"])
    if not pytest_algebra.HAS_GRAPHBLAS:
        return
    n, edges = graph
    seed = seed % n
    with _build(n, edges) as store:
        s = {f"m.py::f{seed}"}
        assert pytest_algebra.reachable_from(store, s) == _pure_reach(store, s)


@settings(max_examples=120, deadline=None)
@given(_graphs, st.integers(0, 7))
def test_reverse_reachable_is_inverse(graph, target):
    algebra = __import__("stitchgraph.core.algebra", fromlist=["x"])
    if not algebra.HAS_GRAPHBLAS:
        return
    n, edges = graph
    target = target % n
    with _build(n, edges) as store:
        tid = f"m.py::f{target}"
        # a is in reverse-reachable(b)  <=>  b is in reachable(a)
        deps = algebra.reverse_reachable_from(store, {tid})
        for a in range(n):
            aid = f"m.py::f{a}"
            if aid == tid:
                continue
            assert (aid in deps) == (tid in algebra.reachable_from(store, {aid}))


@settings(max_examples=80, deadline=None)
@given(_graphs, st.sets(st.integers(0, 7), max_size=3))
def test_find_stale_never_flags_reachable(graph, roots):
    n, edges = graph
    roots = {r % n for r in roots} or {0}
    with _build(n, edges) as store:
        from stitchgraph.core.entrypoints import ConfigOnlyDetector
        det = ConfigOnlyDetector({f"m.py::f{r}" for r in roots})
        reachable = reach.reachable_from(store, {f"m.py::f{r}" for r in roots})
        flagged = {c["id"] for c in sg.find_stale(store, detector=det).result}
        # PRECISION: nothing reachable from a declared root may be flagged stale.
        assert flagged & reachable == set()


@given(st.floats(0.0, 1.0), st.sampled_from(list(Provenance)),
       st.sampled_from(list(Urgency)))
def test_envelope_contracts(confidence, provenance, urgency):
    r = Result(ok=True, result=None, confidence=confidence, provenance=provenance,
               urgency=urgency)
    # needs_review fires below threshold OR on ambiguous provenance.
    if confidence < REVIEW_THRESHOLD or provenance is Provenance.AMBIGUOUS:
        assert r.needs_review
    # provenance gates the urgency ceiling: only EXTRACTED may stay red.
    if urgency is Urgency.RED and provenance is not Provenance.EXTRACTED:
        assert r.urgency is Urgency.ORANGE


@given(st.text(alphabet="abcdefghijklmnop/._", min_size=1, max_size=20),
       st.text(alphabet="abcdefghijklmnop._", min_size=1, max_size=20))
def test_node_id_roundtrip(path, qual):
    nid = Node.make_id(path, qual)
    assert nid.split("::", 1)[0] == path  # owning file recoverable from the id
