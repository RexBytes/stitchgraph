# stitchgraph v2.1.0 — constant-memory queries (scalable find_stale)

Found by **dogfooding v2.0.1 across a multi-repo Python hunt** — Django, Salt, Ansible, the
CPython stdlib, and **Home Assistant**. v2.0.0 made *indexing* memory-bounded; this release
closes the matching gap on the *query* side so the reachability sweeps scale to the graphs the
indexer can now build.

## The headline: `find_stale` scales to a 16M-edge graph

Home Assistant indexed beautifully — **6,728 files, ~16 million edges, ~4 GB peak**. But
`find_stale` then ran out of memory. Every reachability/centrality sweep funnelled through
`Store.resolved_edges()`, which does `SELECT * FROM edges … .fetchall()` and materialises **all
16M `Edge` objects at once** (a double spike: 16M SQLite rows → 16M dataclass instances).

v2.1.0 adds a lean `Store.iter_resolved()` that **streams** `(src, relation, dst_id, weight)`
tuples cursor-by-cursor. The GraphBLAS adjacency (`algebra._Adjacency`) and the pure-Python
sweeps (`reachable_from`, `reverse_reachable_from`, `fan_in`/`fan_out`, `best_path`, SCC) build
their adjacency directly from it — never the full `Edge` list.

| graph | `find_stale` peak before | after |
|---|---|---|
| 6M-edge synthetic | multi-GB | **~840 MB** |
| Home Assistant (~16M edges) | **OOM** | ~2 GB (now completes) |

**Byte-identical results** — the GraphBLAS==pure-Python and incremental/streaming differential
oracles stay green; this is a pure memory-shape change.

## Also fixed: the SQL resolver stops treating prose as SQL

The hunt turned up a noisy precision bug: `_sql_literals` flagged any string whose *first word*
was a SQL verb, so ordinary docstrings — *"Create a list…"*, *"Update the…"*, *"Delete a…"*,
*"With this…"* — were handed to sqlglot, producing a **flood of parse warnings** (hundreds per
file on Django and Salt) and the occasional phantom table. It now requires real statement
structure (`SELECT … FROM`, `INSERT INTO`, `UPDATE … SET`, `DELETE FROM`, `CREATE <table|index|
view|…>`, `WITH … AS … SELECT`). Genuine queries still resolve; prose is ignored. Pinned by a
regression test.

## Also documented: plugin-loader dynamic dispatch

Salt invokes execution-module functions purely by **string name through its loader**, which a
static call graph can't follow — so `find_stale` flagged **3,907** live-but-loader-dispatched
functions. This (and pluggy / entry-point registries generally) is now documented in
`LIMITATIONS.md` as a dynamic-dispatch blind spot, with an escape hatch: pin the public surface
via `stitchgraph.toml [entry_points]` globs or feed a runtime `ingest_trace`.

## Multi-repo hunt scorecard (what passed)

| Project | files | edges | reindex peak | cardinal false-positives |
|---|---|---|---|---|
| Django 5.2 | 913 | 469k | 164 MB | 0 |
| Salt 3008 | 987 | 518k | 238 MB | 0 (3,907 = loader, documented) |
| Ansible-core 2.19 | 583 | 107k | 80 MB | 0 |
| CPython 3.11 stdlib | 674 | 973k | 314 MB | 0 |
| Home Assistant 2024.3 | 6,728 | ~16M | ~4 GB | 0 |

Zero cardinal (live-flagged-dead) false-positives across five large, idiomatically-diverse
Python codebases — the dunder/callback/exported rooting holds. The findings were all
scalability/precision/noise, now fixed.

## Compatibility

- No API or schema change; indexes rebuild cleanly. `resolved_edges()` is unchanged (kept for
  callers that need full `Edge` objects); the sweeps now prefer the streaming accessor.
- Same constant-memory streaming indexer as v2.0.x — this extends the property to queries.

## Quality gate

Full test suite (393, incl. new SQL-prose + streaming-reachability tests) + ruff + mypy clean;
all differential oracles (GraphBLAS==pure-Python, incremental==full, streaming==full) green;
mutation meta-oracle over the changed code; multi-model adversarial review.
