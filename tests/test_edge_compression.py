"""Homonym-group edge compression (research/20), the store primitives.

The three primitives (`_intern_set` / `_compress_edges` / `_expand_groups`) are
pure representation changes: the row multiset seen through `edges_all` must be
IDENTICAL before and after each one, and every read API must be blind to which
representation a widening happens to be in. These tests pin that contract plus
the eligibility gates (non-uniform groups stay flat) and the hot probes'
query plans (the UNION-ALL view must keep indexed access on both branches).
"""
from __future__ import annotations

import textwrap

import stitchgraph as sg
from stitchgraph.core import reach
from stitchgraph.core.model import Edge, NodeKind, Provenance, Relation

CORPUS = {
    "a.py": """
        class Base:
            def work(self):
                return 1

        class Sub(Base):
            def work(self):
                return 2
    """,
    "b.py": """
        from a import Base

        def run(obj):
            return obj.work()

        def helper():
            return run(Base())
    """,
    "c.py": """
        import b

        def main():
            return b.helper()

        def work():
            return 3
    """,
}


def _index(tmp_path):
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    for rel, content in CORPUS.items():
        (root / rel).write_text(textwrap.dedent(content))
    store = sg.Store(str(tmp_path / "idx.db"))
    assert sg.reindex(store, str(root)).ok
    return store


def _multiset(store):
    """The full resolved-edge row multiset through the view — the invariant."""
    rows = store.conn.execute(
        """SELECT src, relation, dst_symbol, dst_id, weight, provenance,
                  location, source, file, name_based FROM edges_all""").fetchall()
    return sorted(tuple(r) for r in rows)


def _api_snapshot(store):
    return {
        "resolved": sorted((e.src, e.relation.value, e.dst_id, e.weight,
                            e.provenance.value, e.name_based)
                           for e in store.resolved_edges()),
        "iter": sorted(store.iter_resolved()),
        "reach": reach.reachable_from(
            store, sorted(n.id for n in store.nodes_by_kind(NodeKind.MODULE))),
        "fan_in": dict(reach.fan_in(store)),
        "fan_out": dict(reach.fan_out(store)),
    }


def _ambiguous_flat_count(store):
    return store.conn.execute(
        """SELECT COUNT(*) FROM edges
            WHERE dst_id IS NOT NULL AND provenance='ambiguous'""").fetchone()[0]


def test_compress_expand_round_trip(tmp_path):
    store = _index(tmp_path)
    before_rows = _multiset(store)
    before_api = _api_snapshot(store)
    assert _ambiguous_flat_count(store) >= 2, "corpus must have a widened group"

    made = store._compress_edges()
    store.commit()
    assert made >= 1, "the obj.work() widening must be eligible"
    n_groups = store.conn.execute("SELECT COUNT(*) FROM edge_groups").fetchone()[0]
    assert n_groups == made
    # representation changed, contract identical
    assert _multiset(store) == before_rows
    assert _api_snapshot(store) == before_api

    keys = store._expand_groups("1=1")
    store.commit()
    assert len(keys) == made
    assert store.conn.execute("SELECT COUNT(*) FROM edge_groups").fetchone()[0] == 0
    assert _multiset(store) == before_rows
    assert _api_snapshot(store) == before_api

    # re-compress the exact expanded keys (the expand-affected re-entry path)
    assert store._compress_edges(srcs=[k[0] for k in keys]) == made
    assert _multiset(store) == before_rows


def test_intern_is_content_addressed(tmp_path):
    store = sg.Store(":memory:")
    a = store._intern_set(["x.py::f", "y.py::f"])
    b = store._intern_set(["y.py::f", "x.py::f"])  # order-blind
    c = store._intern_set(["x.py::f", "z.py::f"])
    assert a == b and a != c
    n = store.conn.execute("SELECT COUNT(*) FROM cand_sets").fetchone()[0]
    assert n == 2


def test_non_uniform_group_stays_flat():
    """The eligibility gates: mixed weight (an override row sharing the key with
    widened arms) must never be interned — those rows keep today's flat form."""
    store = sg.Store(":memory:")
    def edge(dst, w):
        return Edge(src="m.py::caller", relation=Relation.CALLS, dst_symbol="work",
                    dst_id=dst, weight=w, provenance=Provenance.AMBIGUOUS,
                    location="m.py:1:0", source="ast", name_based=True)
    store.add_edge(edge("a.py::A.work", 0.5))
    store.add_edge(edge("b.py::B.work", 0.5))
    store.add_edge(edge("c.py::C.work", 1.0))  # override-propagation weight
    store.commit()
    assert store._compress_edges() == 0
    assert store.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 3


def test_wipe_edges_clears_groups(tmp_path):
    store = _index(tmp_path)
    store._compress_edges()
    store.commit()
    store.wipe_edges()
    store.commit()
    for t in ("edges", "edge_groups", "cand_members", "cand_sets"):
        assert store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0


def test_gc_cand_sets(tmp_path):
    store = _index(tmp_path)
    store._compress_edges()
    store._expand_groups("1=1")  # leaves orphaned sets behind
    assert store.conn.execute("SELECT COUNT(*) FROM cand_sets").fetchone()[0] > 0
    store._gc_cand_sets()
    assert store.conn.execute("SELECT COUNT(*) FROM cand_sets").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM cand_members").fetchone()[0] == 0


def test_hot_probes_stay_indexed(tmp_path):
    """The four hot probe shapes must keep indexed plans on BOTH view branches —
    a full-scan regression here is the 16M-edge planner trap all over again."""
    store = _index(tmp_path)
    store._compress_edges()
    probes = [
        ("SELECT * FROM edges_all WHERE src = ?", ("b.py::run",)),
        ("SELECT * FROM edges_all WHERE dst_id = ?", ("a.py::Base.work",)),
        ("SELECT 1 FROM edges_all WHERE src=? AND dst_id=? LIMIT 1",
         ("b.py::run", "a.py::Base.work")),
    ]
    for sql, params in probes:
        plan = "\n".join(r[3] for r in store.conn.execute(
            f"EXPLAIN QUERY PLAN {sql}", params))
        assert "SCAN edges" not in plan, f"flat branch degraded to a scan:\n{plan}"
        assert "SCAN edge_groups" not in plan, f"group branch scan:\n{plan}"


def test_old_db_gains_view_on_open(tmp_path):
    """An index file created before the compression schema must open cleanly and
    serve the view (empty group tables = exactly the old behaviour)."""
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("""CREATE TABLE nodes (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL,
        relation TEXT NOT NULL, dst_symbol TEXT NOT NULL, dst_id TEXT)""")
    conn.execute("""INSERT INTO edges(src, relation, dst_symbol, dst_id)
                    VALUES ('a.py::f', 'CALLS', 'g', 'b.py::g')""")
    conn.commit()
    conn.close()
    store = sg.Store(str(db))  # migration must backfill THEN create the view
    rows = store.conn.execute("SELECT src, dst_id FROM edges_all").fetchall()
    assert [(r["src"], r["dst_id"]) for r in rows] == [("a.py::f", "b.py::g")]
    store.close()
