# POD / co-activation roadmap — using runtime behavioural data to give stitchgraph users better analysis and tools

**What we have.** stitchgraph v3.21.0–v3.22.0 turned per-test coverage into a first-class data source:
the **co-activation matrix** `M[test, function]` (1 if a test executed a function), its **POD** (mean-centred
SVD → behavioural modes = eigenvectors of the function–function covariance, ranked by eigenvalue), and
pure-set-math queries over `M`. All read-only, advisory, cardinal-safe (stitchgraph never runs your code;
it reads the inert `stitchgraph-coverage-v1` matrix you capture in your own sandbox via `scaffold_coverage`).

Already shipped:
- `find_modes` — behavioural modes, intrinsic dimensionality, minimal covering test set, redundant pairs (SVD).
- `select_tests` — which tests to run for a change (runtime ∩/∪ static blast radius).
- `co_change` — what code moves together / implements an outcome (co-activation neighbourhood).
- `find_coupling` — implicit coupling: co-run but no static edge (runtime ∖ structure).

This doc is the menu of everything *else* the same data unlocks, ranked by value × cost, each with the
math, a concrete op sketch, and an honest caveat. Grouped by what extra input each needs.

---

## Tier A — pure set-math over M (cheap; no numpy; same shape as the v3.22.0 ops)

### A1. `untested_functions` — coverage gaps, split dead vs live-but-untested  ★ highest value
Functions in the graph that appear in **zero** test rows. Fuse with `find_stale`:
- reachable (live) **and** untested → a genuine, actionable **coverage gap** (write a test).
- unreachable (dead) **and** untested → confirms dead code (delete).
This is the single most useful fusion: `find_stale` says "no one *can* reach it"; coverage says "no test
*did* reach it" — together they classify every function into {tested-live, untested-live, dead}.
- **Op sketch:** `find_gaps(store, coverage) -> {untested_live: [...], untested_dead: [...], tested: N}`.
- **Caveat:** only sees code the suite exercised; a function untested here may be exercised in prod. State it.

### A2. `select_tests` for a **changeset** (diff → CI subset)  ★ most immediately practical
Generalise `select_tests` from one symbol to many: feed a git diff's changed functions, union their
runtime tests → the exact minimal set of tests to run for that PR. The daily-driver CI win.
- **Op sketch:** `select_tests(names=[...], coverage)` or a thin `tests_for_diff(store, coverage, diff)`.
- **Caveat:** a brand-new function has no coverage history → fall back to its static blast radius (already
  how `select_tests` degrades). Renamed functions need id remapping.

### A3. `test_order` — fail-fast / coverage-greedy ordering
Order the suite so each next test maximises *new* function coverage (greedy over M). CI surfaces a
regression in the first few tests instead of the last. The prefix is exactly the `minimal_test_set`.
- **Op sketch:** `test_order(coverage) -> [test_id, ...]` (full ordering, coverage-accrual annotated).
- **Caveat:** coverage-greedy ≠ failure-likelihood-greedy; it front-loads *breadth*, not *risk* (combine
  with A? or churn for the latter).

### A4. `redundant_tests` — identical/near-identical profile clusters
Groups of tests with the same (or ≥threshold cosine) function set. `find_modes` already counts the pairs;
this lists the **groups** for triage.
- **Op sketch:** `redundant_tests(coverage, min_similarity=1.0) -> [[test_id,...], ...]`.
- **Caveat (important):** coverage-identical ≠ behaviourally redundant. On stitchgraph the biggest group
  was 135 *parametrized* cases (same path, different data) — valuable, not deletable. Must warn loudly;
  this is a *review aid*, never an auto-delete.

### A5. Extend `co_change` / `find_coupling` polish
- `co_change` anchored on a **test** (not a function) → the functions that test exercises = "what this
  test really covers" (test-intent audit).
- `find_coupling` with a `--same-file/--cross-file` filter and a "common-caller" annotation (does a
  shared static ancestor explain the co-activation?) to rank truly-hidden coupling above sibling noise.

---

## Tier B — SVD-native (needs numpy; extends `find_modes`)

