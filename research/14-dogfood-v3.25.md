# 14 — Dogfood: v3.25.0 on its own source (full battery, first POD run on the fixed math)

**Thread:** post-v3.25.0 (2026-07-03), immediately after the external-review hardening release
merged. The maintainer asked: run the released tool on its own code — the full static battery plus
the POD toolkit. This is also the **first `find_modes` run since the F3/F4 fixes** (full-spectrum
intrinsic dimensionality; test-id normalization), so it doubles as a live validation of both.

Setup: `reindex src` (899 nodes: 51 modules, 32 classes, 734 functions, 75 methods) into an on-disk
store; the whole test suite run under `pytest --cov=src/stitchgraph --cov-context=test`
(2,349 tests, 15m55s) and converted with the shipped `scaffold_coverage` kit → 2,356 raw context
rows over 790 executed functions.

## Headline numbers

| Op | Result |
|---|---|
| `find_stale` | 1 advisory candidate (`treesitter.py::supported_languages`), 0.6/needs_review |
| `find_holes` | 0 |
| `scan` | 192 issues — **3 orange, all verified deliberate** (below); 189 green, self-graded name-ambiguity artifacts |
| `find_chokepoints` | top: `treesitter.extract` (blast 10), `_collect` (6) |
| `find_subsystems` | **3 clusters — the 329-node one IS the body-matrix frontends** (below) |
| `risk` | treesitter.py (73×469), operations.py, python.py |
| `find_modes` | **intrinsic dimensionality 27 (exact)** — the pre-F3 code would have reported ≤ 16 |
| minimal test set | **64 of 939 logical tests** cover all 790 executed functions (6.8%) |
| `find_gaps` | 18 untested-live, **1 untested-dead = the same `supported_languages`** find_stale flags |
| `runtime_risk` | **found a real bug** (src-layout join, fixed this session) — then: treesitter.py top (churn 73 × behavioural centrality 12,664) |

## The three scan oranges — all true structure, all deliberate (verified by reading)

1. **`_object_members ↔ _collect`** (treesitter): necessary mutual recursion — object literals
   contain functions whose bodies contain object literals. Bounding it would be a cardinal recall gap.
2. **`_plain ↔ Result.to_dict`** (envelope): the serialization chokepoint recursing through nested
   payloads. By design.
3. **`_M2V_TRIED` data loop** (similar): the once-only embedder bootstrap latch
   (`find_similar → _try_model2vec → set_embedder → _EMBEDDER/_M2V_TRIED`), documented as
   attempt-at-most-once. Write-once memoization, not runaway feedback.

Verdict: the orange tier surfaced exactly the three most interesting deliberate constructs and
nothing spurious — a precision result worth having on the record. (Soft detector idea: a
write-once boolean latch is distinguishable from genuinely mutable feedback state.)

## The graph rediscovers the review's D2 finding

`find_subsystems` partitions the codebase into three clusters:

| size | auto-label | reads as |
|---|---|---|
| 434 | "stitchgraph core store resolve resolver" | the pipeline: store + operations + resolvers + adapters |
| **329** | **"build pdg vfg walk text"** | **the nine body-matrix frontends** |
| 127 | "rec direct is names name" | the extractors' name-resolution helper family |

The 2026-07-03 review's D2 finding (nine hand-synchronized `structure_*.py` files, ~40–50%
mechanically shared, fixes demonstrably not propagating) appears here as a *structural fact*: the
frontends are their own subsystem, and `orient`'s hub list contains seven byte-identical
per-language `_walk.text` helpers, each at fan_in 122. The staged dedup plan is recorded in
`docs/REVIEW_FINDINGS_2026-07-03.md` (D2).

## POD — what the suite's behaviour actually looks like

- **Normalization (F4) working end-to-end:** 2,356 raw coverage context rows collapse to **939
  logical tests** (parametrized cases merging), and every id in `minimal_test_set` is runnable.
