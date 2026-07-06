# 20 — Homonym-group edge compression: design

*2026-07-06 · the arc promised by v3.40.0's honest benchmark (docs/PERFORMANCE.md):
index time at framework-Python edge density is bounded by edge volume, not
parsing. This is the design for cutting the volume.*

## The measurement (Django 5.2, v3.40.0 indexer)

966 files, 12,344 nodes, **669,944 resolved edge rows** — of which
**644,255 (96.2%) are AMBIGUOUS** homonym fan-out arms: one row per candidate
per call site, emitted so a live symbol is never called dead (the cardinal
rule). The redundancy:

| representation | rows |
|---|---|
| today: one row per (site × candidate) | 644,255 |
| distinct (src, relation, name) source-sites | 24,762 |
| distinct candidate **sets** (content-addressed) | 1,801 |
| member rows for those sets | 12,241 |
| **compressed: site rows + member rows** | **37,003 (5.7%)** |
| whole edges table after | 62,692 (**9.4%** of 669,944) |

The win is concentrated exactly where it hurts: two sets with 100–999 members
each account for 301K of today's rows (856 member rows compressed); the
10–99-member band covers another 274K (6.8K compressed). django.db is 292 MB;
~10× fewer rows puts it around 30–50 MB. At Home Assistant scale
(26.8M-edge class, 21 GB, 62-minute index bounded by row materialisation +
SQLite insertion) the same structure predicts a db in the low GB and an index
time cut by the insertion share of those 25M+ redundant rows.

## Why interning must be content-addressed, not name-keyed

Empirically, **334 of Django's 1,342 (relation, name) groups have more than
one distinct candidate set across sources**. The emitter survey explains why
the set is *not* a pure function of the name:

- `_ref_edges` (the dominant emitter) *is* name-pure: `by_name[name]` minus
  module ids, weight `round(1/n, 3)`, `name_based=1`.
- `_propagate_overrides` (both the extractor and store twins) widens along the
  **INHERITS subtree** of the bound base member — per-source sets, weight 1.0,
  `name_based=0`.
- `_import_edge` narrows candidates by the import's **package base**.
- treesitter `_ref` excludes **the source itself** for INHERITS/IMPORTS/REFERENCES.
- grpcproto filters same-named methods by **service-name substring**;
  routes/events/express/djangotpl/ormx/openapi use per-resolver base weights
  (0.7–0.9) or fixed 0.5 arms over differently-filtered indexes.
- store-side `_rewiden_resolved` recomputes sets with a **language-family
  filter** (`same_lang(id, src)`).

A name-keyed join would silently merge these. Content-addressing — the set is
identified by the sorted tuple of its member ids — represents every emitter's
output exactly, and identical sets still share one definition (Django: 1,801
distinct sets serve 24,762 sites).

One more measured fact the schema leans on: **within every
(src, relation, dst_symbol, provenance=ambiguous) group, weight, location,
name_based and source are uniform across arms** (0 exceptions in Django; it
holds by construction in every emitter — arms of one widening share one call
site). So a single group row carries all per-arm attributes; only `dst_id`
varies, and that lives in the shared set.

## Schema

```sql
CREATE TABLE cand_sets (
    set_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sig    TEXT UNIQUE NOT NULL          -- '\x1f'-joined sorted member ids
);
CREATE TABLE cand_members (
    set_id INTEGER NOT NULL,
    dst_id TEXT    NOT NULL,
    PRIMARY KEY (set_id, dst_id)
) WITHOUT ROWID;
CREATE INDEX idx_members_dst ON cand_members(dst_id);   -- callers_of direction

CREATE TABLE edge_groups (               -- one row per widened source-site
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    src        TEXT NOT NULL,
    relation   TEXT NOT NULL,
    dst_symbol TEXT NOT NULL,
    set_id     INTEGER NOT NULL REFERENCES cand_sets(set_id),
    weight     REAL NOT NULL,            -- the uniform per-arm weight
    provenance TEXT NOT NULL DEFAULT 'ambiguous',
    location   TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,
    file       TEXT NOT NULL DEFAULT '',
    name_based INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_groups_src    ON edge_groups(src);
CREATE INDEX idx_groups_set    ON edge_groups(set_id);
CREATE INDEX idx_groups_file   ON edge_groups(file);
CREATE INDEX idx_groups_symbol ON edge_groups(dst_symbol);

CREATE VIEW edges_all AS                 -- the read-path contract
  SELECT src, relation, dst_symbol, dst_id, weight, provenance,
         location, source, file, name_based
  FROM edges WHERE dst_id IS NOT NULL
  UNION ALL
  SELECT g.src, g.relation, g.dst_symbol, m.dst_id, g.weight, g.provenance,
         g.location, g.source, g.file, g.name_based
  FROM edge_groups g JOIN cand_members m ON m.set_id = g.set_id;
```

The flat `edges` table stays: holes (`dst_id IS NULL`), single-candidate
resolutions, override-propagation singles, and any group that fails the
uniformity/eligibility gates live there unchanged. **Compression is
opportunistic — correctness never depends on coverage.**

## The three principles

**1. Compress at the store, not the extractor.** Emitters keep producing flat
`Edge` arms; the store's ingest paths detect qualifying runs (≥2 resolved
arms sharing (src, relation, dst_symbol, weight, provenance='ambiguous',
location, source, file, name_based)) and intern them. Every extractor,
resolver, and the whole Edge model stay untouched.

