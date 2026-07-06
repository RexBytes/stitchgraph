# v3.38.0 — incremental watch + persisted dense embeddings

*2026-07-05 · closes the roadmap's "true incremental reindex (wire
`replace_file` to `watch`)" and "persist dense-embedder vectors" items ·
details: `CHANGELOG.md`*

## What changed

`stitchgraph watch` no longer rebuilds the whole index on every edit. On a
change it now:

1. **Extracts the whole project in memory** — deliberately NOT single-file
   extraction: resolution (imports, homonym widening, dispatch, dunder seeding)
   needs the full symbol table, and reusing the exact full-reindex extraction
   means every convergence oracle keeps holding *by construction* rather than by
   re-derivation.
2. **Writes only what changed** — the mtime-added/modified files, plus every
   pseudo owner (`db`/`event` aggregates whose members derive from many source
   files), each through `Store.replace_file`. That store-side machinery
   (worklist re-resolve, name-based re-widening, override propagation, dangling
   invalidation) was finished releases ago and pinned to converge with a full
   reindex; it just had no caller in the shipped surfaces until now.

The win: the full-table rewrite (delete + N-million-row insert + dedup endgame)
dominates reindex wall time — the edit loop now pays extraction only.

## Automatic fallbacks (correctness first)

- **A deleted or renamed file → full reindex.** `replace_file`'s deletion path
  has two documented non-cardinal residuals (phantom `fan_in` re-bind,
  `find_holes` count drift — see LIMITATIONS); falling back keeps them
  library-only, exactly as documented.
- **AUTO-streaming-sized trees → full (streaming) reindex.** The incremental
  path's in-memory whole-project extract is precisely what the streaming
  indexer exists to avoid; at that scale the constant-memory path wins.
- **`watch --full`** forces the pre-v3.38 behaviour.

## Convergence is pinned, not assumed

A differential suite compares the incremental store byte-for-byte (sorted
node/edge tuples incl. provenance and `name_based`) against a fresh full
reindex of the same mutated tree, across the drift-prone cases: a modified
file, an **added homonym** (must re-widen an existing caller's name-based edge
to AMBIGUOUS arms), an **emptied file** (stale rows cleared), a **pseudo-owner
change** (a new `event::` node from an edited emitter — mtime can never vouch
for those, so pseudo owners refresh unconditionally), and a **re-export surface
change** (`__all__` edit in one file flips the `exported` role on another
file's symbols — the panel-R37A contract).

## Scale envelope

Personal-to-mid-size repos are the target: below the AUTO-streaming threshold
(~2k source files) the differential apply turns a full-rebuild edit loop into
extraction-only latency. Above it, watch full-reindexes via the streaming path,
unchanged. True single-file extraction against a persistent symbol table —
which would extend incrementality to streaming-scale trees — remains future
work and is tracked in `docs/STATUS.md`.

## Dense embeddings persist in the similarity sidecar

The token similarity path has been sidecar-served since v3.36.0; a registered
dense embedder still re-embedded EVERY code node on EVERY query. Now
`set_embedder(fn, cache_key="model@rev")` — where `cache_key` names the vector
space (the model2vec auto-wiring keys on the configured model name) — persists
node embeddings once in `<db>.simcache-dense/` as L2-normalised float32 rows.
A query then embeds only the snippet and scores in a single matrix·vector
product; the per-query CALLS-edge materialisation is skipped entirely.

Safety rails: the manifest pins the model key, so switching models rebuilds
rather than mixing vector spaces; a keyless embedder (an ad-hoc lambda has no
stable identity) keeps the recompute-per-query reference path; and the sidecar
inherits every token-side gate (generation staleness, `[index]
similarity_cache = false`, pure mode, numpy absence). A counting-embedder test
pins the contract: after the one-time build, exactly one embed call per query.