- **Intrinsic dimensionality 27, exact** (dense solver, no lower-bound flag): the suite exercises
  ~27 independent behaviours. The pre-F3 code capped this metric at 16 — on its own repo the bug
  would have understated the answer by at least 11. First honest measurement.
- **The modes are the architecture:** mode 1 (45.6%) = the Python extractor's role/entry-point
  machinery; mode 2 (18.9%) = the tree-sitter extractor; then VFG/WL fingerprinting (4.7%),
  detector/find_stale (3.1%), store+migration, and per-language body-matrix modes (C++, Rust …).
  Same story as the Flask study: the singular vectors recover runtime subsystems unsupervised.
- **Minimal cover / fail-fast order:** 64 tests cover every executed function; `test_order`'s
  prefix is the same 64, and its first pick
  (`test_cpp_structure_mode_ranks_same_language_only`) alone executes 225 functions.
- **Redundancy:** 1,076 identical-profile pairs in 127 groups (largest 19, the per-language
  completeness batteries) — coverage-identical but behaviourally distinct parametrized fixtures;
  the review-aid-not-auto-delete caveat is doing real work here.
- **`find_gaps` corroborates `find_stale`:** the 1 untested-**dead** function is exactly
  `supported_languages` — static reachability and runtime coverage agree. The 18 untested-**live**
  are almost all `algebra.py` (the GraphBLAS layer): an honest environment artifact — the container
  can't install `python-graphblas`, so those tests skip. In CI's full-extras env this list should
  shrink to near zero; the fusion did its job by *localizing* the gap.
- **`find_core`:** the always-on nucleus is `Store.__init__`/`_migrate` (60% of all tests),
  `Result.__post_init__` (57%), `envelope.ok` (54%), `config._load` (50%) — store + envelope +
  config are the load-bearing trio every behaviour rests on.
- **`find_outlier_tests` (breadth-keyed since F11f):** the top unique-behaviour tests are the
  grammar-load-failure warning test, the deep-expression graph_diff test, the corrupt-store
  battery, and the new missing-db CLI refusal test — precisely the weird-path tests nothing else
  resembles. No false "smoke" labels.
- **`find_coupling`:** top implicit pair is `config._load ↔ envelope.set_review_threshold`
  (score 1.0, 469 shared tests) — the config module reaching across to set the envelope's review
  threshold, a real cross-module side-channel the call graph doesn't bind (the call resolves
  through the config-loading path). A genuine, known-by-the-author coupling, found blind.
- **`select_tests('modes.py::decompose')`:** 10 tests, all `runtime_only` — the ops-registry
  indirection means the static blast radius misses them; the runtime matrix is what makes the
  selection work. (Also a good demo of the qualified-id refusal: the bare name `decompose`
  correctly refused with the two-candidate hint.)

## New defect found (and fixed) by this dogfood

**`runtime_risk` returned ok with zero hotspots on any src-layout repo**: coverage fids are
relative to the indexed root (`stitchgraph/core/…`) while git churn paths are repo-relative
(`src/stitchgraph/core/…`), and the join used raw strings — the exact class of path-namespace bug
`risk` already solved with `_git_path_mapper`. Fixed by mapping through the same helper
(+ regression test `test_runtime_risk_joins_churn_on_src_layout`). Post-fix, its answer is sharp:
treesitter.py is the file that both changes most and carries the most behaviour
(churn 73 × behavioural centrality 12,664), with `extract/python.py` second on behavioural weight
alone — a materially different ranking than static `risk` gives (operations.py drops from #2 to
#4 once "depended on" is measured by what tests actually execute).

## Through-line

Same lesson as research/10, now sharper: the static half described the codebase correctly but told
us what the review already knew (the oranges were deliberate; the duplication was known). The POD
half produced numbers no amount of reading recovers — 27 behaviours, a 64-test cover, the
config↔envelope side-channel, the treesitter churn×behaviour hotspot — **and** caught a real bug in
its own newest op, because dogfooding runtime measurement exercises paths static analysis (and
panels) don't. The toolkit's forward-looking ops are the part that pays rent.
