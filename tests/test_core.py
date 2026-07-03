"""Core tests — stdlib-only, no optional deps required."""

from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core.entrypoints import ConfigOnlyDetector
from stitchgraph.core.model import Edge, Node, NodeKind, Relation


def _node(path: str, qname: str, kind=NodeKind.FUNCTION, **kw) -> Node:
    return Node(id=Node.make_id(path, qname), kind=kind, name=qname.split(".")[-1], **kw)


def build_graph() -> sg.Store:
    """main -> service -> repo ;  repo calls a missing helper (a hole)."""
    store = sg.Store(":memory:")
    main = _node("app/cli.py", "main")
    service = _node("app/svc.py", "save")
    repo = _node("app/repo.py", "persist")
    orphan = _node("app/old.py", "legacy")  # reachable from nothing
    for n in (main, service, repo, orphan):
        store.add_node(n)
    store.add_edge(Edge(main.id, Relation.CALLS, "save", dst_id=service.id))
    store.add_edge(Edge(service.id, Relation.CALLS, "persist", dst_id=repo.id))
    store.add_edge(Edge(repo.id, Relation.CALLS, "missing_helper", dst_id=None))  # hole
    store.commit()
    return store


def test_find_symbol_unique():
    with build_graph() as store:
        res = sg.find_symbol(store, "save")
        assert res.ok and res.confidence == 1.0
        assert res.result["id"] == "app/svc.py::save"
        assert not res.needs_review


def test_find_symbol_missing_refuses():
    with build_graph() as store:
        res = sg.find_symbol(store, "nope")
        assert not res.ok and res.needs_review


def test_callers_and_callees():
    with build_graph() as store:
        callers = sg.get_callers(store, "save")
        assert callers.result[0]["src"] == "app/cli.py::main"
        callees = sg.get_callees(store, "save")
        assert callees.result[0]["dst"] == "app/repo.py::persist"


def test_find_holes_finds_dangling():
    with build_graph() as store:
        res = sg.find_holes(store)
        assert res.meta["count"] == 1
        assert res.result[0]["missing"] == "missing_helper"
        assert res.urgency is sg.Urgency.ORANGE
        assert res.needs_review  # liveness not yet ranked


def test_find_stale_refuses_without_detector():
    with build_graph() as store:
        res = sg.find_stale(store)
        # Honest: candidates returned, but flagged unverified, not "dead".
        assert res.needs_review and res.confidence < 0.5
        assert res.provenance is sg.Provenance.AMBIGUOUS


def test_find_stale_with_real_entrypoints():
    with build_graph() as store:
        detector = ConfigOnlyDetector({"app/cli.py::main"})
        res = sg.find_stale(store, detector=detector)
        ids = {c["id"] for c in res.result}
        assert "app/old.py::legacy" in ids       # genuinely unreachable
        assert "app/svc.py::save" not in ids      # reachable from main


def test_orient_reports_hubs():
    with build_graph() as store:
        res = sg.orient(store)
        assert res.meta["total_nodes"] == 4
        assert res.result["node_counts"]["Function"] == 4


def test_incremental_reresolves_hole():
    """Add the missing helper later; the hole should re-resolve (design §4)."""
    store = build_graph()
    assert sg.find_holes(store).meta["count"] == 1
    helper = _node("app/util.py", "missing_helper")
    store.replace_file("app/util.py", [helper], [])
    assert sg.find_holes(store).meta["count"] == 0  # worklist relinked it
    store.close()


def test_provenance_caps_urgency_at_orange():
    """An inferred red gets demoted (design §7)."""
    res = sg.Result(ok=True, result=[], urgency=sg.Urgency.RED,
                    provenance=sg.Provenance.INFERRED)
    assert res.urgency is sg.Urgency.ORANGE


def test_registry_lists_operations():
    names = {op.name for op in sg.registry()}
    assert {"find_symbol", "find_stale", "find_holes", "orient", "scan"} <= names


def test_file_store_uses_wal_and_busy_timeout(tmp_path):
    """Review 2026-07-03 F10a: the advertised watch + MCP-on-same-DB workflow needs WAL
    (readers proceed during a reindex commit) and a busy_timeout (retry instead of an
    instant 'database is locked'). :memory: stores are unaffected."""
    import stitchgraph as sg
    db = tmp_path / "c.db"
    with sg.Store(str(db)) as store:
        assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        # a second connection can read while the first holds the db open
        with sg.Store(str(db)) as second:
            assert second.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
