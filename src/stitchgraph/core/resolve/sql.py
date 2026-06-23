"""SQL resolver (design §2a). Query string -> table / column edges.

Finds SQL string literals in function bodies, parses them with sqlglot, and links
the enclosing function to the tables it touches: QUERIES always, plus READS
(SELECT) or WRITES (INSERT/UPDATE/DELETE). DBTable nodes are created on demand.

sqlglot is an optional dependency (`pip install 'stitchgraph[extract]'` or
sqlglot directly). Without it this resolver is a no-op — the rest of the graph is
unaffected (design §2a: add only the stacks you use).
"""

from __future__ import annotations

import ast

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext, iter_function_defs

try:
    import sqlglot
    from sqlglot import exp
    _HAVE_SQLGLOT = True
except ModuleNotFoundError:  # pragma: no cover
    _HAVE_SQLGLOT = False

_SQL_START = ("select", "insert", "update", "delete", "with", "create", "replace")


class SqlResolver:
    name = "sql"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        if not _HAVE_SQLGLOT:
            return [], []
        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        for rel, tree in ctx.parse():
            for func, fid, _ in iter_function_defs(tree, rel):
                if fid not in ctx.ids:
                    continue
                for sql in _sql_literals(func):
                    _link(nodes, edges, fid, rel, func.lineno, sql)
        return list(nodes.values()), edges


def _link(nodes: dict[str, Node], edges: list[Edge], fid: str, rel: str,
          line: int, sql: str) -> None:
    try:
        tree = sqlglot.parse_one(sql)
    except Exception:  # noqa: BLE001 — malformed/unsupported SQL, skip
        return
    if tree is None:
        return
    writes = isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Create))
    # The DML *target* is a write; tables read by a nested SELECT/subquery are reads
    # (e.g. `INSERT INTO archive SELECT ... FROM users` writes `archive`, reads
    # `users`). The target lives in the statement's `this`; everything else is a read.
    write_tables = ({id(t) for t in tree.this.find_all(exp.Table)}
                    if writes and tree.this is not None else set())
    # CTE names (`WITH recent AS (...)`) parse as Tables when referenced, but they
    # are query-local aliases, not real db tables — skip them so they don't become
    # phantom `db::` nodes that pollute trace_path / get_matrix.
    cte_names = {cte.alias for cte in tree.find_all(exp.CTE) if cte.alias}
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name or name in cte_names:
            continue
        rel_kind = Relation.WRITES if id(table) in write_tables else Relation.READS
        tid = f"db::{name}"
        nodes.setdefault(tid, Node(id=tid, kind=NodeKind.DB_TABLE, name=name,
                                   location="db", roles=frozenset()))
        loc = f"{rel}:{line}:0"
        edges.append(Edge(src=fid, relation=Relation.QUERIES, dst_symbol=name,
                          dst_id=tid, weight=0.8, provenance=Provenance.INFERRED,
                          location=loc, source="heuristic"))
        edges.append(Edge(src=fid, relation=rel_kind, dst_symbol=name, dst_id=tid,
                          weight=0.8, provenance=Provenance.INFERRED, location=loc,
                          source="heuristic"))


def _sql_literals(func: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value.strip()
            if len(s) > 10 and s.split(None, 1)[0].lower() in _SQL_START:
                out.append(s)
    return out
