"""stitchgraph — local-first code-intelligence (library + CLI + MCP).

The library API is the core: import the operations directly.

    from stitchgraph import Store, find_symbol
    with Store("graph.db") as s:
        print(find_symbol(s, "UserService"))

The CLI (`stitchgraph ...`) and the MCP server are thin adapters over these same
operations — same names, same params (design §3).
"""

from __future__ import annotations

from .core import (
    Edge, Node, NodeKind, Provenance, Relation, Result, Store, Urgency,
    ok, refuse, registry,
)
from .core.operations import (
    find_holes, find_stale, find_symbol, get_callees, get_callers, get_matrix,
    impact_of, orient, reindex, risk, scan, trace_path,
)

__version__ = "0.0.1"

__all__ = [
    # types
    "Store", "Result", "Node", "Edge", "NodeKind", "Relation",
    "Provenance", "Urgency", "ok", "refuse", "registry",
    # operations (the public API)
    "find_symbol", "get_callers", "get_callees", "find_holes", "find_stale",
    "orient", "impact_of", "trace_path", "scan", "reindex", "get_matrix", "risk",
]
