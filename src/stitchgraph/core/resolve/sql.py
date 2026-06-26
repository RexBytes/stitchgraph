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
import re

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext, iter_function_defs

try:
    import sqlglot
    from sqlglot import exp
    _HAVE_SQLGLOT = True
except ModuleNotFoundError:  # pragma: no cover
    _HAVE_SQLGLOT = False

# A string is treated as SQL only if it has real statement *structure*, not merely a leading
# verb. The old "first word in {select,insert,...,create,...}" test misfired on ordinary English
# docstrings — "Create a list of…", "Update the…", "Delete a…", "With this…" — flooding sqlglot
# with prose to parse (hundreds per file on Django/Salt) and occasionally minting phantom tables
# (multi-repo Python hunt, panel R58). Requiring the companion keyword (FROM/INTO/SET/the CREATE
# object type/…) keeps real queries while rejecting prose. Recall cost: a SELECT with no FROM or
# a `CREATE %sINDEX`-style format template won't match — neither yields a real table edge anyway.
_SQL_RE = re.compile(
    r"""^\s*(
        select\b[\s\S]*?\bfrom\b
      | insert\s+(or\s+\w+\s+)?into\b
      | update\b[\s\S]*?\bset\b
      | delete\s+from\b
      | replace\s+into\b
      | with\b[\s\S]*?\bas\b[\s\S]*?\bselect\b
      | create\s+(or\s+replace\s+)?
          (?:(?:temp(?:orary)?|global|local|unique|materialized|unlogged)\s+)*
          (?:table|view|index|database|schema|sequence|trigger|function|procedure|
             extension|collation|role|user|tablespace|aggregate|type|domain|operator)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


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
        # parse() (not parse_one()) so a multi-statement string
        # (`DELETE ...; SELECT ...`) is classified per statement, not as one Block
        # that would mislabel the DML target as a read.
        trees = sqlglot.parse(sql)
    except Exception:  # noqa: BLE001 — malformed/unsupported SQL, skip
        return
    for tree in trees:
        if tree is not None:
            _link_one(nodes, edges, fid, rel, line, tree)


def _link_one(nodes: dict[str, Node], edges: list[Edge], fid: str, rel: str,
              line: int, tree) -> None:
    writes = isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Create))
    # The DML *target* is a write; tables read by a nested SELECT/subquery are reads
    # (e.g. `INSERT INTO archive SELECT ... FROM users` writes `archive`, reads
    # `users`). The target lives in the statement's `this`; everything else is a read.
    # `tree.this` is usually the target expression, but sqlglot can set it to a non-node
    # (e.g. `DELETE TABLE x` parses to a Delete whose `.this` is the bool `False`), so guard
    # on Expression before walking it — else `.find_all` raises on a bool (panel crash-sweep).
    write_tables = ({id(t) for t in tree.this.find_all(exp.Table)}
                    if writes and isinstance(tree.this, exp.Expression) else set())
    # CTE names (`WITH recent AS (...)`) parse as Tables when referenced, but they
    # are query-local aliases, not real db tables — skip them so they don't become
    # phantom `db::` nodes that pollute trace_path / get_matrix.
    cte_names = {cte.alias for cte in tree.find_all(exp.CTE) if cte.alias}
    for table in tree.find_all(exp.Table):
        name = table.name
        # `DELETE TABLE x` / `UPDATE TABLE x` are non-standard (MySQL-isms for DELETE
        # FROM / UPDATE x); sqlglot misparses the `TABLE` keyword itself as the table,
        # yielding a phantom `db::TABLE` node while missing the real one (panel R11B).
        # An unquoted bare `table` is never a real identifier — skip it.
        if not name or name in cte_names or name.lower() == "table":
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
            if len(s) > 10 and _SQL_RE.match(s):
                out.append(s)
    return out
