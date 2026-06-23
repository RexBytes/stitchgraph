"""Cross-language resolver tests: route -> handler -> SQL table, end to end."""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg


def _write_webapp(root: Path) -> None:
    app = root / "webapp"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "views.py").write_text(
        "app = object()\n\n"
        "@app.get('/users')\n"
        "def list_users():\n"
        "    return query_users()\n\n"
        "def query_users():\n"
        '    return db.execute("SELECT id, email FROM users")\n\n'
        "@app.post('/users')\n"
        "def create_user():\n"
        "    return save_user()\n\n"
        "def save_user():\n"
        '    return db.execute("INSERT INTO users (email) VALUES (?)")\n'
    )


def _index(tmp_path: Path) -> sg.Store:
    _write_webapp(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    return store


def test_routes_become_nodes(tmp_path):
    with _index(tmp_path) as store:
        from stitchgraph.core.model import NodeKind
        routes = {n.name for n in store.nodes_by_kind(NodeKind.ROUTE)}
        assert "GET /users" in routes
        assert "POST /users" in routes


def test_route_links_to_handler(tmp_path):
    with _index(tmp_path) as store:
        from stitchgraph.core.model import Relation
        edges = store.resolved_edges(Relation.ROUTES_TO)
        targets = {e.dst_id.split("::")[-1] for e in edges}
        assert "list_users" in targets and "create_user" in targets


def test_sql_tables_extracted(tmp_path):
    with _index(tmp_path) as store:
        from stitchgraph.core.model import NodeKind
        tables = {n.name for n in store.nodes_by_kind(NodeKind.DB_TABLE)}
        assert "users" in tables


def test_full_stack_trace_route_to_table(tmp_path):
    """The headline 'gem': a path from an HTTP route down to a DB table."""
    with _index(tmp_path) as store:
        res = sg.trace_path(store, "GET /users", "users")
        assert res.ok, res.review_reasons
        assert res.result[0].endswith("route:GET /users")
        assert res.result[-1] == "db::users"


def test_write_vs_read_relation(tmp_path):
    with _index(tmp_path) as store:
        from stitchgraph.core.model import Relation
        writes = store.resolved_edges(Relation.WRITES)
        reads = store.resolved_edges(Relation.READS)
        assert any(e.dst_id == "db::users" for e in writes)   # INSERT
        assert any(e.dst_id == "db::users" for e in reads)    # SELECT


def test_routes_are_live_not_dead(tmp_path):
    """Handlers reachable from a route must not be flagged stale."""
    with _index(tmp_path) as store:
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "list_users" not in stale
        assert "query_users" not in stale  # reached via the handler


def _write_orm(root: Path) -> None:
    pkg = root / "orm"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "models.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\n\n"
        "class Base(DeclarativeBase):\n    pass\n\n"
        "class User(Base):\n"
        '    __tablename__ = "users"\n'
        "    id = Column(Integer, primary_key=True)\n"
        "    email = Column(String)\n"
    )
    (pkg / "svc.py").write_text(
        "def report():\n"
        '    return db.execute("SELECT email FROM users")\n'
    )


def _index_orm(tmp_path: Path) -> sg.Store:
    _write_orm(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    return store


def test_orm_maps_model_to_table_and_columns(tmp_path):
    with _index_orm(tmp_path) as store:
        from stitchgraph.core.model import NodeKind
        tables = {n.name for n in store.nodes_by_kind(NodeKind.DB_TABLE)}
        cols = {n.id for n in store.nodes_by_kind(NodeKind.DB_COLUMN)}
        assert "users" in tables
        assert "base" not in tables  # bare declarative Base is not a model
        assert {"db::users.id", "db::users.email"} <= cols


def test_orm_and_sql_converge_on_same_table(tmp_path):
    """The ORM model and a raw SQL query land on the SAME db::users node."""
    with _index_orm(tmp_path) as store:
        from stitchgraph.core.model import Relation
        maps = {e.dst_id for e in store.resolved_edges(Relation.MAPS_TO)}
        reads = {e.dst_id for e in store.resolved_edges(Relation.READS)}
        assert "db::users" in maps   # from the model
        assert "db::users" in reads  # from the SQL — same node
