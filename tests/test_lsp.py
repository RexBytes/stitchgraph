"""The LSP backend (research/24): client transport, resolver mapping, honest
declines, and the reindex/type_at surfaces — all against the deterministic fake
server in fake_lsp_server.py (real JSON-RPC over stdio, no external binary).
The real-binary integration test lives in test_lsp_integration.py, gated on
the server actually being installed."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

import stitchgraph as sg
from stitchgraph.core.operations import type_at
from stitchgraph.core.resolve.lsp import LspClient, server_for
from stitchgraph.core.resolve.lsp_resolver import LspResolver

pytest.importorskip("tree_sitter_language_pack")

FAKE = Path(__file__).parent / "fake_lsp_server.py"


def _fake_cmd(tmp_path, spec: dict) -> str:
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(spec))
    return f"{sys.executable} {FAKE} {answers}"


def _ts_project(tmp_path) -> Path:
    """Two same-named functions + one call: the ambiguity the LSP pass exists
    to break. Line numbers matter — the answer keys below point at them."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "util.ts").write_text(textwrap.dedent("""\
        export function greet(name: string): string {
            return "hello " + name;
        }
    """))
    (root / "src" / "other.ts").write_text(textwrap.dedent("""\
        export function greet(name: string): string {
            return "howdy " + name;
        }
    """))
    (root / "src" / "main.ts").write_text(textwrap.dedent("""\
        import { greet } from "./util";

        export function run(): string {
            return greet("world");
        }
    """))
    return root


# The call site: main.ts line 4 (1-based), `greet` at col 11. LSP wire
# position is 0-based line -> key "src/main.ts:3:11".
_ANSWER = {"rel": "src/util.ts", "line": 0, "char": 16}


def _spec(shape: str) -> dict:
    return {"shape": shape,
            "definitions": {"src/main.ts:3:11": _ANSWER},
            "hover": {"src/main.ts:3:11": "(alias) greet(name: string): string"}}


# -- client transport ---------------------------------------------------------
@pytest.mark.parametrize("shape", ["location", "location_list", "link_list"])
def test_client_definition_normalises_every_shape(tmp_path, shape):
    root = _ts_project(tmp_path)
    client = LspClient(_fake_cmd(tmp_path, _spec(shape)), root, timeout=10.0)
    assert client.start()
    try:
        assert client.did_open("src/main.ts", "typescript")
        defs = client.definition("src/main.ts", 4, 11)
        assert defs == [("src/util.ts", 1, 16)]
    finally:
        client.stop()


def test_client_hover(tmp_path):
    root = _ts_project(tmp_path)
    client = LspClient(_fake_cmd(tmp_path, _spec("location_list")), root)
    assert client.start()
    try:
        client.did_open("src/main.ts", "typescript")
        assert client.hover("src/main.ts", 4, 11) == \
            "(alias) greet(name: string): string"
        assert client.hover("src/main.ts", 1, 0) is None  # nothing scripted there
    finally:
        client.stop()


def test_client_missing_binary_declines():
    client = LspClient("definitely-not-a-real-binary-xyz --stdio", ".")
    assert client.start() is False
    assert client.available is False


def test_client_dead_shim_declines(tmp_path):
    """The rustup-proxy failure mode found in the probe: the binary exists but
    exits immediately — start() must return False, not raise."""
    cmd = _fake_cmd(tmp_path, {"mode": "die"})
    client = LspClient(cmd, tmp_path, init_timeout=5.0)
    assert client.start() is False


def test_client_garbage_output_declines(tmp_path):
    cmd = _fake_cmd(tmp_path, {"mode": "garbage"})
    client = LspClient(cmd, tmp_path, init_timeout=5.0)
    assert client.start() is False


def test_client_unresponsive_request_times_out(tmp_path):
    """A server that answers initialize but never definitions: the request
    returns [] after the per-request timeout — nothing hangs, nothing raises."""
    root = _ts_project(tmp_path)
    cmd = _fake_cmd(tmp_path, {"mode": "mute", "definitions": {}})
    client = LspClient(cmd, root, timeout=1.0)
    assert client.start()
    try:
        client.did_open("src/main.ts", "typescript")
        assert client.definition("src/main.ts", 4, 11) == []
    finally:
        client.stop()


def test_definitions_outside_root_are_dropped(tmp_path):
    """jedi parity: a definition in the stdlib / third-party (outside the
    project root) maps to nothing."""
    root = _ts_project(tmp_path)
    spec = {"shape": "location_list",
            "definitions": {"src/main.ts:3:11":
                            {"rel": "../elsewhere/lib.ts", "line": 0, "char": 0}}}
    client = LspClient(_fake_cmd(tmp_path, spec), root)
    assert client.start()
    try:
        client.did_open("src/main.ts", "typescript")
        assert client.definition("src/main.ts", 4, 11) == []
    finally:
        client.stop()


# -- resolver end-to-end ------------------------------------------------------
def test_reindex_lsp_disambiguates_homonym(tmp_path):
    """The value proposition, end to end: two `greet` candidates -> the
    name-based pass widens AMBIGUOUS to both; the LSP pass adds the true
    EXTRACTED edge; the other arm STAYS (monotone contract)."""
    root = _ts_project(tmp_path)
    (root / "stitchgraph.toml").write_text(textwrap.dedent(f"""\
        [lsp]
        enabled = true
        [lsp.servers]
        ".ts" = '''{_fake_cmd(tmp_path, _spec("location_list"))}'''
    """))
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(root))
    assert res.ok
    report = res.result["lsp"]
    assert sum(s["resolved"] for s in report.values()) == 1
    edges = {(e.dst_id, e.provenance.value, e.source, e.weight)
             for e in store.resolved_edges() if e.src == "src/main.ts::run"
             and e.dst_symbol == "greet"}
    assert ("src/util.ts::greet", "extracted", "lsp", 1.0) in edges
    assert ("src/other.ts::greet", "ambiguous", "tree-sitter", 0.5) in edges
    store.close()


def test_reindex_lsp_streaming_matches_in_memory(tmp_path):
    """The streaming path hands resolvers an empty edge list; the store-driven
    hook must produce the identical resolved-edge multiset."""
    root = _ts_project(tmp_path)
    (root / "stitchgraph.toml").write_text(textwrap.dedent(f"""\
        [lsp]
        enabled = true
        [lsp.servers]
        ".ts" = '''{_fake_cmd(tmp_path, _spec("location_list"))}'''
    """))
    mem = sg.Store(str(tmp_path / "mem.db"))
    strm = sg.Store(str(tmp_path / "strm.db"))
    assert sg.reindex(mem, str(root), streaming=False).ok
    assert sg.reindex(strm, str(root), streaming=True).ok

    def rows(store):
        return sorted((e.src, e.relation.value, e.dst_id, e.provenance.value,
                       e.source, e.weight) for e in store.resolved_edges())
    assert rows(mem) == rows(strm)
    assert any(r[4] == "lsp" for r in rows(mem)), "LSP edge must exist"
    mem.close()
    strm.close()


def test_reindex_lsp_server_unavailable_is_honest(tmp_path):
    root = _ts_project(tmp_path)
    store = sg.Store(str(tmp_path / "g.db"))
    resolver = LspResolver(servers={".ts": "definitely-not-a-real-binary-xyz"})
    rows = [("src/main.ts::run", "greet", "src/main.ts:4:0", True)]
    assert resolver.resolve_rows(root, [], rows) == []
    assert resolver.report["definitely-not-a-real-binary-xyz"]["declined"] \
        == "server unavailable"
    store.close()


def test_resolver_word_boundary_columns(tmp_path):
    """`greet` must never be queried inside `greeting` — the column recovery
    is word-bounded."""
    from stitchgraph.core.resolve.lsp_resolver import _columns
    root = tmp_path
    (root / "a.ts").write_text("const x = greeting(greet(1), regreet(2));\n")
    cols = list(_columns(root, "a.ts", 1, "greet", {}))
    assert cols == [19]  # only the bare `greet(`, not greeting/regreet


def test_span_index_innermost_wins():
    from stitchgraph.core.model import Node, NodeKind
    from stitchgraph.core.resolve.lsp_resolver import _span_index
    nodes = [
        Node(id="a.ts::Outer", kind=NodeKind.CLASS, name="Outer",
             location="a.ts:1:0", end_line=20),
        Node(id="a.ts::Outer.m", kind=NodeKind.METHOD, name="m",
             location="a.ts:5:4", end_line=8),
    ]
    lookup = _span_index(nodes)
    assert lookup("a.ts", 6) == "a.ts::Outer.m"   # innermost
    assert lookup("a.ts", 15) == "a.ts::Outer"    # only the class spans it
    assert lookup("a.ts", 99) is None
    assert lookup("b.ts", 6) is None


# -- config + type_at ---------------------------------------------------------
def test_lsp_config_parsed(tmp_path):
    from stitchgraph.core.config import load_config
    (tmp_path / "stitchgraph.toml").write_text(textwrap.dedent("""\
        [lsp]
        enabled = true
        timeout = 3.5
        [lsp.servers]
        ".ts" = "my-server --stdio"
        ".rs" = ""
    """))
    cfg = load_config(tmp_path)
    assert cfg.lsp_enabled is True
    assert cfg.lsp_timeout == 3.5
    assert cfg.lsp_servers == {".ts": "my-server --stdio", ".rs": ""}
    # empty command disables the extension's default
    assert server_for(".rs", cfg.lsp_servers) is None
    assert server_for(".ts", cfg.lsp_servers) == ("my-server --stdio", "typescript")


def test_type_at_answers_and_refuses(tmp_path):
    root = _ts_project(tmp_path)
    (root / "stitchgraph.toml").write_text(textwrap.dedent(f"""\
        [lsp.servers]
        ".ts" = '''{_fake_cmd(tmp_path, _spec("location_list"))}'''
    """))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    hit = type_at(store, "src/main.ts", 4, 11)
    assert hit.ok and hit.result["type"] == "(alias) greet(name: string): string"
    miss = type_at(store, "src/main.ts", 1, 0)        # nothing scripted there
    assert not miss.ok
    no_server = type_at(store, "src/util.ts.map", 1, 0)  # not a file
    assert not no_server.ok
    store.close()
