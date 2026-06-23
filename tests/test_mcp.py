"""MCP adapter: tools are generated from the operation registry."""

from __future__ import annotations

import pytest

from stitchgraph.core.operations import registry

pytest.importorskip("mcp")


def test_mcp_server_registers_all_operations():
    from stitchgraph.adapters.mcp import build_server

    server = build_server(":memory:")
    # FastMCP exposes registered tools; names must match the operation registry.
    import asyncio
    tools = asyncio.get_event_loop().run_until_complete(server.list_tools())
    tool_names = {t.name for t in tools}
    op_names = {op.name for op in registry()}
    assert op_names <= tool_names, f"missing MCP tools: {op_names - tool_names}"
