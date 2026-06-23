"""stitchgraph core — pure, stdlib-only. Never imports CLI/MCP libraries."""

from .envelope import Provenance, Result, Urgency, ok, refuse
from .model import Edge, Node, NodeKind, Relation
from .operations import Operation, registry
from .store import Store

__all__ = [
    "Provenance", "Result", "Urgency", "ok", "refuse",
    "Edge", "Node", "NodeKind", "Relation",
    "Operation", "registry", "Store",
]
