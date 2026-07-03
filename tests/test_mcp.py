"""MCP/CLI adapter surface: only JSON-simple params are exposed.

The param-filtering test runs everywhere (validates the fix that broke CI: an
`EntryPointDetector | None` param can't be schema-ified by pydantic/FastMCP). The
build test is a best-effort smoke test — skips if the MCP SDK isn't installed or
its API drifts.
"""

from __future__ import annotations

import pytest

from stitchgraph.core.operations import registry


def test_operations_expose_only_json_params():
    ops = {op.name: op for op in registry()}
    # find_stale/scan have an internal `detector` object param — must be excluded.
    for name in ("find_stale", "scan"):
        exposed = {p.name for p in ops[name].exposed_params()}
        assert "detector" not in exposed, f"{name} exposes detector to clients"
    # simple params are kept
    assert {"scope", "relation", "limit"} <= {p.name for p in ops["get_matrix"].exposed_params()}
    assert {"path"} <= {p.name for p in ops["reindex"].exposed_params()}
    # v3.0.0 ops expose their JSON-simple params to CLI/MCP (R153 sonnet F4)
    assert {"other_db", "mode", "body", "body_threshold"} <= {
        p.name for p in ops["graph_diff"].exposed_params()}
    assert "mode" in {p.name for p in ops["find_similar"].exposed_params()}


def test_mcp_server_builds():
    """A broken MCP surface must FAIL, not skip: the old blanket `except Exception:
    pytest.skip` converted any adapter breakage (pydantic schema failure, FastMCP API
    drift) into a green CI run (review 2026-07-03, F10d). Only a missing SDK skips."""
    pytest.importorskip("mcp")
    from stitchgraph.adapters.mcp import build_server

    server = build_server(":memory:")
    assert server is not None


def test_mcp_tool_call_end_to_end(tmp_path):
    """Drive one real tool call through FastMCP: schema generation, kwargs dispatch,
    and the envelope JSON must survive the SDK boundary (review 2026-07-03, F10d).
    Sync wrapper over asyncio.run so the core-only CI job (no anyio pytest plugin)
    collects it cleanly — the importorskip then skips it there."""
    pytest.importorskip("mcp")
    import asyncio
    import json

    import stitchgraph as sg
    from stitchgraph.adapters.mcp import build_server

    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    db = tmp_path / "idx.db"
    with sg.Store(str(db)) as store:
        sg.reindex(store, str(tmp_path))
    server = build_server(str(db))
    result = asyncio.run(server.call_tool("orient", {}))
    # FastMCP returns (content_blocks, structured) or just content blocks depending on
    # version; the text block always carries the serialized envelope.
    blocks = result[0] if isinstance(result, tuple) else result
    envelope = json.loads(blocks[0].text)
    assert envelope.get("ok") is True
    assert envelope.get("meta", {}).get("total_nodes", 0) > 0
    assert envelope.get("result", {}).get("node_counts", {}).get("Function", 0) == 1


# -- Review 2026-07-03 / F2b: adapters must refuse, not silently create, an index --
def test_guard_refuses_missing_db_for_query_ops(tmp_path):
    from stitchgraph.adapters._guard import open_store
    missing = tmp_path / "never-built.db"
    store, refusal = open_store(str(missing), "orient")
    assert store is None and refusal is not None
    assert not refusal.ok and refusal.needs_review
    assert "does not exist" in refusal.review_reasons[0]
    assert not missing.exists(), "a query op must not create the database file"


def test_guard_allows_reindex_to_create(tmp_path):
    from stitchgraph.adapters._guard import open_store
    db = tmp_path / "new.db"
    store, refusal = open_store(str(db), "reindex")
    assert refusal is None and store is not None
    store.close()
    assert db.exists()


def test_guard_refuses_empty_shell_db(tmp_path):
    """A DB file that exists but was never reindexed (no root meta) is a vacuum —
    answering orient/scan from it at confidence 1.0 is the exact failure mode the
    envelope contract exists to prevent (review 2026-07-03, F2b)."""
    from stitchgraph.adapters._guard import open_store
    from stitchgraph.core.store import Store
    db = tmp_path / "shell.db"
    Store(str(db)).close()          # creates the schema, no indexed root
    store, refusal = open_store(str(db), "find_stale")
    assert store is None and refusal is not None
    assert "holds no indexed project" in refusal.review_reasons[0]


def test_guard_passes_through_a_real_index(tmp_path):
    import stitchgraph as sg
    from stitchgraph.adapters._guard import open_store
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    db = tmp_path / "real.db"
    with sg.Store(str(db)) as store:
        sg.reindex(store, str(tmp_path))
    store2, refusal = open_store(str(db), "orient")
    assert refusal is None and store2 is not None
    with store2:
        res = sg.orient(store2)
        assert res.ok and res.meta.get("total_nodes", 0) > 0


def test_mcp_main_db_precedence(monkeypatch):
    """--db > STITCHGRAPH_DB > ./stitchgraph.db (review 2026-07-03, F2a)."""
    import sys

    from stitchgraph.adapters import mcp as mcp_mod
    captured: dict = {}

    def fake_build_server(db="stitchgraph.db"):
        captured["db"] = db

        class _S:
            def run(self):
                pass
        return _S()

    monkeypatch.setattr(mcp_mod, "build_server", fake_build_server)
    monkeypatch.setattr(sys, "argv", ["stitchgraph-mcp", "--db", "/tmp/x.db"])
    monkeypatch.setenv("STITCHGRAPH_DB", "/tmp/env.db")
    mcp_mod.main()
    assert captured["db"] == "/tmp/x.db"
    monkeypatch.setattr(sys, "argv", ["stitchgraph-mcp"])
    mcp_mod.main()
    assert captured["db"] == "/tmp/env.db"
    monkeypatch.delenv("STITCHGRAPH_DB")
    mcp_mod.main()
    assert captured["db"] == "stitchgraph.db"


def test_cli_query_refuses_on_missing_db(tmp_path):
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from stitchgraph.adapters.cli import build_app
    missing = tmp_path / "nope.db"
    res = CliRunner().invoke(build_app(), ["orient", "--db", str(missing)])
    assert "does not exist" in res.stdout
    assert not missing.exists(), "the CLI must not create the db to answer a query"
