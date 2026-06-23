"""ORM resolver (design §2a). Model class -> table / column mapping.

Detects SQLAlchemy and Django model classes and maps their fields to DBColumn
nodes under a DBTable, with MAPS_TO edges. Because tables are keyed `db::<table>`,
ORM models and raw SQL (the SQL resolver) **converge on the same table node** —
so a handler that runs raw SQL and a model that maps the same table link up, and
`trace_path` can cross from code through the ORM to the column.

Heuristic -> INFERRED edges with confidence < 1 (design §3).
"""

from __future__ import annotations

import ast

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext, iter_class_defs

_MODEL_BASES = {"Model", "Base", "DeclarativeBase"}
_COLUMN_CALLS = {"Column", "mapped_column"}


class OrmResolver:
    name = "orm"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        for rel, tree in ctx.parse():
            for cls, cid in iter_class_defs(tree, rel):
                if cid not in ctx.ids or not _is_model(cls):
                    continue
                columns = _columns(cls)
                # A real model maps columns; a bare declarative Base does not.
                if not columns and not _explicit_tablename(cls):
                    continue
                table = _tablename(cls)
                tid = f"db::{table}"
                nodes.setdefault(tid, Node(id=tid, kind=NodeKind.DB_TABLE, name=table,
                                           location="db"))
                loc = f"{rel}:{cls.lineno}:0"
                edges.append(Edge(src=cid, relation=Relation.MAPS_TO, dst_symbol=table,
                                  dst_id=tid, weight=0.7, provenance=Provenance.INFERRED,
                                  location=loc, source="heuristic"))
                for col in columns:
                    colid = f"db::{table}.{col}"
                    nodes.setdefault(colid, Node(id=colid, kind=NodeKind.DB_COLUMN,
                                                 name=col, location="db"))
                    edges.append(Edge(src=cid, relation=Relation.MAPS_TO, dst_symbol=col,
                                      dst_id=colid, weight=0.7, provenance=Provenance.INFERRED,
                                      location=loc, source="heuristic"))
                    edges.append(Edge(src=colid, relation=Relation.REFERENCES,
                                      dst_symbol=table, dst_id=tid, weight=1.0,
                                      provenance=Provenance.EXTRACTED, location="db",
                                      source="heuristic"))
        return list(nodes.values()), edges


def _is_model(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        name = base.attr if isinstance(base, ast.Attribute) else (
            base.id if isinstance(base, ast.Name) else None)
        if name in _MODEL_BASES or name == "Model":
            return True
    return False


def _explicit_tablename(cls: ast.ClassDef) -> str | None:
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == "__tablename__" \
                        and isinstance(stmt.value, ast.Constant) \
                        and isinstance(stmt.value.value, str):
                    return stmt.value.value
    return None


def _tablename(cls: ast.ClassDef) -> str:
    return _explicit_tablename(cls) or cls.name.lower()


def _columns(cls: ast.ClassDef) -> list[str]:
    cols: list[str] = []
    for stmt in cls.body:
        target = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target, value = stmt.target.id, stmt.value
        if target and target.startswith("__"):
            continue
        if target and isinstance(value, ast.Call) and _is_column_call(value.func):
            cols.append(target)
    return cols


def _is_column_call(func: ast.AST) -> bool:
    name = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else "")
    return name in _COLUMN_CALLS or name.endswith("Field") or name == "relationship"
