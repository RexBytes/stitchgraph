"""Pin the pure-Python reachability sweeps (reach.py) directly — they are the reference
implementation and the path the core-only install (no GraphBLAS) actually runs, but with the
`algebra` extra installed the GraphBLAS sweep shadows them, so the rest of the suite never
exercises `_adjacency` / `_reverse_adjacency` / `fan_in` / `fan_out`. These tests force the
pure-Python path (GraphBLAS disabled) and assert exact answers on a tiny known graph, so the
v2.1.0 lean-streaming rewrite of those functions is mutation-pinned, not just oracle-checked.

The fixture deliberately includes the cases that distinguish the guard conditions:
  * a non-liveness relation (WRITES) to a real node — excluded by `rel in rels`;
  * a dangling edge (CALLS to a non-node) — excluded by `dst in nodes`;
  * INFERRED edges — toggled by an `edge_filter`.
so an `and`->`or` or a flipped membership test changes the answer (kills those mutants).
"""
from __future__ import annotations

import pytest

import stitchgraph as sg
from stitchgraph.core import reach
from stitchgraph.core.envelope import Provenance
from stitchgraph.core.model import Relation


@pytest.fixture
def no_graphblas(monkeypatch):
    # Force reach.py's pure-Python fallback regardless of whether python-graphblas is installed.
    monkeypatch.setattr(reach, "_graphblas", lambda: None)


# (src, relation, dst, provenance) — dst 'ghost' is intentionally NOT a node (dangling).
_NODES = ("a", "b", "c", "d", "e", "w", "x")
_EDGES = [
    ("a", "CALLS", "b", "extracted"),
    ("b", "CALLS", "c", "inferred"),       # liveness, but INFERRED (edge_filter toggles it)
    ("a", "CALLS", "d", "extracted"),
    ("a", "REFERENCES", "c", "inferred"),  # second path to c, also INFERRED
    ("a", "WRITES", "w", "extracted"),     # non-liveness relation -> never propagates liveness
    ("a", "CALLS", "ghost", "extracted"),  # dangling: 'ghost' is not a node
    ("x", "WRITES", "c", "extracted"),     # non-liveness inbound to c (reverse-reach guard)
]


def _graph():
    store = sg.Store(":memory:")
    with store.conn:
        for n in _NODES:
            store.conn.execute(
                "INSERT INTO nodes(id,kind,name,location,roles) VALUES(?,?,?,?,?)",
                (f"m::{n}", "Function", n, "x:1:0", ""))
        for src, rel, dst, prov in _EDGES:
            store.conn.execute(
                "INSERT INTO edges(src,relation,dst_symbol,dst_id,weight,provenance,"
                "location,source,file,name_based) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"m::{src}", rel, dst, f"m::{dst}", 1.0, prov, "x:1:0", "ts", "m", 0))
    return store


def test_pure_reachable_from(no_graphblas):
    with _graph() as store:
        # liveness only: w (WRITES) and ghost (dangling) are excluded.
        assert reach.reachable_from(store, ["m::a"]) == {"m::a", "m::b", "m::c", "m::d"}
        assert reach.reachable_from(store, ["m::b"]) == {"m::b", "m::c"}
        assert reach.reachable_from(store, ["m::e"]) == {"m::e"}  # isolated


def test_pure_reachable_respects_relation_filter(no_graphblas):
    with _graph() as store:
        assert reach.reachable_from(store, ["m::a"], (Relation.CALLS,)) == {
            "m::a", "m::b", "m::c", "m::d"}
        assert reach.reachable_from(store, ["m::a"], (Relation.INHERITS,)) == {"m::a"}


def test_pure_reachable_with_edge_filter_extracted_only(no_graphblas):
    """The `edge_filter` (`else`) branch: EXTRACTED-only drops both INFERRED paths to c, so c
    is unreachable; w/ghost stay excluded by the relation/node guards."""
    extracted = lambda e: e.provenance is Provenance.EXTRACTED  # noqa: E731
    with _graph() as store:
        assert reach.reachable_from(store, ["m::a"], edge_filter=extracted) == {
            "m::a", "m::b", "m::d"}


def test_pure_reverse_reachable_from(no_graphblas):
    with _graph() as store:
        # who reaches c via liveness? a (REFERENCES) and b (CALLS). x (WRITES) is excluded.
        assert reach.reverse_reachable_from(store, ["m::c"]) == {"m::a", "m::b"}
        assert reach.reverse_reachable_from(store, ["m::d"]) == {"m::a"}
        assert reach.reverse_reachable_from(store, ["m::e"]) == set()


def test_fan_in_counts():
    with _graph() as store:
        fi = reach.fan_in(store)
        assert fi["m::c"] == 2          # b->c (CALLS) + a->c (REFERENCES); x->c (WRITES) excluded
        assert fi["m::b"] == 1
        assert fi["m::d"] == 1
        assert "m::w" not in fi          # only inbound is WRITES (non-liveness)
        assert "m::a" not in fi


def test_fan_out_counts():
    with _graph() as store:
        fo = reach.fan_out(store)        # CALLS only by default
        assert fo["m::a"] == 2           # a->b, a->d; a->ghost dangling + WRITES excluded
        assert fo["m::b"] == 1
        assert "m::c" not in fo


def test_find_stale_uses_pure_path(no_graphblas):
    """End-to-end find_stale on the pure-Python path: with `a` the only root, e is dead."""
    with _graph() as store:
        store.conn.execute("UPDATE nodes SET roles='main' WHERE id='m::a'")
        store.conn.commit()
        stale = {c["id"] for c in (sg.find_stale(store).result or [])}
        assert "m::e" in stale
        assert "m::a" not in stale and "m::c" not in stale
