"""SQLite adjacency store — the source of truth (design §4).

Stdlib-only (sqlite3). Matrices are *derived* from this on demand (design
principle 2); they are never stored as primary state.

Read-only invariant: this DB is stitchgraph's own index. stitchgraph never
writes to analyzed source — only here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from .envelope import Provenance
from .model import Edge, Node, NodeKind, Relation

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS nodes (
    id        TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    name      TEXT NOT NULL,
    location  TEXT NOT NULL DEFAULT '',
    file      TEXT NOT NULL DEFAULT '',     -- owning file path (for incremental)
    is_stub   INTEGER NOT NULL DEFAULT 0,
    arity     INTEGER,
    summary   TEXT,
    roles     TEXT NOT NULL DEFAULT '',     -- entry-point signals, comma-joined
    end_line  INTEGER                       -- last line of the def
);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    src         TEXT NOT NULL,
    relation    TEXT NOT NULL,
    dst_symbol  TEXT NOT NULL,
    dst_id      TEXT,                        -- NULL = unresolved (a hole candidate)
    weight      REAL NOT NULL DEFAULT 1.0,
    provenance  TEXT NOT NULL DEFAULT 'extracted',
    location    TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'tree-sitter',
    file        TEXT NOT NULL DEFAULT ''     -- owning (source) file path
);

CREATE INDEX IF NOT EXISTS idx_nodes_file   ON nodes(file);
CREATE INDEX IF NOT EXISTS idx_nodes_name   ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_edges_src    ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst    ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_symbol ON edges(dst_symbol);
CREATE INDEX IF NOT EXISTS idx_edges_file   ON edges(file);
CREATE INDEX IF NOT EXISTS idx_edges_rel    ON edges(relation);
"""


