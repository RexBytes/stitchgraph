"""Fixes from the adversarial self-audit (docs/BUG_HUNT_PROMPT.md ritual,
2026-07-07): each test pins one confirmed finding's fix. The convergence
finding (incremental strips LSP edges) is pinned in test_llm_review.py."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from stitchgraph.core.resolve.lsp import LspClient, any_server_available
from stitchgraph.core.resolve.lsp_resolver import LspResolver, _columns

FAKE = Path(__file__).parent / "fake_lsp_server.py"


def _fake_cmd(tmp_path, spec: dict) -> str:
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(spec))
    return f"{sys.executable} {FAKE} {answers}"


def test_circuit_breaker_stops_mute_server(tmp_path):
    """Finding 1: an alive-but-silent server must cost ~3 timeouts, not one
    full timeout per site (15 s x 20k sites = days under the AUTO default)."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    lines = "\n".join(f"export function f{i}() {{ return g{i}(); }}"
                      for i in range(10))
    (root / "src" / "many.ts").write_text(lines + "\n")
    # ten sites, a server that answers initialize but never definitions
    rows = [(f"src/many.ts::f{i}", f"g{i}", f"src/many.ts:{i + 1}:0", True)
            for i in range(10)]
    resolver = LspResolver(servers={".ts": _fake_cmd(tmp_path, {"mode": "mute"})},
                           timeout=0.4)
    t0 = time.monotonic()
    edges = resolver.resolve_rows(root, [], rows)
    elapsed = time.monotonic() - t0
    assert edges == []
    stats = next(iter(resolver.report.values()))
    assert "circuit breaker" in stats.get("declined", "")
    # 3 timeouts x 0.4 s + slack — nowhere near 10 sites x 0.4 s + warm-up
    assert elapsed < 5.0, f"mute server not circuit-broken ({elapsed:.1f}s)"


def test_warm_up_bails_on_stable_empty(tmp_path):
    """Finding 3: a fast healthy server whose honest answer is 'nothing
    in-root' must not burn the whole 30 s warm-up deadline."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.ts").write_text("export function f() { return 1; }\n")
    client = LspClient(_fake_cmd(tmp_path, {"definitions": {}}), root)
    assert client.start()
    try:
        client.did_open("src/a.ts", "typescript")
        t0 = time.monotonic()
        client.warm_up("src/a.ts", 1, 0, deadline=30.0)
        assert time.monotonic() - t0 < 6.0, "empty answers must converge fast"
    finally:
        client.stop()


def test_columns_are_utf16_code_units(tmp_path):
    """Finding 2: LSP positions are UTF-16 code units; an astral char before
    the callee must shift the column by one per astral char."""
    (tmp_path / "a.ts").write_text('log("👍👍"); greet(1);\n')
    cols = list(_columns(tmp_path, "a.ts", 1, "greet", {}))
    text = 'log("👍👍"); greet(1);'
    py_col = text.index("greet")
    utf16_col = len(text[:py_col].encode("utf-16-le")) // 2
    assert cols == [utf16_col]
    assert utf16_col == py_col + 2  # two astral chars -> +2 code units


def test_auto_gate_is_per_extension(monkeypatch):
    """Finding 4: disabling `.ts` alone must not kill the AUTO gate for the
    five sibling extensions sharing typescript-language-server."""
    import stitchgraph.core.resolve.lsp as lsp_mod
    seen: list[str] = []

    def fake_which(binary):
        seen.append(binary)
        return "/usr/bin/x" if binary == "typescript-language-server" else None

    monkeypatch.setattr(lsp_mod.shutil, "which", fake_which)
    assert any_server_available({".ts": ""}) is True, \
        ".js siblings still use the shared server command"
    # disabling every extension of that command removes it
    all_ts = {e: "" for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")}
    assert any_server_available(all_ts) is False


def test_late_reply_after_timeout_is_dropped(tmp_path):
    """Finding 5: a reply arriving after _wait gave up must not accumulate in
    _pending (nor be mistaken for a later request's answer)."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.ts").write_text("export function f() { return 1; }\n")
    spec = {"mode": "slow", "delay": 1.0,
            "definitions": {"src/a.ts:0:0": {"rel": "src/a.ts", "line": 0, "char": 16}}}
    client = LspClient(_fake_cmd(tmp_path, spec), root, timeout=0.2)
    assert client.start()
    try:
        client.did_open("src/a.ts", "typescript")
        assert client.definition("src/a.ts", 1, 0) == []   # times out
        assert client.consecutive_timeouts == 1
        time.sleep(1.5)                                    # late reply lands
        assert client._pending == {}, "late reply leaked into _pending"
    finally:
        client.stop()


def test_server_to_client_request_gets_error_reply(tmp_path):
    """Finding 6: a server that synchronously awaits a reply to its own
    request (workspace/configuration, ...) must be unblocked with a polite
    error, not left waiting forever."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.ts").write_text("export function f() { return 1; }\n")
    spec = {"mode": "needy",
            "definitions": {"src/a.ts:0:0": {"rel": "src/a.ts", "line": 0, "char": 16}}}
    client = LspClient(_fake_cmd(tmp_path, spec), root, timeout=5.0)
    assert client.start()
    try:
        client.did_open("src/a.ts", "typescript")
        # the needy server sends a workspace/configuration request and blocks
        # until it is answered before serving the definition
        assert client.definition("src/a.ts", 1, 0) == [("src/a.ts", 1, 16)]
    finally:
        client.stop()


def test_framing_loss_kills_process_for_fast_failure(tmp_path):
    """Finding 1's amplifier: framing loss must flip `available` to False so
    every subsequent request fails fast instead of paying the full timeout."""
    spec = {"mode": "corrupt_frame"}
    client = LspClient(_fake_cmd(tmp_path, spec), tmp_path, timeout=10.0)
    assert client.start()
    try:
        t0 = time.monotonic()
        client.definition("x.ts", 1, 0)   # triggers the corrupt frame
        time.sleep(0.3)
        assert not client.available, "framing loss must kill the process"
        assert client.definition("x.ts", 1, 0) == []
        assert time.monotonic() - t0 < 5.0, "post-framing-loss must fail fast"
    finally:
        client.stop()
