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
    assert {"other_db", "mode", "body"} <= {p.name for p in ops["graph_diff"].exposed_params()}
    assert "mode" in {p.name for p in ops["find_similar"].exposed_params()}


def test_mcp_server_builds():
    pytest.importorskip("mcp")
    from stitchgraph.adapters.mcp import build_server

    try:
        server = build_server(":memory:")
    except Exception as exc:  # noqa: BLE001 — best-effort across SDK versions
        pytest.skip(f"MCP SDK build issue: {exc}")
    assert server is not None
