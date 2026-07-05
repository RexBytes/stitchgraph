"""Framework resolvers: Django / Express / Spring routes, events, callback fix."""

from __future__ import annotations

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


def test_openapi_spec_routes_and_handlers(tmp_path):
    """v3.35.0: an OpenAPI spec is a routing table — paths become ROUTE nodes on the
    code-first id convention, operationId links the handler, and the spec-wired
    handler stops being dead. YAML and JSON both."""
    (tmp_path / "openapi.yaml").write_text(
        "openapi: 3.0.0\n"
        "paths:\n"
        "  /users:\n"
        "    get:\n"
        "      operationId: list_users\n"
        "  /users/{id}:\n"
        "    delete: {}\n")  # no operationId -> route node only, no edge
    (tmp_path / "api.json").write_text(
        '{"swagger": "2.0", "paths": {"/ping": {"get": {"operationId": "ping"}}}}')
    (tmp_path / "handlers.py").write_text(
        "def list_users():\n    return fetch_all()\n\n"
        "def fetch_all():\n    return []\n\n"
        "def ping():\n    return 'pong'\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert {"GET /users", "DELETE /users/{id}", "GET /ping"} <= _routes(store)
        rt = _routes_to(store)
        assert ("route:GET /users", "list_users") in rt
        assert ("route:GET /ping", "ping") in rt
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "list_users" not in stale and "fetch_all" not in stale


def test_grpc_proto_binds_servicer(tmp_path):
    """v3.35.0: rpc definitions become ROUTE nodes bound to the conventional
    Servicer implementation, so servicer methods stop surfacing as dead code."""
    (tmp_path / "greeter.proto").write_text(
        'syntax = "proto3";\n'
        "service Greeter {\n"
        "  rpc SayHello (HelloRequest) returns (HelloReply);\n"
        "  rpc SayGoodbye (HelloRequest) returns (HelloReply);\n"
        "}\n"
        "message HelloRequest { string name = 1; }\n")
    (tmp_path / "server.py").write_text(
        "class GreeterServicer:\n"
        "    def SayHello(self, request, context):\n"
        "        return make_reply()\n\n"
        "def make_reply():\n    return 1\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert {"rpc Greeter.SayHello", "rpc Greeter.SayGoodbye"} <= _routes(store)
        assert ("rpc:Greeter.SayHello", "GreeterServicer.SayHello") in _routes_to(store)
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "GreeterServicer.SayHello" not in stale
        assert "make_reply" not in stale  # reached THROUGH the rpc root


def test_prisma_and_typeorm_map_tables(tmp_path):
    pytest.importorskip("tree_sitter_language_pack")
    (tmp_path / "schema.prisma").write_text(
        "model User {\n  id Int @id\n  @@map(\"users\")\n}\n"
        "model Post {\n  id Int @id\n}\n")
    (tmp_path / "user.py").write_text("class User:\n    pass\n")
    (tmp_path / "photo.ts").write_text(
        "@Entity()\nexport class Photo {\n  render() { return 1 }\n}\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        tables = {n.id for n in store.nodes_by_kind(NodeKind.DB_TABLE)}
        assert {"db::users", "db::Post", "db::Photo"} <= tables
        maps = {(e.src.split("::")[-1], e.dst_id)
                for e in store.resolved_edges(Relation.MAPS_TO)}
        assert ("User", "db::users") in maps      # prisma @@map + same-named class
        assert ("Photo", "db::Photo") in maps     # typeorm @Entity class node
