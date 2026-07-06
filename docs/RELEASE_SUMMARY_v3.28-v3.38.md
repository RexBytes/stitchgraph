# v3.28 → v3.38 — the speed-then-honesty campaign

*2026-07-03 → 2026-07-05 · eleven releases on one branch · per-release detail in
`CHANGELOG.md` and `docs/RELEASE_NOTES_v3.3*.md` · field evidence in
`research/16` (HA static analysis), `research/17` (100k-node scale validation),
`research/18` (HA POD field validation)*

One sentence: **make every query fast, then prove the answers honest, at
real-codebase scale** — finishing with the call graph field-measured at
**99.1% recall** against runtime ground truth on Home Assistant.

## 1. Fast: the sidecar architecture (v3.30–v3.36)

SQLite stays the single source of truth; each sidecar is a disposable,
generation-gated, mmapped derivation that any reindex invalidates and the next
query lazily rebuilds. Every accelerated path is pinned byte-identical to its
pure-Python reference, so defaulting to fast costs nothing in trust.

| what | before → after (measured) |
|---|---|
| `find_stale` @ 16M edges (adjacency sidecar, v3.30) | 119 s → **2.1 s** |
| `find_chokepoints` (SCC/articulation on CSR, v3.31 + v3.36.1) | 216 s → **58.5 s**, 4.1 → 3.0 GB |
| `find_similar` / `find_component` @ 106k nodes (similarity sidecar, v3.36) | ~3 min → **<0.1 s** per query |
| dense-embedder queries (persisted vectors, v3.38) | one embed call **per node** → one **per query** |

Supporting decisions: full-power default install with guarded imports
(`--pure` / `STITCHGRAPH_PURE=1` for the stdlib-only reference paths, a
core-only CI job pinning the degradation), ANALYZE planner stats + pinned
query shapes, and the scan/orient hot paths moved to per-candidate SQL and
confident-only sidecar bitcounts.

## 2. Scale: constant memory, field-tested estimator (v3.29, research/17)

- Streaming reindex holds **flat, symbol-scale RSS** regardless of repo size:
  228 MB at 26.8M edges / 106k nodes; 375 MB on the 20.9 GB HA repo-root index.
- `docs/PERFORMANCE.md`: estimate minutes ≈ edges/500k, size ≈ 0.65 KB/edge —
  tested −12% on its first blind run — plus per-op anchors and the hazard list
  (lead hazard: never pad a corpus with near-duplicate trees).

## 3. Honest: the POD field validation (v3.37, research/18)

The first run of the full coverage-audit suite against REAL captured coverage
(Home Assistant helpers suite: 2,056 tests × 3,274 executed functions) turned
`audit_graph` on the indexer itself and caught **four bugs** — silent
newer-syntax file drops (10% of HA invisible), ignore-glob semantics wrong in
both directions, a disk-full endgame rollback, and rescued files missing from
cross-file resolution. Three rounds bracket the story: recall 0.975 on a
half-blind graph → 0.299 on a complete-but-severed one → **0.991 on the
complete, connected one**. The residual 0.9% is an enumerable resolver roadmap
(`with`-protocol dunders, jinja sandbox hooks, getattr dispatch, fixture
indirection), not noise.

New guarantees that came out of it: **no file is ever skipped silently**
(counted + named on the reindex Result), and stitchgraph can index **code
newer than its own interpreter** (tree-sitter Python fallback, stitched into
resolution both directions).

## 4. Breadth and finish (v3.32–v3.35, v3.38)

- New ops: `find_component` (purpose-aware locator), `audit_graph`
  (recall/over-approximation vs runtime truth), behavioural `find_similar`,
  test-anchored `co_change`, `find_coupling` common-callers + scope.
- Contract resolvers: OpenAPI/Swagger, gRPC proto, Prisma, TypeORM — spec
  files become routing tables, spec-wired handlers stop reading as dead.
- Language gaps: C/C++ `#include`, Ruby `require`, Bash `source` imports.
- v3.38: incremental `watch` (differential apply through `replace_file`,
  pinned byte-equal to full reindexes) + persisted dense embeddings.

## What Home Assistant analysis found (the dogfood dividend)

Beyond validating the tool: HA's helper suite collapses to **45 behavioural
modes** (464 of 2,056 tests preserve the full structure), 363 tests are
coverage-identical to a sibling, 20 fixture-bedrock functions run in 100% of
tests, and 1,063 functions are corroborated dead (statically unreachable AND
never executed). Details + per-op timings: `research/18`.
