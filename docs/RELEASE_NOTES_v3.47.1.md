# v3.47.1 — green everywhere the tests actually run

*2026-07-07 · patch release: the CI environments caught what the local gate
could not · details: `CHANGELOG.md`*

## Why a patch

v3.47.0's tag CI ran red on two environment-specific failures (the code on
PyPI works — the failures were in test gating and one degraded-install
path). This patch makes every CI job green end to end and is otherwise
identical in behaviour on default installs.

## Fixed

1. **LSP integration tests now verify the server binary RUNS**, not merely
   that it is on PATH. GitHub's runners ship rustup's `rust-analyzer`
   proxy shim *without the component installed* — on PATH, dies on first
   use. That is precisely the dead-shim failure mode research/24 documents
   and the LSP client itself already tolerates; only the test gate was
   naive. It now probes `--version` and skips honestly.
2. **Orient's test-mass exclusion degrades with the metric.** On installs
   without numpy/GraphBLAS (core-only, `--pure`), the default hub metric
   falls back to direct confident fan-in — and that fallback now excludes
   test-owned dependers exactly like the transitive metrics do, instead of
   letting 25 test callers out-rank 3 src dependers (the core-only CI job
   caught the gap). Explicitly-chosen `fan_in`/`pagerank` metrics keep raw
   degree semantics, as documented in v3.47.0.
3. **`god_object` skips test-owned entities.** The v3.47.0 generalization
   run on hono surfaced a test file's router mock among the three
   survivors; suite plumbing is not design feedback — the same principle
   as the orient hub-list exclusion, now pinned in both directions by a
   test.

## Compatibility

No schema change, no API change. Default (full) installs behave
identically to v3.47.0 except that test-owned god objects no longer
appear; core-only/pure installs additionally get the corrected fallback
hub ranking.
