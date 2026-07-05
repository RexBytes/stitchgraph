# 18 — HA POD field validation (real coverage, real graph, round 1)

**Date:** 2026-07-05 · **Tool:** v3.36.2 (branch `claude/adjacency-sidecar`) ·
**Goal:** the research/16 future-work item — run the *entire* POD (Proof-Of-Dynamics)
operation suite against a **real captured coverage artifact** from a real test run of
a real codebase, not a synthetic fixture. Until now every POD number came from
constructed matrices; this is the first time runtime ground truth meets the static
graph in the field.

## The setup

- **Codebase:** Home Assistant core at `7368b9ca1d` (2026-01-28, the last commit
  before the `requires-python >= 3.14` bump — the newest tree our Python 3.13 venv
  can run).
- **Capture:** `pytest tests/helpers/` under `coverage.py` with per-test contexts
  (2 modules excluded for a missing system C library (turbojpeg), 31 failures from
  env shape — 5,469 tests passed). Converted with the dogfood kit's `to_canonical.py`:
  **13,290 coverage rows → 2,056 base tests × 3,274 executed functions**.
- **Index:** repo root (so test nodes exist for audit_graph), `stitchgraph.toml`
  ignore-globs for `tests/components/**` etc. Result: 115,557 nodes / 30.0M resolved
  edges / 23 GB, ~40 min, flat RSS.
- **Battery:** nine coverage-consuming ops, one subprocess each, same db + artifact.

## The battery (all nine ran, all `ok=True`)

| op | time | peak RSS | headline |
|---|---|---|---|
| find_modes | 10.1 s | 513 MB | intrinsic dimensionality **45**; minimal test set **464 / 2,056** (23%) |
| find_gaps | 168 s | 1.1 GB | 853 untested+unreachable (corroborated-dead candidates) |
| feature_map | 7.1 s | 528 MB | 16 modes labelled; top: restore-state/registry setup (energy 0.45) |
| redundant_tests | 0.8 s | 167 MB | 208 identical-profile groups / 363 subsumed tests |
| test_order | 10.0 s | 167 MB | greedy order: first test alone covers 764 functions |
| find_core | 0.9 s | 167 MB | 20 functions in **100%** of tests (fixture bedrock: `frame.async_setup`, executor teardown) |
| find_coupling | **979 s** | **12.8 GB** | 40 co-activated pairs; top pairs explained by `common_callers` |
| find_outlier_tests | 6.7 s | 528 MB | top outlier: webhook config-flow test (residual 0.88, unique) |
| audit_graph | 1374 s | 879 MB | graph-conditional recall **0.975** over 2,056 tests, 0 unmatched |

First op pays the one-time lazy sidecar build on the fresh 30M-edge index
(`.adjcache/` = 310 MB); after that the small ops are interactive.
`find_coupling` is the one sore thumb — usable but expensive at this scale
(entered in `docs/PERFORMANCE.md` as a known-cost op).

## The recall number — stated honestly

`audit_graph` compares, per test, the functions the test **executed** (coverage)
with the functions it **statically reaches** (graph BFS). Two numbers matter:

1. **Graph-conditional recall: 0.975.** Of executed functions *that exist as graph
   nodes*, static reach from the test node finds 97.5%. The 2.5% tail is real and
   diagnosable — `missed_functions` is dominated by **operator-protocol dispatch**
   the resolvers don't model: `TemplateContextManager.__exit__` (`with` blocks,
   missed by 389 tests), `ScriptRunVariables.__setitem__` (`x[k] = v`),
   `RenderInfo._freeze` (called via callable indirection). A protocol-method
   resolver (`with X:` → `X.__enter__/__exit__`, subscript-assign → `__setitem__`)
   is the single highest-yield resolver improvement this measurement exposes.
   The zero-recall tests are **pytest-fixture blindness**: their only static edges
   point at their own nested helpers; all real work flows through fixtures and
   dynamic registration (template extensions, voluptuous schema `__call__`).