class Store:
    """The graph store. Use as a context manager or call .close()."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        with closing(self.conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
        self._migrate()
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns missing from an older index file (forward-compatible)."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(nodes)")}
        for col, ddl in (("roles", "roles TEXT NOT NULL DEFAULT ''"),
                         ("end_line", "end_line INTEGER")):
            if col not in have:
                self.conn.execute(f"ALTER TABLE nodes ADD COLUMN {ddl}")

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- writes ------------------------------------------------------------
    def add_node(self, node: Node, file: str = "") -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO nodes(id, kind, name, location, file, is_stub, arity, summary, roles, end_line)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (node.id, node.kind.value, node.name, node.location,
             file or _file_of(node.id), int(node.is_stub), node.arity, node.summary,
             ",".join(sorted(node.roles)), node.end_line),
        )

    def add_role(self, node_id: str, role: str) -> None:
        node = self.get_node(node_id)
        if node is None or role in node.roles:
            return
        roles = ",".join(sorted(node.roles | {role}))
        self.conn.execute("UPDATE nodes SET roles = ? WHERE id = ?", (roles, node_id))

    def add_edge(self, edge: Edge, file: str = "") -> None:
        self.conn.execute(
            """INSERT INTO edges(src, relation, dst_symbol, dst_id, weight, provenance, location, source, file)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (edge.src, edge.relation.value, edge.dst_symbol, edge.dst_id,
             edge.weight, edge.provenance.value, edge.location, edge.source,
             file or _file_of(edge.src)),
        )

    def commit(self) -> None:
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                          (key, value))
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def replace_file(self, file: str, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        """Incremental update for one file (design §4, Store & incremental updates).

        1. Delete nodes/edges owned by this file.
        2. Insert the freshly-extracted nodes/edges.
        3. Re-resolve the unresolved worklist against new nodes.
        4. Invalidate inbound edges whose target id no longer exists.
        """
        nodes = list(nodes)
        edges = list(edges)
        with self.conn:  # single transaction
            self.conn.execute("DELETE FROM nodes WHERE file = ?", (file,))
            self.conn.execute("DELETE FROM edges WHERE file = ?", (file,))
            for n in nodes:
                self.add_node(n, file=file)
            for e in edges:
                self.add_edge(e, file=file)
            self._resolve_worklist()
            self._invalidate_dangling()

    def _resolve_worklist(self) -> None:
        """Relink unresolved edges whose dst_symbol now matches a known node."""
        self.conn.execute(
            """UPDATE edges
                  SET dst_id = (SELECT n.id FROM nodes n WHERE n.name = edges.dst_symbol LIMIT 1)
                WHERE dst_id IS NULL
                  AND (SELECT COUNT(*) FROM nodes n WHERE n.name = edges.dst_symbol) = 1"""
        )

    def _invalidate_dangling(self) -> None:
        """Any resolved edge pointing at a now-missing node reverts to a hole."""
        self.conn.execute(
            """UPDATE edges SET dst_id = NULL
                WHERE dst_id IS NOT NULL
                  AND dst_id NOT IN (SELECT id FROM nodes)"""
        )

    # -- reads -------------------------------------------------------------
    def nodes_by_name(self, name: str) -> list[Node]:
        rows = self.conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchall()
        return [_row_to_node(r) for r in rows]

    def get_node(self, node_id: str) -> Node | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return _row_to_node(row) if row else None

    def nodes_by_kind(self, kind: NodeKind) -> list[Node]:
        rows = self.conn.execute("SELECT * FROM nodes WHERE kind = ?", (kind.value,)).fetchall()
        return [_row_to_node(r) for r in rows]

    def all_node_ids(self) -> list[str]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM nodes").fetchall()]

    def all_nodes_full(self) -> list[Node]:
        return [_row_to_node(r) for r in self.conn.execute("SELECT * FROM nodes").fetchall()]

    def nodes_with_role(self, role: str) -> list[Node]:
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE (',' || roles || ',') LIKE ?",
            (f"%,{role},%",),
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    def stub_nodes(self) -> list[Node]:
        rows = self.conn.execute("SELECT * FROM nodes WHERE is_stub = 1").fetchall()
        return [_row_to_node(r) for r in rows]

    def callers_of(self, node_id: str, relation: Relation = Relation.CALLS) -> list[Edge]:
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE dst_id = ? AND relation = ?",
            (node_id, relation.value),
        ).fetchall()
        return [_row_to_edge(r) for r in rows]

    def callees_of(self, node_id: str, relation: Relation = Relation.CALLS) -> list[Edge]:
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE src = ? AND relation = ? AND dst_id IS NOT NULL",
            (node_id, relation.value),
        ).fetchall()
        return [_row_to_edge(r) for r in rows]

    def unresolved_edges(self) -> list[Edge]:
        """Dangling references — the substrate of find_holes (design §4/§6.D)."""
        rows = self.conn.execute("SELECT * FROM edges WHERE dst_id IS NULL").fetchall()
        return [_row_to_edge(r) for r in rows]

    def resolved_edges(self, relation: Relation | None = None) -> list[Edge]:
        if relation is None:
            rows = self.conn.execute("SELECT * FROM edges WHERE dst_id IS NOT NULL").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM edges WHERE dst_id IS NOT NULL AND relation = ?",
                (relation.value,),
            ).fetchall()
        return [_row_to_edge(r) for r in rows]

    def node_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"]


# -- row mappers -----------------------------------------------------------
def _row_to_node(row: sqlite3.Row) -> Node:
    keys = row.keys()
    roles = row["roles"] if "roles" in keys else ""
    return Node(
        id=row["id"], kind=NodeKind(row["kind"]), name=row["name"],
        location=row["location"],
        end_line=row["end_line"] if "end_line" in keys else None,
        is_stub=bool(row["is_stub"]), arity=row["arity"], summary=row["summary"],
        roles=frozenset(r for r in roles.split(",") if r),
    )


def _row_to_edge(row: sqlite3.Row) -> Edge:
    return Edge(
        src=row["src"], relation=Relation(row["relation"]),
        dst_symbol=row["dst_symbol"], dst_id=row["dst_id"], weight=row["weight"],
        provenance=Provenance(row["provenance"]), location=row["location"],
        source=row["source"],
    )


def _file_of(node_id: str) -> str:
    """Owning file path is the part of the id before '::' (design §4 ids)."""
    return node_id.split("::", 1)[0] if "::" in node_id else ""
