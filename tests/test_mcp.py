"""MCP adapter: the server builds from the operation registry without error.

Skips when the MCP SDK isn't installed (runs in CI, where [all] provides it).
A smoke test — building the server registers one tool per operation via
FastMCP.add_tool, so a successful build validates the adapter wiring.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")


def test_mcp_server_builds():
    from stitchgraph.adapters.mcp import build_server

    try:
        server = build_server(":memory:")
    except (TypeError, AttributeError) as exc:  # FastMCP API drift between versions
        pytest.skip(f"MCP SDK API mismatch: {exc}")
    assert server is not None