**2. Expand-affected before every mutating pass.** The store's resolve
pipeline (`_resolve_worklist`, `_rewiden_resolved`, `_invalidate_dangling`,
`_drop_redundant_holes`, `_propagate_overrides`, `_dedup_resolved_edges`) is
subtle, battle-tested SQL over flat rows. It stays byte-identical: before a
pass runs, any group whose rows the pass *could* touch is expanded back to
flat rows inside the same transaction; the pass runs unchanged; surviving
ambiguous runs are re-compressed at the end. The affected universe per
`replace_file` is small (the worklist principle the store already uses):
groups owned by the replaced file, groups whose `dst_symbol` matches a
node name added or removed, groups whose set contains a removed node id, and
groups colliding with a freshly-inserted override row. Django-scale numbers
say this is thousands of rows, not hundreds of thousands.

**3. Dedup is file-local, so the sink can compress at ingest.** `src` ids
embed the owning file, so `(src, relation, dst_id)` dedup groups never cross
files. The streaming sink already batches per file: run the existing
in-memory `_dedup_edges` on the file's batch, then intern the surviving
ambiguous runs — the 600K-row insert becomes a ~37K-row insert, which is the
actual index-time lever. Endgame `_propagate_overrides` probes existence
against `edges_all` (so it neither duplicates a group arm nor skips a needed
row) and collision cases fall back to expand-affected + re-dedup + re-compress.

## Read-path migration

From the consumer survey, the repoint set (SQL `FROM edges` → `FROM
edges_all`, or method body change) is:

- store: `callers_of`, `callees_of`, `resolved_edges`, `iter_resolved`,
  `iter_resolved_full` (holes/`unresolved_edges` stay on flat `edges`)
- operations: `confident_fan_in` fallback SQL, `impact_of` provenance stream,
  scan cycle SUM + god-object per-candidate probes, `find_coupling` `_linked`
  probes
- adjcache: `build_cache` scan + `apply_delta` per-node re-reads (the CSR
  still materialises the full expansion in v1 — sidecar size unchanged,
  build input 10× smaller)
- simcache sparse+dense builds (src, dst_symbol over CALLS)
- everything routed through `iter_resolved`/`resolved_edges`
  (reach reference paths, algebra COO, dataloop, similar, graphdiff,
  get_matrix) inherits the change for free.

SQLite flattens UNION-ALL views, pushing `src=?`/`dst_id=?` predicates into
both branches (idx_groups_src; idx_members_dst→idx_groups_set), so indexed
probes keep their plans. Row *order* out of the view differs from the flat
table (flat branch first, then groups); any consumer whose output order is
pinned by tests must stay deterministic — the differential campaign compares
against pre-compression output byte-for-byte and will surface every ordering
seam.

Sidecar delta capture: expand-affected does its work as flat-row
inserts/deletes inside the `replace_file` transaction, so the existing v3.40.0
TEMP-trigger capture sees affected srcs; re-compression (delete flat + insert
group) needs mirror triggers on `edge_groups` recording `src`, with member
dsts unioned in at capture-finish via the set join.

## Migration & compat

- Schema version bump; `cand_sets`/`cand_members`/`edge_groups`/`edges_all`
  created on open for old dbs (empty groups = today's behaviour exactly).
- No rewrite-on-open: an old db compresses progressively as files are
  replaced, or fully on the next `reindex`.
- `STITCHGRAPH_NO_EDGE_COMPRESSION=1` (or `[index] edge_compression = false`)
  keeps ingest flat — the differential campaign's control arm, and the escape
  hatch.

## Differential campaign (the release gate)

1. **Oracle**: index the same tree twice (compression on/off); assert
   byte-equality of every operation's rendered output (scan, orient,
   find_stale, find_holes, impact_of, trace_path, get_matrix, find_coupling,
   audit_graph, find_similar, subsystems, chokepoints) plus
   `edges_all`-vs-flat row-multiset equality.
2. **Incremental convergence**: the existing convergence oracles
   (delete/re-add, rename, homonym add/remove) run with compression on;
   `replace_file` end-state must equal a fresh full reindex byte-for-byte —
   this exercises expand-affected + re-compression round-trips.
3. **Sidecar**: adjcache built from a compressed db equals adjcache built
   from the flat twin (byte-equality of the mmap files), and the v3.40.0
   overlay tests pass with compression on.
4. **Scale**: Django before/after (index time, db size, scan/orient
   wall-clock); HA-class re-measure if disk allows.

## Risks

- **Write-pass drift**: any store pass touching a group without expansion
  first silently diverges. Mitigation: passes assert no `edge_groups` row
  matches their WHERE-universe before running (debug builds), plus oracle 2.
- **View plan regressions** on odd probes: EXPLAIN QUERY PLAN checks in tests
  for the four hot probes (src=?, dst_id=?, src+dst, dst_symbol=?).
- **Order-sensitive consumers**: caught by oracle 1's byte-equality; fix by
  deterministic ORDER BY at the consumer, never by relying on view order.
- **Trigger coverage**: overlay staleness is guarded by the existing
  stale-safe-or-not-at-all rules (chain gaps → full rebuild), so a missed
  capture degrades to a rebuild, never a wrong answer.
