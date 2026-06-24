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
"""

# Indexes are created *after* migration so they never reference a column an older
# index file hasn't gained yet (e.g. `edges.file`).
_INDEXES = """
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
            cur.executescript(_SCHEMA)   # tables (IF NOT EXISTS)
        self._migrate()                  # add columns an older file is missing
        with closing(self.conn.cursor()) as cur:
            cur.executescript(_INDEXES)  # indexes, now every column exists
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns missing from an older index file (forward-compatible). Covers
        both tables: `_row_to_edge` reads `source`/`file` unconditionally, so an index
        built before those edge columns existed must gain them or edge reads fail."""
        tables = {
            "nodes": (("roles", "roles TEXT NOT NULL DEFAULT ''"),
                      ("end_line", "end_line INTEGER")),
            "edges": (("source", "source TEXT NOT NULL DEFAULT 'tree-sitter'"),
                      ("file", "file TEXT NOT NULL DEFAULT ''")),
        }
        for table, cols in tables.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, ddl in cols:
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- writes ------------------------------------------------------------
    def add_node(self, node: Node, file: str = "") -> None:
        try:
            self.conn.execute(
                """INSERT OR REPLACE INTO nodes(id, kind, name, location, file, is_stub, arity, summary, roles, end_line)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (node.id, node.kind.value, node.name, node.location,
                 file or _file_of(node.id), int(node.is_stub), node.arity, node.summary,
                 ",".join(sorted(node.roles)), node.end_line),
            )
        except (UnicodeEncodeError, ValueError):
            # A source file/dir with a non-UTF-8 name (Latin-1/Shift-JIS bytes on a
            # POSIX fs) is decoded via surrogateescape into a lone-surrogate node id;
            # an embedded-NUL name yields ValueError. sqlite can't bind either, so the
            # bulk-insert in reindex would abort the whole index. Skip the unstorable
            # node instead — the read side already refuses such ids symmetrically
            # (nodes_by_name/get_node), so dropping it keeps the graph consistent and
            # reindex completes (panel R11A). Edges into it are dropped the same way.
            return

    def add_role(self, node_id: str, role: str) -> None:
        node = self.get_node(node_id)
        if node is None or role in node.roles:
            return
        roles = ",".join(sorted(node.roles | {role}))
        self.conn.execute("UPDATE nodes SET roles = ? WHERE id = ?", (roles, node_id))

    def add_edge(self, edge: Edge, file: str = "") -> None:
        try:
            self.conn.execute(
                """INSERT INTO edges(src, relation, dst_symbol, dst_id, weight, provenance, location, source, file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (edge.src, edge.relation.value, edge.dst_symbol, edge.dst_id,
                 edge.weight, edge.provenance.value, edge.location, edge.source,
                 file or _file_of(edge.src)),
            )
        except (UnicodeEncodeError, ValueError):
            # An edge touching a non-UTF-8 / NUL id (src, dst_symbol or dst_id) can't be
            # bound by sqlite; skip it rather than abort reindex. Its endpoint node is
            # dropped for the same reason, so no dangling edge survives (panel R11A).
            return

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
            # Deleting a file can sever one target of an ambiguous fan-out (caller ->
            # [a, b] as two AMBIGUOUS edges); _invalidate_dangling just turned that
            # edge into a hole even though the reference is still satisfied by the
            # surviving sibling. Drop those phantom holes (else find_holes over-counts),
            # then re-resolve any genuine hole the deletion may have disambiguated.
            self._drop_redundant_holes()
            self._resolve_worklist()
            self._dedup_resolved_edges()

    def _dedup_resolved_edges(self) -> None:
        """Collapse duplicate resolved edges and drop redundant REFERENCES — the DB-level
        twin of reindex's in-memory `_dedup_edges`. The incremental path bulk-inserts and
        resolves edges without that pass, so it could leave parallel rows that inflate
        fan_in / pagerank (panel R12B): a function named like its module collapses to one
        node yet keeps two outbound edges per call site, and resolving a hole whose symbol
        already has a resolved sibling adds a second row. Holes (dst_id IS NULL) are
        distinct reference sites and are left untouched."""
        # 1. one row per (src, relation, dst_id): drop any that has a strictly-better
        #    sibling (higher weight, or equal weight with a lower id), keeping one best row.
        self.conn.execute(
            """DELETE FROM edges
                WHERE dst_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM edges b
                               WHERE b.dst_id IS NOT NULL
                                 AND b.src = edges.src AND b.relation = edges.relation
                                 AND b.dst_id = edges.dst_id
                                 AND (b.weight > edges.weight
                                      OR (b.weight = edges.weight AND b.id < edges.id)))"""
        )
        # 2. a CALLS edge subsumes a REFERENCES to the same (src, dst), and a REFERENCES
        #    self-loop carries no liveness/impact meaning — both only inflate degree metrics
        #    (a recursive CALLS self-loop is meaningful and kept). Mirrors `_dedup_edges`.
        self.conn.execute(
            """DELETE FROM edges
                WHERE relation = ? AND dst_id IS NOT NULL
                  AND (src = dst_id
                       OR EXISTS (SELECT 1 FROM edges c
                                   WHERE c.relation = ? AND c.src = edges.src
                                     AND c.dst_id = edges.dst_id))""",
            (Relation.REFERENCES.value, Relation.CALLS.value),
        )

    def _drop_redundant_holes(self) -> None:
        """Delete unresolved edges that already have a resolved sibling.

        A sibling shares (src, relation, dst_symbol): the reference is still linked,
        so the hole is a phantom left by invalidating one arm of an ambiguous
        fan-out. Dropping it (rather than re-resolving to the surviving target)
        avoids both the spurious hole and a duplicate edge that would inflate
        fan_in (panel R11B). Genuine holes — no resolved sibling — are kept.
        """
        self.conn.execute(
            """DELETE FROM edges
                WHERE dst_id IS NULL
                  AND EXISTS (SELECT 1 FROM edges r
                               WHERE r.dst_id IS NOT NULL
                                 AND r.src = edges.src
                                 AND r.relation = edges.relation
                                 AND r.dst_symbol = edges.dst_symbol)"""
        )

    def _resolve_worklist(self) -> None:
        """Relink unresolved edges whose dst_symbol now matches known node(s).

        A unique match resolves in place. An *ambiguous* match (the name now resolves
        to several nodes) over-approximates to every candidate as AMBIGUOUS edges —
        mirroring the extractors (`python.py:_ref_edges`, `treesitter.py:_ref`) so the
        incremental path never under-counts reachability and calls a live symbol dead
        (precision over recall). This was the lone resolution site that linked to only
        one candidate.
        """
        self.conn.execute(
            """UPDATE edges
                  SET dst_id = (SELECT n.id FROM nodes n WHERE n.name = edges.dst_symbol LIMIT 1)
                WHERE dst_id IS NULL
                  AND (SELECT COUNT(*) FROM nodes n WHERE n.name = edges.dst_symbol) = 1"""
        )
        ambiguous = self.conn.execute(
            """SELECT * FROM edges
                WHERE dst_id IS NULL
                  AND (SELECT COUNT(*) FROM nodes n WHERE n.name = edges.dst_symbol) >= 2"""
        ).fetchall()
        for row in ambiguous:
            cands = [r["id"] for r in self.conn.execute(
                "SELECT id FROM nodes WHERE name = ?", (row["dst_symbol"],)).fetchall()]
            w = round(1.0 / len(cands), 3)
            self.conn.execute("DELETE FROM edges WHERE id = ?", (row["id"],))
            for cid in cands:
                self.conn.execute(
                    """INSERT INTO edges(src, relation, dst_symbol, dst_id, weight,
                                         provenance, location, source, file)
                       VALUES (?, ?, ?, ?, ?, 'ambiguous', ?, ?, ?)""",
                    (row["src"], row["relation"], row["dst_symbol"], cid, w,
                     row["location"], row["source"], row["file"]))

    def _invalidate_dangling(self) -> None:
        """Any resolved edge pointing at a now-missing node reverts to a hole."""
        self.conn.execute(
            """UPDATE edges SET dst_id = NULL
                WHERE dst_id IS NOT NULL
                  AND dst_id NOT IN (SELECT id FROM nodes)"""
        )

    # -- reads -------------------------------------------------------------
    def nodes_by_name(self, name: str) -> list[Node]:
        try:
            rows = self.conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchall()
        except (UnicodeEncodeError, ValueError):
            # A user-supplied name with a lone surrogate (invalid-UTF-8 argv via
            # surrogateescape) or an embedded NUL can't be a stored symbol; sqlite raises
            # on bind. Refuse with no match rather than crash the op (panel XXX).
            return []
        return [_row_to_node(r) for r in rows]

    def get_node(self, node_id: str) -> Node | None:
        try:
            row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        except (UnicodeEncodeError, ValueError):
            return None  # non-UTF-8 / NUL id can't exist; refuse, don't crash (panel XXX)
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