### B1. `feature_map` — mode ↔ code ↔ tests, as a first-class artifact
Each mode is a feature axis. Emit, per mode: top-loading functions (the feature's implementation) ×
top-expressing tests (the feature's tests) × the files it spans. Directly powers:
- "which tests exercise feature X?"  - "coverage gap *by feature*"  - "onboarding slice for feature X".
- **Op sketch:** richer `find_modes(..., detail="feature_map")` or `feature_map(coverage)`.
- **Caveat:** modes are axes, not hard partitions; a function loads on several. Label honestly.

### B2. Behavioural outliers — unique-behaviour vs everything-touching tests
A test row's reconstruction from the top-k modes: **low residual** = typical behaviour; **high residual /
orthogonal to all modes** = unique behaviour (keep — it's the only thing testing something) OR a
touches-everything smoke test (different signature: high mass on mode 1). Distinguish the two by mode-1
loading.
- **Op sketch:** `find_modes(..., detail="outliers")` → tests tagged {typical, unique, smoke}.
- **Caveat:** k choice affects residual; report the k used.

### B3. God-function / always-on-core flag
Functions loading heavily on mode 1 (the "everything" axis — 45.9% on stitchgraph) = the always-on core
touched by nearly every behaviour → highest blast radius, prime review/refactor targets. Fuse with
`find_chokepoints` (static articulation) for a "critical core" report that's both structural and runtime.
- **Caveat:** mode-1 dominance is partly inherent (every test boots the same setup); normalise or note it.

### B4. Behavioural embeddings → runtime `find_similar`
The mode-space coordinates (U for tests, V for functions) are low-dim behavioural embeddings. Two
functions close in mode-space *behave* similarly even if lexically/structurally different — a **runtime**
complement to the current token/AST `find_similar`. Enables "find functions that behave like X".
- **Caveat:** only meaningful for functions the suite exercises; needs enough modes for a stable embedding.

---

## Tier C — fused with other stitchgraph data (git, multiple snapshots)

### C1. Runtime-informed `risk`  ★ strong
Today `risk` = git churn × static centrality. Add a runtime term: **co-activation centrality** (how many
behaviours a function participates in, from M). A function that is high-churn **and** high-co-activation
**and** on many modes is the most dangerous to touch — a sharper hotspot than churn×static alone.
- **Op sketch:** extend `risk(..., coverage=...)` with a behavioural-centrality factor.
- **Caveat:** needs both git history and coverage; degrade gracefully when either is absent.

### C2. `coverage_drift` — behavioural diff across releases
Diff two coverage artifacts (v_old, v_new): which functions gained/lost test exposure, which **modes**
appeared/vanished/reshaped. "Release N changed behaviour cluster 3 (sessions)" — a behavioural changelog.
Pairs with the existing `graph_diff` (structural diff) for a structure+behaviour release report.
- **Op sketch:** `coverage_drift(old.json, new.json) -> {gained, lost, mode_shift}`.
- **Caveat:** modes aren't stably ordered across runs; align by function-loading overlap, not index.

### C3. `impact_of` cross-validation (already partly in `select_tests`)
Systematically report, per changed symbol: static-only reachers (possible over-approximation or coverage
gap) vs runtime-only (dynamic dispatch the graph missed) — a standing **precision/recall audit of the
call graph itself**, using runtime as ground truth. Could even feed back to improve the resolver.

---

## Tier D — deeper / speculative

- **Anomaly detection on M** — a function whose activation pattern doesn't fit any mode may be
  mis-placed (belongs in another module) or a cross-cutting concern; SVD reconstruction error flags it.
- **Temporal co-activation** (if coverage carries ordering) — call *sequences*, not just co-occurrence →
  closer to true dynamic call graphs / state machines. (Needs richer capture than line coverage.)
- **Mutation-testing prioritisation** — mutate functions in the smallest modes first (fastest to kill),
  or target functions with few covering tests (weakest spots).
- **LLM-facing "behavioural brief"** — one MCP call that bundles, for a symbol: its mode(s), co_change
  neighbourhood, tests to run, and hidden-coupling partners → a change-planning context pack.

---

## Recommended build order (my pick)

1. **A1 `untested_functions`** — biggest, cleanest win; the `find_stale` fusion is uniquely stitchgraph.
2. **A2 diff → CI subset** — the everyday practical driver; small extension of `select_tests`.
3. **C1 runtime-informed `risk`** — sharpens an existing flagship op with the new signal.
4. **B1 `feature_map`** — turns `find_modes` output into something an LLM/human acts on directly.
5. Then A3/A4/B2/B3/C2 as appetite allows.

Everything above is advisory, read-only, and cardinal-safe by construction — same discipline as the
shipped ops. Each is one focused op + tests + docs + the two-clean-panel gate.

## Honest framing (carry this into the README when any of these ship)

POD/co-activation is the one part of stitchgraph that is genuinely **LLM-complementary** rather than
LLM-redundant: it is grounded in *runtime measurement* + *linear algebra*, which a model cannot reproduce
by reading source at any context size (you can't "read your way" to "these 62 tests cover everything" or
"these two functions are implicitly coupled"). But it is **not free** — it requires a runnable suite and a
coverage capture, and it only sees exercised code. Every tool here should say so.
