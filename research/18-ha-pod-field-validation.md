# 18 — HA POD field validation (real coverage, real graph, three rounds)

**Date:** 2026-07-05 · **Tool:** v3.36.2 → v3.37.1 (branch `claude/adjacency-sidecar`) ·
**Goal:** the research/16 future-work item — run the *entire* POD (Proof-Of-Dynamics)
operation suite against a **real captured coverage artifact** from a real test run of
a real codebase. Until now every POD number came from constructed matrices; this is
the first time runtime ground truth met the static graph in the field.

**Headline: `audit_graph` recall 0.991** — on a complete, connected index, static
reachability finds 99.1% of every function Home Assistant's helper test suite
actually executed (2,056 base tests, 3,274 executed functions, 0 unmatched ids).
Getting there took three rounds, because the validation kept doing its job:
**it found four indexer bugs before it said anything about Home Assistant.**
All four shipped fixed as v3.37.0 + v3.37.1 (`docs/RELEASE_NOTES_v3.37.0.md`).

## The setup

- **Codebase:** Home Assistant core at `7368b9ca1d` (2026-01-28, the last commit
  before `requires-python >= 3.14`) — 9,000 indexed files.
- **Capture:** `pytest tests/helpers/` under `coverage.py` with per-test contexts
  (2 modules excluded for a missing C library (turbojpeg); 31 env-shape failures;
  5,469 tests passed). Converted to the canonical artifact: 13,290 rows →
  **2,056 base tests × 3,274 executed functions**.
- **Index:** repo root (test nodes must exist for audit_graph), indexed under
  Python 3.11 against a codebase that uses 3.12+ syntax — which is exactly what
  made this run a bug-finder.
- **Battery:** nine coverage-consuming ops, one subprocess each, per round.

## Three rounds, one lesson each

| round | tool | graph state | recall | what the number meant |
|---|---|---|---|---|
| 1 | v3.36.2 | **half-blind**: 880 files silently missing | 0.975 | recall over only the 50% of executed functions the graph knew — looked great, hid the hole |
| 2 | v3.37.0 | **complete but severed**: files rescued, edges not stitched | 0.299 | honest denominator exposed that cross-parser calls were dropped |
| 3 | v3.37.1 | **complete and connected** | **0.991** | the real measurement |

The pattern worth internalizing: rounds 1 and 2 were both "the tool working" —
each round's anomaly named its bug precisely. A validation harness that can only
confirm success is not a validation harness.

## The four bugs the validation caught (all fixed)

1. **Silent syntax skips** — 880 files (10% of HA, *half its executed functions*,
   including `core.py`) use PEP 695 syntax; under Python 3.11 `ast.parse` rejects
   them and the extractor dropped each with no count, warning, or meta. Fix:
   skips are counted and named on the reindex Result; a tree-sitter Python
   fallback (one grammar, tracks current syntax independent of the interpreter)
   extracts them structurally. Round 3: `python_fallback_files: 883`, zero missing.
2. **Ignore-glob semantics wrong in both directions** — `PurePath.match` is
   right-anchored and pre-3.13 `**` is single-segment: `tests/components/**`
   ignored nothing below one level (6,627 files wrongly indexed → 23 GB index),
   `script/**` swallowed `homeassistant/components/script/` (6 files wrongly
   dropped). Fix: root-anchored gitignore-style matcher (`core/globs.py`).
3. **Endgame rollback on disk-full** — the glob-bloated index filled the disk at
   the final `DROP INDEX`; the enclosing transaction rolled back edge-dedup and
   skipped ANALYZE/generation/root-meta. Fix: dedup commits separately; a failed
   temp-index drop is a warning, not a rollback.
4. **Rescued files weren't stitched into resolution** (the round-2 collapse) —
   fallback nodes joined the graph after reference resolution, so every
   cross-boundary call was dropped (unknown call names are dropped by design).
   With `core.py` — the hub — on the far side of the boundary, reachability
   severed at the first hop. Fix: rescued symbols join the Python extractor's
   symbol table *before* its reference pass; their own unresolved references
   re-resolve against the full table via the standard name-based rules.

