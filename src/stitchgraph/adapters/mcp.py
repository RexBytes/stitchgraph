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


# Ops whose payload lists are index-correlated: get_matrix's `cells` entries
# point INTO `labels` by index, so the generic per-list walk would silently
# corrupt the correspondence. They get the dedicated correlated cut below
# (labels and cells trimmed TOGETHER) instead of the generic walk. NEVER a
# blanket exemption: get_matrix's own scope refusal is gated on the
# caller-supplied `limit`, which has no ceiling — an exemption reopened the
# unbounded-blob hole the bound exists to close (self-review round 2).
_MATRIX_OPS = frozenset({"get_matrix"})


def _needs_cut(value: Any, budget: int) -> bool:
    """Allocation-free pre-scan: does any list anywhere exceed the budget? The
    common small-result case then skips the full tree rebuild in _bound."""
    if isinstance(value, list):
        return len(value) > budget or any(_needs_cut(v, budget) for v in value)
    if isinstance(value, dict):
        return any(_needs_cut(v, budget) for v in value.values())
    return False


def _bound_matrix(envelope: dict, budget: int) -> dict:
    """Correlated cut for matrix payloads: trim `labels` to the budget, keep
    only `cells` whose src/dst indices survive (alignment preserved — every
    remaining index still names the right label), then apply the plain item cap
    to the cells themselves. Both cuts are reported in meta.truncated."""
    result = envelope.get("result")
    if not isinstance(result, dict):
        return envelope
    truncated: dict[str, dict[str, int]] = {}
    labels = result.get("labels")
    cells = result.get("cells")
    n_cells0 = len(cells) if isinstance(cells, list) else 0
    if isinstance(labels, list) and len(labels) > budget:
        truncated["result.labels"] = {"shown": budget, "total": len(labels)}
        result["labels"] = labels[:budget]
        if isinstance(cells, list):
            cells = [c for c in cells if isinstance(c, dict)
                     and isinstance(c.get("src"), int) and isinstance(c.get("dst"), int)
                     and c["src"] < budget and c["dst"] < budget]
    if isinstance(cells, list) and (len(cells) > budget or len(cells) != n_cells0):
        result["cells"] = cells[:budget]
        truncated["result.cells"] = {"shown": len(result["cells"]), "total": n_cells0}
    if truncated:
        meta = envelope.get("meta")
        if not isinstance(meta, dict):
            meta = envelope["meta"] = {}
        meta["truncated"] = truncated
        meta["truncation_hint"] = (
            f"matrix bounded to {budget} labels for MCP, cells cut consistently "
            "(surviving indices stay aligned) — narrow the scope, or use the CLI "
            "--json for the full matrix")
    return envelope


def _bound(envelope: dict, op_name: str | None = None) -> dict:
    """Cut oversized lists in a serialized envelope, RECURSIVELY — a nested list
    (a scan cycle's `members`, an orient hub's callers) must not smuggle the
    400 KB blob the bound exists to prevent (self-review 2026-07-09). Every cut
    is recorded in `meta.truncated[path] = {shown, total}` plus a one-line hint,
    so a consumer can never mistake a bounded answer for a complete one.
    Matrix ops take the correlated cut; everything else the generic walk."""
    budget = _item_budget()
    if budget <= 0:
        return envelope
    if op_name in _MATRIX_OPS:
        return _bound_matrix(envelope, budget)
    if not (_needs_cut(envelope.get("result"), budget)
            or _needs_cut(envelope.get("alternatives") or [], budget)):
        return envelope  # nothing to cut: skip the tree rebuild entirely
    truncated: dict[str, dict[str, int]] = {}

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, list):
            if len(value) > budget:
                # Same path may be cut in several sibling items ("result[].members");
                # report the largest original length rather than overwriting blindly.
                seen = truncated.get(path)
                total = max(len(value), seen["total"]) if seen else len(value)
                truncated[path] = {"shown": budget, "total": total}
                value = value[:budget]
            return [walk(v, f"{path}[]") for v in value]
        if isinstance(value, dict):
            return {k: walk(v, f"{path}.{k}") for k, v in value.items()}
        return value

    envelope["result"] = walk(envelope.get("result"), "result")
    envelope["alternatives"] = walk(envelope.get("alternatives") or [], "alternatives")
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
        return _bound(result.to_dict(), op.name)

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
