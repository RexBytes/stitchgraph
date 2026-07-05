"""Prisma + TypeORM resolvers (design §2a) — the JS/TS-ecosystem twins of the
Python `OrmResolver`. Both map model definitions onto `db::<table>` DBTable
nodes with MAPS_TO edges, converging with the SQL resolver's table keys so a
full-stack trace can cross from a TS entity to the raw query that reads it.

- **Prisma**: `schema.prisma` files — `model X { ... }` blocks; `@@map("name")`
  renames the table, `@map("col")` renames a field. The model has no in-graph
  class of its own (the client is generated), so the DBTable node is created and
  any SAME-NAMED class in the graph (a hand-written wrapper/domain type) gets a
  MAPS_TO edge, AMBIGUOUS when several.
- **TypeORM**: `@Entity()` classes in .ts/.tsx sources — a lightweight text pass
  (the tree-sitter extractor already made the class a node; this resolver only
  adds the table mapping). `@Entity("name")` renames; column fields are left to
  the SQL side (precision over recall — decorator arg parsing beyond the table
  name is guesswork without types).
"""

from __future__ import annotations

import re

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext

_PRISMA_MODEL = re.compile(r"^model\s+(\w+)\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
_PRISMA_MAP = re.compile(r"@@map\(\s*\"(\w+)\"\s*\)")
_ENTITY = re.compile(
    r"@Entity\s*\(\s*(?:[\"'](\w+)[\"'])?\s*\)\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")


def _table_node(table: str, rel: str, line: int, nodes: list[Node]) -> str:
    tid = f"db::{table}"
    nodes.append(Node(id=tid, kind=NodeKind.DB_TABLE, name=table,
                      location=f"{rel}:{line}:0"))
    return tid


class PrismaResolver:
    name = "prisma"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        for path in sorted(ctx.root.rglob("*.prisma")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(ctx.root).as_posix()
            for m in _PRISMA_MODEL.finditer(text):
                model, body = m.group(1), m.group(2)
                mapped = _PRISMA_MAP.search(body)
                table = mapped.group(1) if mapped else model
                line = text[:m.start()].count("\n") + 1
                tid = _table_node(table, rel, line, nodes)
                cands = ctx.by_name.get(model, [])
                prov = (Provenance.INFERRED if len(cands) == 1
                        else Provenance.AMBIGUOUS)
                for cid in cands:  # hand-written same-named domain classes, if any
                    edges.append(Edge(
                        src=cid, relation=Relation.MAPS_TO, dst_symbol=table,
                        dst_id=tid, weight=0.8 if len(cands) == 1 else 0.5,
                        provenance=prov, location=f"{rel}:{line}:0",
                        source="prisma-schema"))
        return nodes, edges


class TypeOrmResolver:
    name = "typeorm"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        for path in sorted(ctx.root.rglob("*.ts")) + sorted(ctx.root.rglob("*.tsx")):
            if not path.is_file() or ".d.ts" in path.name:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "@Entity" not in text:  # byte-gate: don't regex every TS file
                continue
            rel = path.relative_to(ctx.root).as_posix()
            for m in _ENTITY.finditer(text):
                table = m.group(1) or m.group(2)
                cls = m.group(2)
                line = text[:m.start()].count("\n") + 1
                tid = _table_node(table, rel, line, nodes)
                cid = f"{rel}::{cls}"
                if cid in ctx.ids:  # the tree-sitter extractor's class node
                    edges.append(Edge(
                        src=cid, relation=Relation.MAPS_TO, dst_symbol=table,
                        dst_id=tid, weight=0.9, provenance=Provenance.INFERRED,
                        location=f"{rel}:{line}:0", source="typeorm-entity"))
        return nodes, edges
