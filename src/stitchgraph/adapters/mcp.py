"""MCP adapter (design §3). Registers each core operation as an MCP tool by the
same name, deriving the schema from the operation's signature + docstring.

The MCP SDK is an optional dependency (`pip install stitchgraph[mcp]`); imported
lazily so `import stitchgraph` never requires it.
"""

from __future__ import annotations

import inspect
from typing import Any

from ..core.operations import Operation, registry
from ..core.store import Store


def _require_mcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit(
            "The MCP server needs the MCP SDK. Install it with: "
            "pip install 'stitchgraph[mcp]'"
        ) from exc
    return FastMCP


def build_server(db: str = "stitchgraph.db"):
    FastMCP = _require_mcp()
    server = FastMCP("stitchgraph")
    for op in registry():
        server.add_tool(_make_tool(op, db), name=op.name, description=op.summary)
    return server


def _make_tool(op: Operation, db: str):
    """Wrap an operation as an MCP tool: open the store, call, return the envelope."""

    def tool(**kwargs: Any) -> dict:
        with Store(db) as store:
            result = op.func(store, **kwargs)
        return result.to_dict()

    # Expose only JSON-simple params (drop `store` and internal objects like
    # `detector`, which pydantic can't schema-ify) — they fall back to defaults.
    params = [
        inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY,
                          default=(p.default if p.default is not inspect.Parameter.empty
                                   else inspect.Parameter.empty),
                          annotation=(p.annotation if p.annotation is not inspect.Parameter.empty
                                      else str))
        for p in op.exposed_params()
    ]
    tool.__signature__ = inspect.Signature(params)
    tool.__name__ = op.name
    tool.__doc__ = op.summary
    return tool


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
