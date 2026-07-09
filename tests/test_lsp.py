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
def test_reindex_lsp_disambiguates_homonym(tmp_path, monkeypatch):
    """The value proposition, end to end: two `greet` candidates -> the
    name-based pass widens AMBIGUOUS to both; the LSP pass adds the true
    EXTRACTED edge; the other arm STAYS (monotone contract)."""
    monkeypatch.delenv("STITCHGRAPH_NO_LSP", raising=False)
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


def test_reindex_lsp_streaming_matches_in_memory(tmp_path, monkeypatch):
    """The streaming path hands resolvers an empty edge list; the store-driven
    hook must produce the identical resolved-edge multiset."""
    monkeypatch.delenv("STITCHGRAPH_NO_LSP", raising=False)
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
    """A decline names the missing binary and says WHY (field review 2026-07-09,
    request 1) — the opaque "server unavailable" cost a whole low-confidence
    first pass that one actionable message would have saved."""
    root = _ts_project(tmp_path)
    store = sg.Store(str(tmp_path / "g.db"))
    resolver = LspResolver(servers={".ts": "definitely-not-a-real-binary-xyz"})
    rows = [("src/main.ts::run", "greet", "src/main.ts:4:0", True)]
    assert resolver.resolve_rows(root, [], rows) == []
    stats = resolver.report["definitely-not-a-real-binary-xyz"]
    assert "not on PATH" in stats["declined"]
    assert "definitely-not-a-real-binary-xyz" in stats["declined"]
    # a binary that simply isn't installed is NOT the broken-binary case
    assert "broken_binary" not in stats
    store.close()


# -- actionable server diagnostics (field review 2026-07-09, request 1) --------
def test_diagnose_server_missing_binary_names_install_hint():
    import shutil

    from stitchgraph.core.resolve.lsp import diagnose_server
    msg, present = diagnose_server("definitely-not-a-real-binary-xyz --stdio")
    assert present is False
    assert "not on PATH" in msg
    if not shutil.which("rust-analyzer"):
        # a registered default server carries its one-line install fix
        msg_ra, _present = diagnose_server("rust-analyzer")
        assert "rustup component add rust-analyzer" in msg_ra


def test_diagnose_server_rustup_shim_names_the_fix(tmp_path, monkeypatch):
    """THE field case: rust-analyzer resolves to rustup's proxy shim (component
    not installed). The diagnostic must lift the exact `rustup component add`
    command from the shim's own error output."""
    import os

    from stitchgraph.core.resolve.lsp import diagnose_server
    shim = tmp_path / "rust-analyzer"
    shim.write_text(
        "#!/bin/sh\n"
        "echo \"error: 'rust-analyzer' is not installed for the toolchain "
        "'stable-x86_64-unknown-linux-gnu'\" >&2\n"
        "echo 'To install, run: rustup component add rust-analyzer' >&2\n"
        "exit 1\n")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    msg, present = diagnose_server("rust-analyzer")
    assert present is True
    assert "proxy shim" in msg
    assert "rustup component add rust-analyzer" in msg


def test_diagnose_probe_detaches_stdin_and_memoizes(tmp_path, monkeypatch):
    """(1) The --version probe must NOT inherit the parent's stdin — under the
    MCP stdio transport that fd is the live JSON-RPC channel, and a broken
    binary that reads stdin would eat protocol bytes (self-review round 2).
    (2) The verdict is memoized per (cmd, path, mtime): a second decline in
    the same session must not re-spawn the probe."""
    import subprocess as sp

    from stitchgraph.core.resolve import lsp as lsp_mod

    shim = tmp_path / "fake-server"
    shim.write_text("#!/bin/sh\nexit 1\n")
    shim.chmod(0o755)
    # a REAL PATH entry: the probe subprocess resolves the binary via the OS,
    # so patching shutil.which alone would leave the spawn failing (OSError,
    # a transient outcome the memo deliberately never caches)
    monkeypatch.setenv("PATH", str(tmp_path))
    calls: list[dict] = []
    real_run = sp.run

    def spy_run(argv, **kwargs):
        calls.append(kwargs)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(lsp_mod.subprocess, "run", spy_run)
    lsp_mod._DIAGNOSIS_CACHE.clear()
    msg1, present1 = lsp_mod.diagnose_server("fake-server")
    msg2, present2 = lsp_mod.diagnose_server("fake-server")
    assert present1 is present2 is True
    assert msg1 == msg2
    assert len(calls) == 1, "second decline must hit the memo, not re-probe"
    assert calls[0].get("stdin") is sp.DEVNULL
    # honest wording: the probe's failure is reported as an observation, not
    # asserted as a broken install
    assert "probing it with --version also failed" in msg1


def test_diagnose_transient_probe_failure_is_not_cached(tmp_path, monkeypatch):
    """A probe timeout on a loaded machine may be transient: it must be
    reported but never cached, so the next decline re-probes."""
    import subprocess as sp

    from stitchgraph.core.resolve import lsp as lsp_mod

    monkeypatch.setattr(lsp_mod.shutil, "which", lambda b: "/fake/bin/srv")
    monkeypatch.setattr(lsp_mod.os, "stat",
                        lambda p: type("S", (), {"st_mtime_ns": 1})())
    attempts = []

    def timeout_run(argv, **kwargs):
        attempts.append(argv)
        raise sp.TimeoutExpired(argv, 10)

    monkeypatch.setattr(lsp_mod.subprocess, "run", timeout_run)
    lsp_mod._DIAGNOSIS_CACHE.clear()
    msg1, _ = lsp_mod.diagnose_server("srv")
    msg2, _ = lsp_mod.diagnose_server("srv")
    assert "could not be executed" in msg1 == msg2
    assert len(attempts) == 2, "transient outcomes must not be memoized"


def test_reindex_auto_flags_broken_server_binary(tmp_path, monkeypatch):
    """AUTO mode stays silent for a machine with no servers, but a binary that
    EXISTS and cannot serve (the rustup-shim class) must surface a review
    reason with the stable LSP_UNAVAILABLE code — the user half-installed a
    server, so degrading silently to the name-based graph hides exactly the
    diagnostic they need."""
    monkeypatch.delenv("STITCHGRAPH_NO_LSP", raising=False)
    root = _ts_project(tmp_path)
    (root / "stitchgraph.toml").write_text(textwrap.dedent(f"""\
        [lsp.servers]
        ".ts" = '''{_fake_cmd(tmp_path, {"mode": "die"})}'''
    """))
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(root))
    assert res.ok
    assert res.needs_review
    assert any("declined" in r for r in res.review_reasons)
    assert "LSP_UNAVAILABLE" in res.review_codes
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
