# stitchgraph v3.29.0 — release notes

**The field-analysis release.** The first thing we did with v3.28.0's ability to index a
16M-edge graph in 158 MB was *analyse* one (`research/16-ha-field-analysis.md`). The
analysis found real dead code in the target — and, more usefully for this repo, three
places where the **query layer** didn't hold up at the scale the indexer now reaches.
All three fixes are general: they change how every run behaves on every codebase; the
16M-edge graph was just the corpus big enough to expose them.

## Fixed

- **`scan` was Edge-object scale, twice.** Its provenance-share step (issue #11's
  cycle/god-object demotion) fetched every resolved edge into Python and indexed them
  into two dicts; its EXTRACTED-only liveness sweep materialised the same full list a
  second time inside `_adjacency(edge_filter=...)`. On a 16M-edge graph that is a
  multi-GB peak — MemoryError under a 6 GB cap — while every sweep around it ran at
  compact-adjacency scale. Now: the shares are per-component / per-candidate `COUNT`
  queries inside SQLite (temp-table join for cycle members, indexed probes for
  god-object candidates), and the filtered sweep streams edges one at a time through the
  new `Store.iter_resolved_full()`. **Byte-identical output** (162-issue differential on
  stitchgraph's own graph, old vs new); **1,486 MB → 185 MB and ~3× faster** on the
  ~1.2M-edge gate corpus.
- **Hub ranking (`orient`, and `risk`'s centrality) counted every AMBIGUOUS widening arm
  at full weight**, so on any big codebase with common names the "read these first" list
  degenerates into homonym artifacts (the field graph's top hubs were attribute nodes
  with fan-in ~12,000 — none of them architecture). The fan-in fallback now ranks by
  **confident fan-in** (EXTRACTED-provenance edges only), computed by a single SQL
  `GROUP BY` — O(nodes) output, no Python edge sweep — and reported as
  `confident_fan_in`. The GraphBLAS metrics (`transitive_fan_in`, `pagerank`) still rank
  over raw matrices; a provenance-filtered matrix variant is recorded follow-up work.

## Added

- **`[entry_points] root_modules`** — glob patterns over module file paths for
  framework-loaded plugin/integration trees (Django apps, pytest/Sphinx plugins, Odoo
  addons, Home Assistant integrations…). Modules a loader imports dynamically by name
  have no static importer, so their module-level wiring — schema validators, registered
  hooks, dispatch tables — used to surface as dead-code candidates. A matching glob
  roots the MODULE node, which is the right granularity: what the module body references
  becomes live, while an unreferenced function in the same file remains a candidate
  (pinned by test). On the field corpus this rescued exactly the 33
  module-level-rooted false candidates.
- `Store.iter_resolved_full()` — cursor-streamed full `Edge` objects for consumers that
  need provenance/weight but must never hold the O(edges) list.
- The CI memory gate now also runs **`scan`** against the index it just built, under a
  400 MB `RLIMIT_AS` cap calibrated to kill the pre-fix code (1,486 MB) with the same
  margin it grants the fix (185 MB).
- `research/16-ha-field-analysis.md` — the full field-analysis record: the verified
  dead-code findings in the target, the POD feasibility verdict, the detector
  experiment that became `root_modules`, and the query-layer scale profile.

## Validation

- `scan` differential (old vs new) on stitchgraph's own index: 162 issues, identical.
- New tests falsified against the pre-fix code: the hub-ranking test's homonym strictly
  dominates raw fan-in (old code ranks it first, new code inverts); the `root_modules`
  test fails on old code (key unknown).
- 16M-edge field graph under a 6 GB cap: `scan` completes in **625 s at 2.1 GB peak**
  (compact-adjacency scale, same league as the other sweeps) where the pre-fix code
  MemoryError'd; `orient` drops from 106 s to **4 s** and its top hubs become
  architecture (`HomeAssistant`, `ConfigEntry`, `AddEntitiesCallback`, `FlowResult`,
  `Platform`) instead of homonym attribute noise.
- Full suite green.

## Also fixed en route: a query-planner trap this same graph exposed

The first cut of the SQL shares put `relation = ?` in the WHERE clause next to the
selective key. On a database without ANALYZE stats, SQLite's planner may choose
`idx_edges_rel` — a 12.9M-entry index walk **per god-object candidate** (~2 s each,
hours in total), caught live with py-spy. The shipped queries keep only the selective
equality (`src` / `dst_id`) in the WHERE and move relation/provenance into `SUM`
aggregates, with `CROSS JOIN` pinning the cycle query's join order. Verified with
`EXPLAIN QUERY PLAN` on the field graph: `idx_edges_src`/`idx_edges_dst` probes at
0.3–25 ms. This hardening applies to any store queried without stats — i.e. every
stitchgraph index built before this release.

Belt and braces: `reindex` now also finishes with an approximate `ANALYZE`
(`analysis_limit=1000` — measured 0.03 s on the 16M-edge graph, vs 13.8 s for a full
scan), so freshly built indexes carry `sqlite_stat1` planner statistics. Measured on
the field graph, those stats alone steer the planner off the trap. The pinned query
shapes stay the primary defense — indexes already in the field remain stat-less until
their next reindex, the sampled stats are imprecise (they rank index selectivity
correctly but misestimate absolute counts by orders of magnitude), and a deterministic
shape beats a data-dependent plan — the stats exist to protect every *other* query
(ad-hoc, future, user-issued) by default.
