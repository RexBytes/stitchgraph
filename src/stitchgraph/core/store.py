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
    file        TEXT NOT NULL DEFAULT '',     -- owning (source) file path
    name_based  INTEGER NOT NULL DEFAULT 0    -- 1 = resolved by name (re-widenable)
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
                      ("file", "file TEXT NOT NULL DEFAULT ''"),
                      ("name_based", "name_based INTEGER NOT NULL DEFAULT 0")),
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
                """INSERT INTO edges(src, relation, dst_symbol, dst_id, weight, provenance, location, source, file, name_based)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (edge.src, edge.relation.value, edge.dst_symbol, edge.dst_id,
                 edge.weight, edge.provenance.value, edge.location, edge.source,
                 file or _file_of(edge.src), int(edge.name_based)),
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
        try:
            self.conn.execute("SELECT ? ", (file,))  # probe: a lone-surrogate / NUL file
        except (UnicodeEncodeError, ValueError):
            # name (POSIX surrogateescape on a non-UTF-8 path) can't be bound by sqlite —
            # skip the whole update rather than raise, mirroring add_node/add_edge (panel R15B).
            return
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
            self._rewiden_resolved()
            self._propagate_overrides()
            self._dedup_resolved_edges()

    def _rewiden_resolved(self) -> None:
        """Re-normalize NAME-BASED resolved edges so an incremental update matches a full
        reindex of the same final state. `_resolve_worklist` only revisits holes (dst_id
        IS NULL); resolved edges are never reconsidered, so a name-based group drifts:

        * Widening (cardinal): when a prior update resolved a bare `caller -> foo` uniquely
          and a later update adds a second `foo`, the new node gets no inbound edge and
          find_stale flags it dead (panels R15B/R16A/R18A). Rebuild the name-based group
          AMBIGUOUS over ALL same-named candidates at weight 1/n.
        * Narrowing (panel R19A): when one arm of an N-way ambiguous fan-out is deleted,
          the survivors keep weight 1/N_old instead of 1/(N-1) (or 1.0 when one candidate
          remains), deflating best_path/trace_path confidence. Re-normalize the weights.

        Only edges marked `name_based` are touched: a PRECISE resolution (import by path,
        self/cls scope, declared type, or a seeded structural edge) is kept bound to its
        one target exactly as a full reindex would, never widened across same-named members
        of unrelated classes (panels R21A/R21B/R22A/R22B). On narrowing to one candidate
        the rebuilt edge is INFERRED, not EXTRACTED: the pre-widen provenance is not
        recoverable at the store layer, and under-claiming is the safe direction (issue #10).
        """
        groups = self.conn.execute(
            """SELECT DISTINCT src, relation, dst_symbol FROM edges
                WHERE dst_id IS NOT NULL AND name_based = 1"""
        ).fetchall()
        for g in groups:
            nb = self.conn.execute(
                """SELECT * FROM edges WHERE src = ? AND relation = ? AND dst_symbol = ?
                    AND dst_id IS NOT NULL AND name_based = 1""",
                (g["src"], g["relation"], g["dst_symbol"])).fetchall()
            if not nb:
                continue
            cands = [r["id"] for r in self.conn.execute(
                "SELECT id FROM nodes WHERE name = ?", (g["dst_symbol"],)).fetchall()]
            if not cands:
                continue
            if len(cands) >= 2:
                w = round(1.0 / len(cands), 3)
                if ({e["dst_id"] for e in nb} == set(cands)
                        and all(e["provenance"] == "ambiguous" and e["weight"] == w
                                for e in nb)):
                    continue  # already the correct ambiguous fan-out
                self._rebuild_name_based(g, nb[0], [(cid, w, "ambiguous") for cid in cands])
            else:
                # One candidate: rebuild only a leftover widened fan-out (several rows, or a
                # sole AMBIGUOUS one) to a single weight-1.0 INFERRED edge; a clean single
                # name-based edge (incl. a 0.95 EXTRACTED REFERENCES) is left untouched.
                widened = len(nb) > 1 or any(e["provenance"] == "ambiguous" for e in nb)
                if not widened:
                    continue
                self._rebuild_name_based(g, nb[0], [(cands[0], 1.0, "inferred")])

    def _rebuild_name_based(self, g: sqlite3.Row, tmpl: sqlite3.Row,
                            rows: list[tuple[str, float, str]]) -> None:
        """Replace a (src, relation, dst_symbol) group's name-based resolved edges with
        `rows` (dst_id, weight, provenance), reusing `tmpl`'s location/source/file."""
        self.conn.execute(
            """DELETE FROM edges WHERE src = ? AND relation = ? AND dst_symbol = ?
                AND dst_id IS NOT NULL AND name_based = 1""",
            (g["src"], g["relation"], g["dst_symbol"]))
        for dst, w, prov in rows:
            self.conn.execute(
                """INSERT INTO edges(src, relation, dst_symbol, dst_id, weight,
                                     provenance, location, source, file, name_based)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (g["src"], g["relation"], g["dst_symbol"], dst, w, prov,
                 tmpl["location"], tmpl["source"], tmpl["file"]))

    def _propagate_overrides(self) -> None:
        """Inheritance-aware override propagation, the store twin of the extractor's
        `_propagate_overrides`. A full reindex widens a CALLS/REFERENCES edge bound to a
        base-class member `B.m` (e.g. a `self.m()` dispatch, or a declared-type call) to the
        same-named override `Sub.m` on every subclass, so a live override is never flagged
        dead. On an incremental `replace_file` that adds a subclass in a DIFFERENT file, the
        base's precise edge is untouched and `_rewiden_resolved` (name-based only) won't
        reach it — so the override would be orphaned and confidently flagged dead (panel
        R22A, cardinal). Re-derive those edges here from the store's INHERITS graph.

        Scoped to the inheritance subtree (not by name), so unrelated same-named members are
        never linked; AMBIGUOUS, so it only adds reachability, never under-counts."""
        class_ids = {r["id"] for r in self.conn.execute(
            "SELECT id FROM nodes WHERE kind = ?", (NodeKind.CLASS.value,)).fetchall()}
        if not class_ids:
            return
        subclasses: dict[str, set[str]] = {}
        for e in self.conn.execute(
                "SELECT src, dst_id FROM edges WHERE relation = ? AND dst_id IS NOT NULL",
                (Relation.INHERITS.value,)).fetchall():
            if e["src"] in class_ids and e["dst_id"] in class_ids and e["src"] != e["dst_id"]:
                subclasses.setdefault(e["dst_id"], set()).add(e["src"])
        if not subclasses:
            return
        cache: dict[str, set[str]] = {}

        def descendants(base_id: str) -> set[str]:
            if base_id in cache:
                return cache[base_id]
            out: set[str] = set()
            stack = list(subclasses.get(base_id, ()))
            while stack:
                s = stack.pop()
                if s in out:
                    continue
                out.add(s)
                stack.extend(subclasses.get(s, ()))
            cache[base_id] = out
            return out

        node_ids = {r["id"] for r in self.conn.execute("SELECT id FROM nodes").fetchall()}
        edges = self.conn.execute(
            """SELECT src, relation, dst_symbol, dst_id, location, source, file FROM edges
                WHERE dst_id IS NOT NULL AND relation IN (?, ?)""",
            (Relation.CALLS.value, Relation.REFERENCES.value)).fetchall()
        seen = {(e["src"], e["relation"], e["dst_id"]) for e in edges}
        for e in edges:
            base_id = _owner_scope(e["dst_id"])
            if base_id is None or base_id not in class_ids:
                continue
            method = e["dst_id"].rsplit(".", 1)[1]
            for sub_id in descendants(base_id):
                override = f"{sub_id}.{method}"
                if override == e["dst_id"] or override not in node_ids:
                    continue
                key = (e["src"], e["relation"], override)
                if key in seen:
                    continue
                seen.add(key)
                self.conn.execute(
                    """INSERT INTO edges(src, relation, dst_symbol, dst_id, weight,
                                         provenance, location, source, file, name_based)
                       VALUES (?, ?, ?, ?, 1.0, 'ambiguous', ?, ?, ?, 0)""",
                    (e["src"], e["relation"], method, override,
                     e["location"], e["source"], e["file"]))

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
        # Resolving a hole here is BY NAME (the only clue is dst_symbol), so mark the
        # result name_based — if more homonyms are added later, `_rewiden_resolved` must be
        # free to re-widen it (a precise import whose target wasn't yet indexed degrades to
        # this name match; it must stay re-widenable so the real target is linked when it
        # arrives — cardinal-safe, panel R22 convergence).
        self.conn.execute(
            """UPDATE edges
                  SET dst_id = (SELECT n.id FROM nodes n WHERE n.name = edges.dst_symbol LIMIT 1),
                      name_based = 1
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
                                         provenance, location, source, file, name_based)
                       VALUES (?, ?, ?, ?, ?, 'ambiguous', ?, ?, ?, 1)""",
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
        except (sqlite3.Error, UnicodeEncodeError, ValueError, TypeError, OverflowError):
            # A name that can't be a stored symbol: a lone surrogate / embedded NUL
            # (ValueError/UnicodeEncodeError on bind), a non-str type — list/dict/bytes —
            # which sqlite rejects as an unsupported bind (sqlite3.ProgrammingError/TypeError),
            # or an int beyond SQLite's signed-64-bit range (OverflowError, panel R20B).
            # Refuse with no match rather than crash the op (panels XXX/R18B).
            return []
        return [_row_to_node(r) for r in rows]

    def get_node(self, node_id: str) -> Node | None:
        try:
            row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        except (sqlite3.Error, UnicodeEncodeError, ValueError, TypeError, OverflowError):
            # non-UTF-8 / NUL / non-str id, or an out-of-range int (OverflowError, panel
            # R20B), can't be a stored id; refuse, don't crash (R18B).
            return None
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
        # Tolerate a row that predates the column (e.g. a projection that didn't select it).
        name_based=bool(row["name_based"]) if "name_based" in row.keys() else False,
    )


def _file_of(node_id: str) -> str:
    """Owning file path is the part of the id before '::' (design §4 ids)."""
    return node_id.split("::", 1)[0] if "::" in node_id else ""


def _owner_scope(node_id: str) -> str | None:
    """The enclosing class/scope of a member id (`file::A.B.m` -> `file::A.B`), or None for
    a top-level (module-level) symbol. Splits on '::' first so the file extension's dot is
    never mistaken for a qualname separator."""
    file, sep, qual = node_id.partition("::")
    if not sep or "." not in qual:
        return None
    return f"{file}::{qual.rsplit('.', 1)[0]}"


