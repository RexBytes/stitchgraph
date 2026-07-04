"""SQLite adjacency store — the source of truth (design §4).

Stdlib-only (sqlite3). Matrices are *derived* from this on demand (design
principle 2); they are never stored as primary state.

Read-only invariant: this DB is stitchgraph's own index. stitchgraph never
writes to analyzed source — only here.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Iterator
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

def _canonical_columns() -> dict[str, dict[str, str]]:
    """`{table: {column: ALTER-ready DDL}}` derived from `_SCHEMA` by materializing it in a
    throwaway in-memory DB and reading `PRAGMA table_info`. `_migrate` uses this so its
    backfill set is always exactly the schema's columns — no hand-maintained list to drift.
    The reconstructed DDL preserves type, NOT NULL, and DEFAULT (PRAGMA `dflt_value` is
    already a SQL literal), which is what `ALTER TABLE ADD COLUMN` needs for NOT NULL cols.
    Cheap (one in-memory schema build) and only on the rare construct-on-old-DB path."""
    out: dict[str, dict[str, str]] = {}
    with closing(sqlite3.connect(":memory:")) as probe:
        probe.row_factory = sqlite3.Row
        probe.executescript(_SCHEMA)
        for table in ("nodes", "edges"):
            cols: dict[str, str] = {}
            for r in probe.execute(f"PRAGMA table_info({table})"):
                ddl = f"{r['name']} {r['type']}"
                if r["notnull"]:
                    ddl += " NOT NULL"
                if r["dflt_value"] is not None:
                    ddl += f" DEFAULT {r['dflt_value']}"
                cols[r["name"]] = ddl
            out[table] = cols
    return out


def _node_lang(node_id: str) -> str | None:
    """Canonical language bucket for a node id, from its file extension — or None when
    unknown (a pseudo node like `db::`/`var::`/`route::`, or an extension we don't map).
    Reuses the extractor's EXT_LANG/_canon_lang (lazily, no import cycle) so the store's
    notion of language can't drift from the extractor's (C/C++ share one bucket)."""
    file = node_id.split("::", 1)[0]
    ext = Path(file).suffix
    if ext == ".py":
        return "python"
    from .extract.treesitter import EXT_LANG, _canon_lang
    lang = EXT_LANG.get(ext)
    return _canon_lang(lang) if lang else None


def _same_lang(id_a: str, id_b: str) -> int:
    """1 if two node ids are in the same language family, OR if either language is unknown
    (recall-safe: never filter out a possibly-valid bind, which could flag live code dead —
    the cardinal sin). Registered as a SQLite function so incremental name resolution stays
    per-language, matching the full-reindex `by_lang` bucketing (panel R34A); the language-
    blind store path otherwise bound e.g. a Rust `helper()` to a Go `helper`, inflating
    fan_in/get_callers vs a full reindex."""
    la, lb = _node_lang(id_a), _node_lang(id_b)
    if la is None or lb is None:
        return 1
    return 1 if la == lb else 0


class Store:
    """The graph store. Use as a context manager or call .close()."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            # Concurrency pragmas for the advertised multi-process workflow (`watch` writing
            # while the MCP server / CLI reads the same DB): WAL lets readers proceed during a
            # multi-second reindex commit, and busy_timeout retries instead of failing with
            # 'database is locked' the moment two openers overlap (review 2026-07-03, F10a).
            # WAL is a persistent DB property and is a no-op if already set; :memory: has no
            # journal. Failure is non-fatal (e.g. a read-only filesystem) — behave as before.
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA busy_timeout=10000")
            except sqlite3.Error:
                pass
        # Per-language name resolution: keeps the incremental resolver from binding a bare
        # name across languages (Rust helper -> Go helper), matching full reindex (panel R34A).
        self.conn.create_function("same_lang", 2, _same_lang, deterministic=True)
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
        """Backfill every column the canonical schema declares that an older index file
        lacks (forward-compatible). The wanted set is DERIVED from `_SCHEMA` itself, so the
        backfill can never drift out of sync with the schema again: a hand-maintained list
        previously omitted `nodes.file` (crashed `_INDEXES` on `idx_nodes_file`) and
        `edges.location` (crashed `_row_to_edge` reads) on old DBs (panels R28A/R28B)."""
        for table, cols in _canonical_columns().items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols.items():
                if name not in have:
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
                # Two defs can share one node id — most commonly same-name METHOD OVERLOADS
                # (`void f()` / `void f(int)` in Java/C#/C++, both qualified `Class.f`). A plain
                # INSERT OR REPLACE made the LAST-written overload's row win outright, CLOBBERING
                # the earlier one's roles: a public API method (`exported`) overloaded with a
                # private same-name helper declared after it, or a framework-callback overload
                # (@PostConstruct/@Test) followed by a plain one, lost its only root and was
                # confidently flagged dead though live (#61, cardinal). Upsert instead and UNION
                # the roles — a role is never dropped (cardinal-safe; the edges from both bodies
                # are already kept, since edges key on src id, so only the row's roles were at
                # risk). Duplicates in the joined string are harmless: every reader splits into a
                # set (`get_node` -> frozenset; `nodes_with_role` LIKE; `_set_exported_roles`
                # normalizes), and reindex/replace_file both clear before re-inserting, so the
                # union is bounded to one build's overloads. Non-role columns take the new row,
                # matching the prior REPLACE semantics.
                """INSERT INTO nodes(id, kind, name, location, file, is_stub, arity, summary, roles, end_line)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     kind=excluded.kind, name=excluded.name, location=excluded.location,
                     file=excluded.file, is_stub=excluded.is_stub, arity=excluded.arity,
                     summary=excluded.summary, end_line=excluded.end_line,
                     roles=CASE
                       WHEN excluded.roles='' THEN nodes.roles
                       WHEN nodes.roles='' THEN excluded.roles
                       ELSE nodes.roles || ',' || excluded.roles
                     END""",
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
        if not row:
            return None
        # A non-str value (a BLOB from a corrupt/tampered index) would crash a string
        # consumer (e.g. `load_config(Path(value))`); treat it as absent (panel R31B).
        val = row["value"]
        return val if isinstance(val, str) else None

    def replace_file(self, file: str, nodes: Iterable[Node], edges: Iterable[Edge],
                     *, exported_ids: set[str] | None = None) -> None:
        """Incremental update for one file (design §4, Store & incremental updates).

        1. Delete nodes/edges owned by this file.
        2. Insert the freshly-extracted nodes/edges.
        3. Re-resolve the unresolved worklist against new nodes.
        4. Invalidate inbound edges whose target id no longer exists.

        `exported_ids` (optional): the COMPLETE set of node ids carrying the `exported` role
        in a whole-project extract — `{n.id for n in nodes if "exported" in n.roles}`. When
        given, the `exported` role is re-applied to match it exactly (set where in, cleared
        where out), so an incremental update that changes a package __init__'s re-export
        surface converges with a full reindex. Without it, replace_file is a single-file
        update and cannot see another file's export change, leaving a newly re-exported symbol
        flagged dead (panel R37A) — incremental callers should pass it, derived from the same
        whole-project extract that produced `nodes`/`edges`.
        """
        nodes = list(nodes)
        edges = list(edges)
        try:
            self.conn.execute("SELECT ? ", (file,))  # probe: a lone-surrogate / NUL file
        except (UnicodeEncodeError, ValueError):
            # name (POSIX surrogateescape on a non-UTF-8 path) can't be bound by sqlite —
            # skip the whole update rather than raise, mirroring add_node/add_edge (panel R15B).
            return
        # A `runtime` role is set by ingest_trace from a coverage trace — a fact about
        # execution, orthogonal to extraction. The freshly-extracted Node objects carry
        # no runtime role, so a naive delete+re-insert erases it: a function observed
        # executing would lose its seed and get flagged dead on the next incremental
        # update (cardinal sin), while `has_runtime` meta stayed set and inflated
        # find_stale's confidence to 0.78 (panel R33A). Carry the role across for every
        # id that survives the re-extraction (add_role no-ops on a vanished id).
        runtime_ids = {
            row["id"] for row in self.conn.execute(
                "SELECT id, roles FROM nodes WHERE file = ?", (file,))
            if "runtime" in (row["roles"] or "").split(",")
        }
        with self.conn:  # single transaction
            self.conn.execute("DELETE FROM nodes WHERE file = ?", (file,))
            self.conn.execute("DELETE FROM edges WHERE file = ?", (file,))
            for n in nodes:
                self.add_node(n, file=file)
            for e in edges:
                self.add_edge(e, file=file)
            for nid in runtime_ids:
                self.add_role(nid, "runtime")
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
            if exported_ids is not None:
                self._set_exported_roles(exported_ids)

    def _set_exported_roles(self, exported_ids: set[str]) -> None:
        """Make the cross-file `exported` role match `exported_ids` exactly — set on a node
        whose id is in it, cleared from one whose id is not. The caller derives the set from a
        whole-project extract (`{n.id for n in nodes if "exported" in n.roles}`), which already
        did the precise per-node assignment (incl. exported-class public methods), so this is
        an exact convergence with full reindex without re-deriving any export logic in the
        store (panel R37A)."""
        for row in self.conn.execute("SELECT id, roles FROM nodes").fetchall():
            roles = set((row["roles"] or "").split(",")) - {""}
            want = row["id"] in exported_ids
            if want == ("exported" in roles):
                continue
            roles = roles | {"exported"} if want else roles - {"exported"}
            self.conn.execute("UPDATE nodes SET roles = ? WHERE id = ?",
                              (",".join(sorted(roles)), row["id"]))

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
            cands = self._name_candidates(g["dst_symbol"], g["relation"], g["src"])
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

    def _name_candidates(self, dst_symbol: str, relation: str,
                         src: str | None = None) -> list[str]:
        """Node ids a bare-name reference can resolve to. Excludes MODULE nodes for any
        non-IMPORTS relation: a CALLS/REFERENCES/INHERITS to a bare name is never a module
        (mirrors the extractor's `_ref_edges`, panel R13B) — resolving one to a same-named
        module inflated that module's fan_in on the incremental path (panel R31A). IMPORTS
        keeps module resolution (`from pkg import submodule`). When `src` is given, restricts
        candidates to the SAME language family as the referencing node, matching the full
        reindex's per-language bucketing (panel R34A)."""
        sql = "SELECT id FROM nodes WHERE name = ?"
        params: list[object] = [dst_symbol]
        if relation != Relation.IMPORTS.value:
            sql += " AND kind != ?"
            params.append(NodeKind.MODULE.value)
        if src is not None:
            sql += " AND same_lang(id, ?)"
            params.append(src)
        return [r["id"] for r in self.conn.execute(sql, params).fetchall()]

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
        never linked; AMBIGUOUS, so it only adds reachability, never under-counts.

        Constant-memory (2026-07-03 HA field report): Python touches only SYMBOL-scale data
        — class ids, the INHERITS closure, node ids, and the distinct edge *targets*. The
        edge scan and the widened inserts run inside SQLite. The previous body fetchall'd
        every resolved CALLS/REFERENCES row plus a seen-set of their key tuples — O(edges)
        Python memory, the one allocation the streaming reindex's per-file drain didn't
        remove, and it OOM'd Home Assistant's ~16M-edge graph in the endgame after the
        whole index had streamed at a flat ~113 MB."""
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

        # Widening map at symbol scale: for each DISTINCT bound target that is a class
        # member, every existing override of it below the class. (base, override, method)
        # rows — O(actual overrides), never O(edges).
        node_ids = {r["id"] for r in self.conn.execute("SELECT id FROM nodes")}
        pairs: list[tuple[str, str, str]] = []
        for r in self.conn.execute(
                """SELECT DISTINCT dst_id FROM edges
                    WHERE dst_id IS NOT NULL AND relation IN (?, ?)""",
                (Relation.CALLS.value, Relation.REFERENCES.value)):
            base_id = _owner_scope(r["dst_id"])
            if base_id is None or base_id not in class_ids:
                continue
            method = r["dst_id"].rsplit(".", 1)[1]
            for sub_id in descendants(base_id):
                override = f"{sub_id}.{method}"
                if override != r["dst_id"] and override in node_ids:
                    pairs.append((r["dst_id"], override, method))
        if not pairs:
            return
        self.conn.execute("DROP TABLE IF EXISTS temp._widen")
        self.conn.execute("CREATE TEMP TABLE _widen (base TEXT, override TEXT, method TEXT)")
        self.conn.executemany("INSERT INTO _widen VALUES (?, ?, ?)", pairs)
        self.conn.execute("CREATE INDEX idx_widen_base ON _widen(base)")
        # Materialise the candidates BEFORE inserting (the in-memory twin iterates a
        # snapshot of the edge list — a widened edge must never seed further widening).
        # rn = 1 keeps the first triggering edge per (src, relation, override) key in
        # insertion (rowid) order, matching the twin's first-wins seen-set; NOT EXISTS
        # skips keys that already exist as real edges, matching its seen-set seeding.
        self.conn.execute("DROP TABLE IF EXISTS temp._widen_out")
        self.conn.execute(
            """CREATE TEMP TABLE _widen_out AS
               SELECT src, relation, method, override, location, source, file
                 FROM (SELECT e.src AS src, e.relation AS relation, w.method AS method,
                              w.override AS override, e.location AS location,
                              e.source AS source, e.file AS file,
                              ROW_NUMBER() OVER (PARTITION BY e.src, e.relation, w.override
                                                 ORDER BY e.id) AS rn
                         FROM edges e JOIN _widen w ON w.base = e.dst_id
                        WHERE e.relation IN (?, ?)
                          AND NOT EXISTS (SELECT 1 FROM edges p
                                           WHERE p.src = e.src AND p.relation = e.relation
                                             AND p.dst_id = w.override))
                WHERE rn = 1""",
            (Relation.CALLS.value, Relation.REFERENCES.value))
        self.conn.execute(
            """INSERT INTO edges(src, relation, dst_symbol, dst_id, weight,
                                 provenance, location, source, file, name_based)
               SELECT src, relation, method, override, 1.0, 'ambiguous',
                      location, source, file, 0
                 FROM _widen_out""")
        self.conn.execute("DROP TABLE temp._widen_out")
        self.conn.execute("DROP TABLE temp._widen")

    def _dedup_resolved_edges(self) -> None:
        """Collapse duplicate resolved edges and drop redundant REFERENCES — the DB-level
        twin of reindex's in-memory `_dedup_edges`. The incremental path bulk-inserts and
        resolves edges without that pass, so it could leave parallel rows that inflate
        fan_in / pagerank (panel R12B): a function named like its module collapses to one
        node yet keeps two outbound edges per call site, and resolving a hole whose symbol
        already has a resolved sibling adds a second row. Holes (dst_id IS NULL) are
        distinct reference sites and are left untouched."""
        # 0. Preserve name-based-ness across duplicates BEFORE collapsing them. A declared-
        #    type call emits both a precise (name_based=0) and a widening (name_based=1) edge
        #    to its declared target; step 1 keeps the higher-weight (precise) row and would
        #    drop the name_based marker, so the group could never re-widen to a homonym added
        #    later, flagging it dead (panel R23A, cardinal). OR the flag onto every row of a
        #    (src, relation, dst_id) group so the survivor stays re-widenable.
        self.conn.execute(
            """UPDATE edges SET name_based = 1
                WHERE dst_id IS NOT NULL AND name_based = 0
                  AND EXISTS (SELECT 1 FROM edges b
                               WHERE b.dst_id IS NOT NULL AND b.name_based = 1
                                 AND b.src = edges.src AND b.relation = edges.relation
                                 AND b.dst_id = edges.dst_id)"""
        )
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
        """Delete unresolved edges that already have a resolved sibling OF THE SAME KIND.

        A sibling shares (src, relation, dst_symbol) AND name_based-ness: the hole is a
        phantom left by invalidating one arm of an ambiguous fan-out (all name-based), and
        the surviving same-kind arm still links the reference. Dropping it avoids the
        spurious hole and a fan_in-inflating duplicate (panel R11B).

        The name_based match is essential: a name-based widening hole (a reference to one of
        several same-named members) must NOT be considered satisfied by an unrelated PRECISE
        edge to a different class's same-named member — that hole is a real pending link, and
        dropping it leaves the genuine target unreferenced and flagged dead once indexed
        (panel R25A, cardinal). Genuine holes with no same-kind resolved sibling are kept.
        """
        self.conn.execute(
            """DELETE FROM edges
                WHERE dst_id IS NULL
                  AND EXISTS (SELECT 1 FROM edges r
                               WHERE r.dst_id IS NOT NULL
                                 AND r.src = edges.src
                                 AND r.relation = edges.relation
                                 AND r.dst_symbol = edges.dst_symbol
                                 AND r.name_based = edges.name_based)"""
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
        # A bare CALLS/REFERENCES/INHERITS reference is never a module — exclude MODULE
        # candidates for any non-IMPORTS relation (the correlated `edges.relation = ? OR
        # n.kind != ?`), mirroring the extractor's `_ref_edges` (panel R13B). Resolving a
        # moved-function hole to a same-named MODULE node inflated that module's fan_in on the
        # incremental path (panel R31A). IMPORTS keeps module resolution (`from pkg import sub`).
        # `same_lang(n.id, edges.src)` keeps name resolution per-language, matching the full
        # reindex's by_lang bucketing — without it a Rust call bound to a Go same-named def on
        # the incremental path, inflating fan_in/get_callers (panel R34A).
        imp, mod = Relation.IMPORTS.value, NodeKind.MODULE.value
        self.conn.execute(
            """UPDATE edges
                  SET dst_id = (SELECT n.id FROM nodes n WHERE n.name = edges.dst_symbol
                                  AND (edges.relation = ? OR n.kind != ?)
                                  AND same_lang(n.id, edges.src) LIMIT 1),
                      name_based = 1
                WHERE dst_id IS NULL
                  AND (SELECT COUNT(*) FROM nodes n WHERE n.name = edges.dst_symbol
                         AND (edges.relation = ? OR n.kind != ?)
                         AND same_lang(n.id, edges.src)) = 1""",
            (imp, mod, imp, mod),
        )
        ambiguous = self.conn.execute(
            """SELECT * FROM edges
                WHERE dst_id IS NULL
                  AND (SELECT COUNT(*) FROM nodes n WHERE n.name = edges.dst_symbol
                         AND (edges.relation = ? OR n.kind != ?)
                         AND same_lang(n.id, edges.src)) >= 2""",
            (imp, mod),
        ).fetchall()
        for row in ambiguous:
            cands = self._name_candidates(row["dst_symbol"], row["relation"], row["src"])
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
        """Revert a NAME-BASED resolved edge whose target node no longer exists back to a
        hole, so it re-resolves by name against the new node set. A PRECISE (name_based=0)
        edge is NEVER reverted: it stays pointing at its exact original id (dangling while
        that id is absent) and auto-revalidates the instant the id returns.

        This keeps a precise import/call/declared-type/seeded edge bound to its ONE true
        target and never name-rebinds it to an unrelated same-named symbol — covering both a
        forward reference to a not-yet-indexed file (panel R24A) AND a delete->re-add of the
        target's own file (panel R29A). The earlier code nullified a precise edge whose target
        lived in the replaced file; `_resolve_worklist` then rebound that hole by bare name to
        a homonym in another file and marked it name_based, so `_rewiden_resolved` widened it
        to every homonym on re-add — inflating the (often dead) homonym's fan_in.

        A precise edge to a genuinely-removed target is left dangling; `unresolved_edges`
        still surfaces it as a hole (its id is absent from `nodes`), and the graph-metric
        readers ignore an edge to a non-existent node, so nothing real is inflated.

        Also reverts a name-based NON-IMPORTS edge that now points at a MODULE node: when a
        file `helper.py` defining `def helper()` is emptied, the function and module share the
        id `helper.py::helper`, so the surviving MODULE node silently inherits an edge that was
        resolved to the function — re-targeting a module a CALLS/REFERENCES edge must never
        reach (`_ref_edges` invariant, panel R13B), inflating that module's fan_in (panel R31A).
        Nullifying it lets `_resolve_worklist` re-bind it module-excluded to the real target.
        """
        imp, mod = Relation.IMPORTS.value, NodeKind.MODULE.value
        self.conn.execute(
            """UPDATE edges SET dst_id = NULL
                WHERE dst_id IS NOT NULL AND name_based = 1
                  AND (dst_id NOT IN (SELECT id FROM nodes)
                       OR (relation != ?
                           AND dst_id IN (SELECT id FROM nodes WHERE kind = ?)))""",
            (imp, mod),
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
        return [n for r in rows if (n := _row_to_node(r))]

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
        return [n for r in rows if (n := _row_to_node(r))]

    def all_node_ids(self) -> list[str]:
        # This raw projection bypasses `_row_to_node`, so guard the corrupt-index case here
        # too: a non-str `id` (a BLOB from external tampering / bit-rot) can't be a real node
        # id and would crash callers that do string ops on it (get_matrix/risk) — drop it, so
        # every op returns a Result instead of raising (panel R30B, mirrors R29B row mappers).
        return [r["id"] for r in self.conn.execute("SELECT id FROM nodes").fetchall()
                if isinstance(r["id"], str)]

    def all_nodes_full(self) -> list[Node]:
        return [n for r in self.conn.execute("SELECT * FROM nodes").fetchall()
                if (n := _row_to_node(r))]

    def nodes_with_role(self, role: str) -> list[Node]:
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE (',' || roles || ',') LIKE ?",
            (f"%,{role},%",),
        ).fetchall()
        return [n for r in rows if (n := _row_to_node(r))]

    def stub_nodes(self) -> list[Node]:
        rows = self.conn.execute("SELECT * FROM nodes WHERE is_stub = 1").fetchall()
        return [n for r in rows if (n := _row_to_node(r))]

    def callers_of(self, node_id: str, relation: Relation = Relation.CALLS) -> list[Edge]:
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE dst_id = ? AND relation = ?",
            (node_id, relation.value),
        ).fetchall()
        return [e for r in rows if (e := _row_to_edge(r))]

    def callees_of(self, node_id: str, relation: Relation = Relation.CALLS) -> list[Edge]:
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE src = ? AND relation = ? AND dst_id IS NOT NULL",
            (node_id, relation.value),
        ).fetchall()
        return [e for r in rows if (e := _row_to_edge(r))]

    def unresolved_edges(self) -> list[Edge]:
        """Dangling references — the substrate of find_holes (design §4/§6.D).

        A hole is an edge with no resolvable target: either dst_id IS NULL (never resolved),
        or dst_id points at a node that no longer exists (a PRECISE edge whose target file was
        deleted — `_invalidate_dangling` keeps it bound to its exact id rather than nullifying
        and mis-widening it, so find_holes must recognise the missing-target form too, panel
        R29A; mirrors GraphBLAS dropping edges to ids outside the node set).

        Synthetic `_propagate_overrides` edges (provenance='ambiguous' AND name_based=0 — the
        only edges with that combination) are NOT source references: they are derived liveness
        links to subclass overrides, regenerated each update. A stale one left dangling by a
        subclass file's deletion is not a real broken reference, so it is excluded here rather
        than reported as a spurious hole (panel R30A, non-blocking find_holes over-count)."""
        rows = self.conn.execute(
            """SELECT * FROM edges
                WHERE dst_id IS NULL
                   OR (dst_id NOT IN (SELECT id FROM nodes)
                       AND NOT (provenance = 'ambiguous' AND name_based = 0))"""
        ).fetchall()
        return [e for r in rows if (e := _row_to_edge(r))]

    def unresolved_count(self) -> int:
        """COUNT twin of `unresolved_edges` (same predicate, no row materialisation) for
        callers that only need the tally — the streaming reindex's final holes count must
        not undo its constant-memory work by building an Edge per hole (2026-07-03)."""
        return self.conn.execute(
            """SELECT COUNT(*) AS c FROM edges
                WHERE dst_id IS NULL
                   OR (dst_id NOT IN (SELECT id FROM nodes)
                       AND NOT (provenance = 'ambiguous' AND name_based = 0))"""
        ).fetchone()["c"]

    def iter_resolved_full(self) -> Iterator[Edge]:
        """Stream resolved edges as full `Edge` objects one at a time (cursor-iterated, no
        fetchall) — for consumers that need fields beyond `iter_resolved`'s lean tuple
        (provenance, weight) but must never hold the O(edges) list. `scan`'s
        EXTRACTED-only liveness sweep on Home Assistant's 16M-edge graph MemoryError'd
        at 6 GB through `resolved_edges()` (field analysis 2026-07-03); one-at-a-time
        keeps that sweep at adjacency scale. Same row set + corrupt-row filtering as
        `resolved_edges()`."""
        for r in self.conn.execute("SELECT * FROM edges WHERE dst_id IS NOT NULL"):
            e = _row_to_edge(r)
            if e is not None:
                yield e

    def resolved_edges(self, relation: Relation | None = None) -> list[Edge]:
        if relation is None:
            rows = self.conn.execute("SELECT * FROM edges WHERE dst_id IS NOT NULL").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM edges WHERE dst_id IS NOT NULL AND relation = ?",
                (relation.value,),
            ).fetchall()
        return [e for r in rows if (e := _row_to_edge(r))]

    def iter_resolved(
        self, relation: Relation | None = None,
    ) -> Iterator[tuple[str, str, str, float]]:
        """Stream resolved edges as lean `(src, relation, dst_id, weight)` tuples, cursor-
        iterated (no `fetchall`, no `Edge` construction). The reachability / centrality sweeps
        only read those four columns, so this is what they build their adjacency from — a
        16M-edge graph (Home Assistant) then materialises ~3 int columns, not 16M `Edge`
        objects, keeping the *query* peak bounded the way the *index* peak already is.
        `relation` is returned as its raw stored string; compare against a set of
        `Relation.value` (see callers), not the enum, to avoid per-row enum coercion.

        Rows whose `relation` isn't a known `Relation` are skipped — only possible from a
        corrupt/bit-rotted index (no writer emits one), but it keeps parity with
        `resolved_edges()`'s `_row_to_edge` drop so an unfiltered consumer (`best_path`/
        `trace_path` with `relations=None`) can't traverse a garbage edge (panel R58, opus).
        A non-finite `weight` from such an index is coerced to 1.0 (matches the Edge default)."""
        valid = {r.value for r in Relation}
        sql = "SELECT src, relation, dst_id, weight FROM edges WHERE dst_id IS NOT NULL"
        params: tuple[str, ...] = ()
        if relation is not None:
            sql += " AND relation = ?"
            params = (relation.value,)
        cur = self.conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(20000)
            if not rows:
                break
            for r in rows:
                rel = r["relation"]
                if rel not in valid:
                    continue
                src, dst = r["src"], r["dst_id"]
                if not isinstance(src, str) or not isinstance(dst, str):
                    # non-str src/dst (BLOB corruption) can't be a real resolved edge —
                    # skip, mirroring _row_to_edge (R31B). Previously only consumers that
                    # went through Edge materialization were protected (review 2026-07-03).
                    continue
                w = r["weight"]
                if not isinstance(w, (int, float)) or not math.isfinite(w):
                    w = 1.0
                yield src, rel, dst, w

    def node_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"]


# -- row mappers -----------------------------------------------------------
def _row_to_node(row: sqlite3.Row) -> Node | None:
    keys = row.keys()
    # Tolerate a row from an older/partial schema missing any optional column (panel R27A)
    # AND a corrupt index whose str-typed columns hold non-str values (a BLOB from external
    # tampering / on-disk bit-rot): a `kind` no writer emits, or a non-str `id`/`name`, can't
    # be a real node — skip the row (the caller filters None) so an op returns a Result
    # instead of raising downstream string ops (panels R29B/R31B). Other str columns are
    # coerced to "". Only id/kind/name are guaranteed columns (PRIMARY KEY / NOT NULL).
    try:
        kind = NodeKind(row["kind"])
    except ValueError:
        return None
    nid, name = row["id"], row["name"]
    if not isinstance(nid, str) or not isinstance(name, str):
        return None
    # Coerce EVERY field to its declared dataclass type rather than hand-listing the str
    # columns (which kept missing one: summary/end_line/arity slipped through rounds 29-31
    # — panel R32B). Wrong-typed optional columns degrade to the field default; a non-finite
    # check is unnecessary here (end_line/arity are ints). The `tests/oracles` corrupt-store
    # chokepoint oracle asserts this for every column, so a new column is covered by type.
    roles = row["roles"] if "roles" in keys else ""
    if not isinstance(roles, str):
        roles = ""
    location = row["location"] if "location" in keys else ""
    if not isinstance(location, str):
        location = ""
    end_line = row["end_line"] if "end_line" in keys else None
    if not isinstance(end_line, int):       # bytes / str / float(inf) / None -> drop
        end_line = None
    arity = row["arity"] if "arity" in keys else None
    if not isinstance(arity, int):
        arity = None
    summary = row["summary"] if "summary" in keys else None
    if not isinstance(summary, str):
        summary = None
    return Node(
        id=nid, kind=kind, name=name, location=location,
        end_line=end_line, is_stub=bool(row["is_stub"]) if "is_stub" in keys else False,
        arity=arity, summary=summary,
        roles=frozenset(r for r in roles.split(",") if r),
    )


def _row_to_edge(row: sqlite3.Row) -> Edge | None:
    # Tolerate a row from an older/partial schema or a column-subset projection missing any
    # optional column — only src/relation/dst_symbol are guaranteed (NOT NULL, no default)
    # — so an op never raises IndexError on such a row (panels R27A/R28B mirror _row_to_node).
    keys = row.keys()
    try:
        relation = Relation(row["relation"])
    except ValueError:
        return None  # corrupt relation: skip rather than crash (panel R29B, mirror node).
    src, dst_symbol = row["src"], row["dst_symbol"]
    if not isinstance(src, str) or not isinstance(dst_symbol, str):
        return None  # non-str src/dst_symbol (BLOB corruption) can't be a real edge (R31B).
    dst_id = row["dst_id"] if "dst_id" in keys else None
    if dst_id is not None and not isinstance(dst_id, str):
        dst_id = None  # corrupt non-str dst_id -> treat as unresolved rather than crash.
    try:
        provenance = (Provenance(row["provenance"]) if "provenance" in keys
                      else Provenance.EXTRACTED)
    except ValueError:
        provenance = Provenance.AMBIGUOUS  # corrupt provenance -> least-confident, safe.
    weight = row["weight"] if "weight" in keys else 1.0
    if not isinstance(weight, (int, float)) or not math.isfinite(weight):
        weight = 1.0  # a NaN/inf weight (corrupt index) would leak `Infinity` into JSON.
    location = row["location"] if "location" in keys else ""
    if not isinstance(location, str):
        location = ""
    source = row["source"] if "source" in keys else "tree-sitter"
    if not isinstance(source, str):
        source = "tree-sitter"
    return Edge(
        src=src, relation=relation, dst_symbol=dst_symbol, dst_id=dst_id,
        weight=weight, provenance=provenance, location=location, source=source,
        name_based=bool(row["name_based"]) if "name_based" in keys else False,
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


