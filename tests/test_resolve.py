"""Cross-language resolver tests: route -> handler -> SQL table, end to end."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    pytest.importorskip("sqlglot")
    with _index(tmp_path) as store:
        from stitchgraph.core.model import NodeKind
        tables = {n.name for n in store.nodes_by_kind(NodeKind.DB_TABLE)}
        assert "users" in tables


def test_prose_starting_with_sql_verb_is_not_parsed_as_sql(tmp_path):
    """R58 (multi-repo Python hunt — Django/Salt): the SQL resolver must require real SQL
    *structure*, not just a leading verb, or ordinary English docstrings ('Create a list…',
    'Update the…', 'Delete a…') get fed to sqlglot — a warning flood and the odd phantom table.
    Prose yields no DB tables; a genuine query alongside it still does."""
    pytest.importorskip("sqlglot")
    from stitchgraph.core.model import NodeKind
    (tmp_path / "svc.py").write_text(
        'def helper():\n'
        '    """Create a list of prepopulated fields that should render JavaScript."""\n'
        '    x = "Update the cache when the value changes for the current request"\n'
        '    y = "Delete a stale entry from the in-memory mapping if present"\n'
        '    return x, y\n'
        'def real():\n'
        '    return run("SELECT id, name FROM customers WHERE active = 1")\n'
    )
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        tables = {n.name for n in store.nodes_by_kind(NodeKind.DB_TABLE)}
    assert "customers" in tables                                   # real SQL still resolves
    assert not ({"list", "cache", "entry", "prepopulated"} & tables)  # prose -> no phantom tables


def test_sql_literals_rejects_prose_keeps_real_sql():
    """Pin `_sql_literals` directly: prose strings whose first word is a SQL verb are NOT
    collected (so sqlglot never sees them — no warning flood), while real statements are.
    This pins the `and`/length guards the end-to-end table test can't distinguish (R58)."""
    pytest.importorskip("sqlglot")
    import ast as _ast

    from stitchgraph.core.resolve.sql import _sql_literals
    src = (
        'def f():\n'
        '    a = "Create a list of prepopulated fields to render"\n'   # prose
        '    b = "Update the cache for the current request object"\n'  # prose
        '    c = "Delete a stale entry from the mapping object now"\n' # prose
        '    d = "SELECT id FROM accounts WHERE active = 1"\n'         # real
        '    e = "CREATE TABLE widgets (id integer primary key)"\n'    # real
        '    g = "INSERT INTO audit (who) VALUES (1)"\n'               # real
        '    return a, b, c, d, e, g\n'
    )
    func = _ast.parse(src).body[0]
    found = _sql_literals(func)
    assert all(s.split()[0].upper() in ("SELECT", "CREATE", "INSERT") for s in found)
    assert len(found) == 3                                  # only the 3 real statements
    assert not any(s.startswith(("Create a", "Update the", "Delete a")) for s in found)


def test_prose_with_companion_keyword_makes_no_phantom_table(tmp_path):
    """R58 (haiku): prose that *reads* like a clause — 'Select items from the list', 'Update the
    user set to active' — has SQL structure, so structure alone let it through and sqlglot minted
    a phantom `db::the` table. The second 'signal' gate (real SQL carries (/,/=/*/clause) rejects
    it. A genuine query in the same file still resolves."""
    pytest.importorskip("sqlglot")
    from stitchgraph.core.model import NodeKind
    (tmp_path / "svc.py").write_text(
        'def doc():\n'
        '    a = "Select items from the list when the page first renders"\n'
        '    b = "Update the cache for the request before the handler runs"\n'
        '    c = "Delete from the queue all the stale entries after a backup"\n'
        '    return a, b, c\n'
        'def q():\n'
        '    return run("SELECT id FROM orders WHERE total > 0")\n'
    )
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        tables = {n.name for n in store.nodes_by_kind(NodeKind.DB_TABLE)}
    assert "orders" in tables                                      # real query resolves
    # prose parsed-as-SQL yields only English function-word "tables" (the/a/…), all dropped
    assert not ({"the", "a", "for", "from", "with"} & tables)


def test_full_stack_trace_route_to_table(tmp_path):
    pytest.importorskip("sqlglot")
    """The headline 'gem': a path from an HTTP route down to a DB table."""
    with _index(tmp_path) as store:
        res = sg.trace_path(store, "GET /users", "users")
        assert res.ok, res.review_reasons
        assert res.result[0].endswith("route:GET /users")
        assert res.result[-1] == "db::users"


def test_write_vs_read_relation(tmp_path):
    pytest.importorskip("sqlglot")
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
    pytest.importorskip("sqlglot")
    """The ORM model and a raw SQL query land on the SAME db::users node."""
    with _index_orm(tmp_path) as store:
        from stitchgraph.core.model import Relation
        maps = {e.dst_id for e in store.resolved_edges(Relation.MAPS_TO)}
        reads = {e.dst_id for e in store.resolved_edges(Relation.READS)}
        assert "db::users" in maps   # from the model
        assert "db::users" in reads  # from the SQL — same node


def _write_fullstack(root: Path) -> None:
    app = root / "app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "views.py").write_text(
        "app = object()\n\n"
        "@app.post('/users')\n"
        "def create_user():\n"
        '    return db.execute("INSERT INTO users (email) VALUES (?)")\n'
    )
    tmpl = root / "templates"
    tmpl.mkdir()
    (tmpl / "signup.html").write_text(
        '<form action="/users" method="post"><input name="email"></form>\n')


def test_full_stack_html_to_table(tmp_path):
    pytest.importorskip("sqlglot")
    """The complete gem: HTML form -> route -> handler -> DB table in one trace."""
    _write_fullstack(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    res = sg.trace_path(store, "templates/signup.html", "users")
    assert res.ok, res.review_reasons
    assert res.result[0].endswith("signup.html::template")
    assert any("route:POST /users" in step for step in res.result)
    assert res.result[-1] == "db::users"
    store.close()
