"""Node and edge model (design §4). Stdlib-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .envelope import Provenance


class NodeKind(str, Enum):
    FILE = "File"
    MODULE = "Module"
    PACKAGE = "Package"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"
    VARIABLE = "Variable"
    ROUTE = "Route"
    ENDPOINT = "Endpoint"
    HANDLER = "Handler"
    TEMPLATE = "Template"
    HTML_ELEMENT = "HTMLElement"
    ORM_MODEL = "ORMModel"
    DB_TABLE = "DBTable"
    DB_COLUMN = "DBColumn"
    QUERY = "Query"
    TEST = "Test"


class Relation(str, Enum):
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    INHERITS = "INHERITS"
    READS = "READS"
    WRITES = "WRITES"
    QUERIES = "QUERIES"
    ROUTES_TO = "ROUTES_TO"
    RENDERS = "RENDERS"
    SUBMITS_TO = "SUBMITS_TO"
    MAPS_TO = "MAPS_TO"
    RETURNS = "RETURNS"
    REFERENCES = "REFERENCES"
    TESTS = "TESTS"
    EMITS = "EMITS"
    HANDLES = "HANDLES"
    RUNTIME_HITS = "RUNTIME_HITS"


@dataclass
class Node:
    """A code entity. `id` is `path::qualified.name` (design §4 — stable ids).

    `roles` are entry-point signals the extractor records for the detector to
    interpret (design §4): "exported" (public API), "main", "script", "test".
    """

    id: str
    kind: NodeKind
    name: str
    location: str = ""  # file:line:col
    end_line: int | None = None  # last line of the def (for runtime mapping)
    is_stub: bool = False
    arity: int | None = None
    summary: str | None = None
    roles: frozenset[str] = frozenset()
    meta: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_id(path: str, qualified_name: str, disambiguator: int = 0) -> str:
        base = f"{path}::{qualified_name}"
        return base if disambiguator == 0 else f"{base}#{disambiguator}"

    def to_dict(self) -> dict[str, Any]:
        out = {"id": self.id, "kind": self.kind.value, "name": self.name,
               "location": self.location}
        if self.is_stub:
            out["is_stub"] = True
        if self.roles:
            out["roles"] = sorted(self.roles)
        return out


@dataclass
class Edge:
    """A typed, weighted, provenanced relation.

    `dst_id is None` means the reference did not resolve — a dangling reference,
    the raw material of find_holes (design §4). `dst_symbol` always records the
    raw name at the reference site so the incremental updater can re-resolve it.
    """

    src: str
    relation: Relation
    dst_symbol: str
    dst_id: str | None = None
    weight: float = 1.0
    provenance: Provenance = Provenance.EXTRACTED
    location: str = ""
    source: str = "tree-sitter"

    @property
    def resolved(self) -> bool:
        return self.dst_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {"src": self.src, "relation": self.relation.value,
                "dst_id": self.dst_id, "dst_symbol": self.dst_symbol,
                "weight": round(self.weight, 3), "provenance": self.provenance.value}
