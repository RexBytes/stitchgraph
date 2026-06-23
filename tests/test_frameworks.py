"""Framework resolvers: Django / Express / Spring routes, events, callback fix."""

from __future__ import annotations

from pathlib import Path

import pytest

import stitchgraph as sg
from stitchgraph.core.model import NodeKind, Relation


def _routes(store):
    return {n.name for n in store.nodes_by_kind(NodeKind.ROUTE)}


def _routes_to(store):
    return {(e.src.split("::")[-1], e.dst_id.split("::")[-1])
            for e in store.resolved_edges(Relation.ROUTES_TO)}


def test_django_urlconf(tmp_path):
    (tmp_path / "urls.py").write_text(
        "def user_list(req): return 1\nurlpatterns = [path('users/', user_list)]\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert ("route:ANY /users/", "user_list") in _routes_to(store)


def test_express_routes(tmp_path):
    pytest.importorskip("tree_sitter_language_pack")
    (tmp_path / "server.js").write_text(
        "function listUsers(req,res){ return 1; }\napp.get('/api/users', listUsers);\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert ("route:GET /api/users", "listUsers") in _routes_to(store)


def test_spring_routes(tmp_path):
    pytest.importorskip("tree_sitter_language_pack")
    (tmp_path / "Ctrl.java").write_text(
        'class Ctrl {\n  @GetMapping("/items")\n  public int items(){ return 1; }\n}\n')
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert "GET /items" in _routes(store)
        assert ("route:GET /items", "Ctrl.items") in _routes_to(store)


def test_events_decoupled_trace(tmp_path):
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bus.py").write_text(
        "bus = object()\n"
        "def publisher():\n    bus.emit('user_created', 1)\n"
        "def setup():\n    bus.on('user_created', on_created)\n"
        "def on_created():\n    return 1\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert {n.name for n in store.nodes_by_kind(NodeKind.EVENT)} == {"user_created"}
        res = sg.trace_path(store, "publisher", "on_created")
        assert res.ok
        assert any("event::user_created" in s for s in res.result)


def test_callback_methods_not_dead(tmp_path):
    """Methods overriding a framework (external) base aren't flagged dead."""
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "parser.py").write_text(
        "from html.parser import HTMLParser\n\n"
        "class MyParser(HTMLParser):\n"
        "    def handle_starttag(self, tag, attrs):\n"        # framework callback
        "        return tag\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "MyParser.handle_starttag" not in stale