2. **Graph coverage of executed behaviour: 50.0%** — only 1,637 of 3,274 executed
   functions existed in the graph at all. That is NOT a resolver gap; it is how
   the validation caught three genuine product bugs (below). audit_graph excludes
   graph-absent ids from the denominator *by design* (id drift must not read as
   fake misses), which is why both numbers must be reported together.

Over-approximation ratio: **912×** — a single test's static reach floods the shared
helper core. Expected for BFS-from-one-test on a hub-dense graph; it is reported as
a ratio, never a defect list.

## The three product bugs the validation caught

This is the actual value of field validation: the battery's *inputs* indicted the
indexer before its outputs said anything about HA.

**Bug 1 — files with newer Python syntax are dropped silently.** 880 of 8,803
`homeassistant/**.py` files (10%) use PEP 695 syntax (`type X = ...`, 3.12+).
Indexing with a 3.11 interpreter, `ast.parse` raises SyntaxError and the extractor
skips the file with **no count, no warning, no meta**. 1,614 of the 3,274 executed
functions (49%) were invisible for this reason alone — including all of
`homeassistant/auth/__init__.py` (`AuthManager` — not a fringe module). The tell
was audit_graph's `missed_functions` + zero-recall tests pointing at modules that
simply weren't there. *Fix (v3.37.0): count and surface skipped files in the
reindex result; fall back to the tree-sitter Python grammar for files stdlib `ast`
cannot parse.*

**Bug 2 — ignore-glob semantics were broken in both directions.** `_ignored` used
`PurePath.match`, which is **right-anchored** and (before 3.13) treats `**` as a
single segment. Consequences on this run: `tests/components/**` failed to ignore
anything below one directory deep — **6,627 test files were indexed against the
config's intent** (a large share of the 23 GB / 30M edges); meanwhile `script/**`
matched *any* path whose trailing segments are `script/<file>`, wrongly swallowing
`homeassistant/components/script/` (6 files, 23 executed functions). *Fix
(v3.37.0): root-anchored, gitignore-style recursive matching.*

**Bug 3 — disk-full during the final `DROP INDEX` rolled back the endgame.** The
oversized index (bug 2) pushed the disk to full during reindex's last cleanup step;
the enclosing transaction rolled back edge-dedup, and ANALYZE / generation-bump /
root-meta never ran. The battery was unaffected (every op here is boolean-
reachability or coverage-matrix based, both invariant to duplicate parallel edges)
— but degree-based ops (fan-in hubs, pagerank) would have seen inflated counts with
no signal anything was wrong. The error surfaced as a raw traceback rather than a
Result.

## What POD said about Home Assistant itself

With the denominator caveat (helpers-slice coverage, half the executed functions
graph-visible), the structural findings stand on the coverage matrix alone:

- **45 behavioural modes** span 2,056 helper-suite tests — the helper layer's
  runtime behaviour is dramatically lower-dimensional than its test count, and
  **464 tests (23%) preserve the full mode structure** (`test_order` gives the
  greedy schedule).
- **208 groups of coverage-identical tests** (363 subsumed) — mostly parametrize
  families (`test_country` / `test_currency` / `test_language`… share one profile),
  a consolidation review aid, not a delete list.
- **The fixture bedrock is total:** 20 functions execute in 100.0% of tests
  (`frame.async_setup`, timer-handle checks, executor teardown) — any regression
  there invalidates the whole suite at once; `find_core` names them precisely.
- **Implicit coupling is explainable:** every top co-activation pair either shares
  a file or a `common_caller` (e.g. `Store._async_load` / `_async_load_data`);
  no smoking-gun hidden coupling in the helper layer.

## Round 2

Round 1's numbers are lower bounds on graph quality: the graph was missing half the
executed functions for reasons that are *fixed bugs*, not modelling limits. Round 2
(after v3.37.0: correct globs, PEP 695 fallback, surfaced skips) re-indexes and
re-runs the battery; the number to watch is graph-conditional recall holding ≥0.97
while graph coverage of executed behaviour jumps from 50% toward ~100%.