## Round 3 — the real numbers

Index: 9,000 files / 77,553 nodes / 20.9 GB / 46 min / **375 MB peak, flat**
(the +5 GB over round 2 is the stitched cross-boundary resolution, i.e. the
edges whose absence was bug 4).

| op | time | peak RSS | headline |
|---|---|---|---|
| find_modes | ~7 s | 513 MB | intrinsic dimensionality **45**; minimal test set **464 / 2,056** (23%) |
| find_gaps | ~84 s | 821 MB | 1,063 untested+unreachable (corroborated-dead candidates) |
| feature_map | 7 s | 528 MB | 16 modes labelled; top: restore-state/registry setup |
| redundant_tests | 0.8 s | 167 MB | 208 identical-profile groups / 363 subsumed tests |
| test_order | 10 s | 167 MB | greedy order: first test alone covers 764 functions |
| find_core | 0.8 s | 167 MB | 20 functions in 100% of tests (fixture bedrock) |
| find_coupling | 251 s | **10.1 GB** | 40 co-activated pairs, explained by `common_callers`; the known-cost op (`docs/PERFORMANCE.md`) |
| find_outlier_tests | 6.9 s | 527 MB | top outlier: webhook config-flow test (unique) |
| audit_graph | 31.6 min | 994 MB | **recall 0.991**, over-approximation 329× |

### The 0.9% tail is the resolver roadmap

`missed_functions` now contains no artifacts — every entry is a genuine
dynamic-dispatch pattern the static graph cannot see:

- **Operator/protocol dispatch:** `TemplateContextManager.__exit__` (`with`
  blocks; missed by 389 tests) — a protocol-method resolver (`with X:` →
  `X.__enter__/__exit__`) remains the single highest-yield improvement.
- **Framework-internal hooks:** `TemplateEnvironment.is_safe_callable` /
  `is_safe_attribute` (called by jinja2's sandbox internals, not by HA code),
  `LoggingUndefined._fail_with_undefined_error` (jinja undefined-handling).
- **Dynamic attribute dispatch:** `_ScriptRun._async_step_event`-style handlers
  invoked via `getattr(self, f"_async_step_{action}")`, `_domain_default`
  (registry field default factories), `dir_with_deprecated_constants`
  (module `__dir__` hook).
- **Fixture blindness** (the 7 remaining zero-recall tests): tests whose only
  static edges point at their own nested helpers, all real work flowing through
  pytest fixtures and dynamic registration.

Over-approximation is 329×: a single test's static reach floods the shared
helper core. Expected for hub-dense BFS; reported as a ratio, never a defect
list.

## What POD says about Home Assistant itself

- **45 behavioural modes** span the 2,056-test helper suite; **464 tests (23%)
  preserve the full mode structure** — `test_order` gives the greedy schedule.
- **208 groups of coverage-identical tests** (363 subsumed) — parametrize
  families sharing one profile; a consolidation review aid, not a delete list.
- **The fixture bedrock is total:** 20 functions execute in 100.0% of tests
  (`frame.async_setup`, timer-handle checks, executor teardown). A regression
  there invalidates the whole suite at once; `find_core` names them precisely.
- **No hidden coupling in the helper layer:** every top co-activation pair is
  explained by a shared file or a `common_caller`.
- **1,063 functions are both unreachable and never executed** — corroborated
  dead-code candidates where static and runtime evidence agree.

## Verdict

- The call graph is **validated**: 99.1% of real executed behaviour is
  statically reachable, and the misses are enumerable, explainable dynamic
  patterns — resolver roadmap items, not model failures.
- The pipeline is **field-hardened**: four bugs found by pointing the audit at
  ourselves, each fix pinned by tests and re-validated end-to-end at 20 GB scale
  on constant memory.
- The estimator holds: `docs/PERFORMANCE.md` anchors updated with all three
  rounds' costs; `find_coupling` documented as the one budget-separately op;
  `audit_graph` ≈ 0.9 s/test at 30M+ edges.
