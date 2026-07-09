"""MCP adapter (design §3). Registers each core operation as an MCP tool by the
same name, deriving the schema from the operation's signature + docstring.

The MCP SDK is an optional dependency (`pip install stitchgraph[mcp]`); imported
lazily so `import stitchgraph` never requires it.
"""

from __future__ import annotations

import inspect
import os
from typing import Any

from ..core.operations import Operation, registry
from ._guard import open_store

# Per-list item bound for MCP tool results (field review 2026-07-09, request 9):
# a 400 KB tool result is unusable inside an agent's context, so every list in
# the envelope is cut at this many items and the cut is REPORTED in meta —
# bounded, never silently truncated. Full lists remain available via the CLI's
# --json (and by raising the env var). 0 disables the bound.
_MCP_MAX_ITEMS = 100


def _item_budget() -> int:
    try:
        return int(os.environ.get("STITCHGRAPH_MCP_MAX_ITEMS", _MCP_MAX_ITEMS))
    except ValueError:
        return _MCP_MAX_ITEMS


def _bound(envelope: dict) -> dict:
    """Cut oversized lists in a serialized envelope: the top-level `result` list,
    list values directly under a dict `result`, and `alternatives`. Every cut is
    recorded in `meta.truncated[key] = {shown, total}` plus a one-line hint, so a
    consumer can never mistake a bounded answer for a complete one."""
    budget = _item_budget()
    if budget <= 0:
        return envelope
    truncated: dict[str, dict[str, int]] = {}

    def cut(value: Any, key: str) -> Any:
        if isinstance(value, list) and len(value) > budget:
            truncated[key] = {"shown": budget, "total": len(value)}
            return value[:budget]
        return value

    result = envelope.get("result")
    if isinstance(result, list):
        envelope["result"] = cut(result, "result")
    elif isinstance(result, dict):
        for k, v in result.items():
            result[k] = cut(v, f"result.{k}")
    envelope["alternatives"] = cut(envelope.get("alternatives") or [], "alternatives")
    if truncated:
        meta = envelope.get("meta")
        if not isinstance(meta, dict):
            meta = envelope["meta"] = {}
        meta["truncated"] = truncated
        meta["truncation_hint"] = (
            f"lists bounded to {budget} items for MCP "
            "(STITCHGRAPH_MCP_MAX_ITEMS overrides; the CLI --json carries full lists)")
    return envelope


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
        # Refuse (rather than silently create) a missing/never-indexed DB, and
        # return an envelope instead of a raw sqlite traceback on an unopenable
        # path (panel R12; review 2026-07-03, F2b).
        store, refusal = open_store(db, op.name)
        if refusal is not None:
            return refusal.to_dict()
        assert store is not None
        with store:
            result = op.func(store, **kwargs)
        return _bound(result.to_dict())

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
    tool.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    tool.__name__ = op.name
    tool.__doc__ = op.summary
    return tool


def main() -> None:
    """Entry point (`stitchgraph-mcp`). The DB path is configurable because MCP
    clients launch servers with an arbitrary cwd — a hardcoded relative default
    would resolve to the wrong directory and (pre-guard) answer from a fresh
    empty index (review 2026-07-03, F2a). Precedence: --db > STITCHGRAPH_DB >
    ./stitchgraph.db."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="stitchgraph-mcp",
        description="stitchgraph MCP server — code-intelligence tools for LLM agents")
    parser.add_argument(
        "--db",
        default=os.environ.get("STITCHGRAPH_DB", "stitchgraph.db"),
        help="index database path (env: STITCHGRAPH_DB; default: ./stitchgraph.db)")
    parser.add_argument(
        "--pure", action="store_true",
        help="run on the pure-Python reference paths only (disable the adjacency "
             "sidecar and GraphBLAS accelerators — identical results, stdlib "
             "footprint; env: STITCHGRAPH_PURE=1)")
    args = parser.parse_args()
    if args.pure:
        from ..core.purity import set_pure_mode
        set_pure_mode(True)
    build_server(db=args.db).run()


if __name__ == "__main__":
    main()
