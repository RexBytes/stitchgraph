# Testing strategy (a living plan)

This is the **map** of how stitchgraph is verified: every layer, the *failure mode*
it exists to catch, where it lives, and its status. It is deliberately a living
document — the plan is developed as the project moves (see "How this evolves").

The depth — *how* to write each kind of test, the white-box symmetry method, and the
differential-oracle design — is in **`CONTRIBUTING.md`**. This file is the inventory
and the roadmap.

> **Reusable for other projects.** The specific tests are stitchgraph's; the *structure*
> is general. Pick layers by the **failure modes** you must not ship, not by a pyramid
> picture. The two reusable tools are the **failure-mode → layer table** below and the
> **four oracle shapes** (differential / chokepoint-invariant / metamorphic / contract).

## The principle: cover failure modes, not test types

More test *types* is not the goal. Each layer earns its place by covering a failure
mode the cheaper layers below it cannot. Order them cheapest-first: a failure caught by
a 40-line oracle in CI should never be left for a multi-agent panel to rediscover.

## Inventory (layer → failure mode → where → status)

| Layer | Failure mode it catches | Where | Status |
|---|---|---|---|
| **Unit** | a single function breaks its contract | `tests/test_<area>.py` | ✅ present |
| **Integration / round-trip** | components compose wrong (extract→resolve→reach→op; cross-language chains) | `test_polyglot`, `test_frameworks`, `test_seams` | ✅ present |
| **Property-based** | an invariant fails on inputs nobody hand-wrote | `test_properties.py` (Hypothesis) | ✅ present (graph layer) |
| **Differential oracle** | two independent computations of one answer disagree | full vs incremental reindex (`tests/oracles/test_incremental_differential.py`); GraphBLAS vs pure-Python (`test_properties.py`) | ✅ present |
| **Metamorphic** | a path/order that must not matter, does | reindex idempotence; edit-order independence (`tests/oracles/`) | ✅ present |
| **Chokepoint-invariant** | a malformed record survives the one gate all data flows through | row-mapper type invariant (`tests/oracles/test_corrupt_store.py`); `Result` envelope | ✅ present |
| **Cardinal matrix** | a live use in some scope×use-kind cell is flagged dead | `tests/oracles/test_cardinal_matrix.py` | ✅ present |
| **Regression** | a fixed defect silently returns | `test_regressions.py` (one test per panel finding) | ✅ present |
| **Safety / hostile-input** | malformed/adversarial input crashes instead of returning a Result | `test_safety.py` | ✅ present |
| **Static** | type/lint defects | `mypy`, `ruff` (CI) | ✅ present |
| **Adversarial panel** | a *novel class* no automated layer covers yet | multi-model review (see CONTRIBUTING) | ✅ present |
| **Dogfood baseline** | a whole-system regression in real behaviour | run stitchgraph on `src/` (golden: holes=0 / scan red=1 orange=2 / find-stale=1) | ✅ present |
| **Release gate** | shipping before the snapshot is trustworthy | RRS + convergence + 2-clean streak (`scripts/readiness.py`) | ✅ present |

## Gaps (the roadmap — developed as we go)

Ordered by leverage. Each names the failure mode it would close.

1. **Mutation testing — IMPLEMENTED (`scripts/mutate.py`), expanding.** *Failure mode it
   catches: a test/oracle that does not actually bite (false-clean).* It is the
   **meta-oracle** — the only layer that checks the other layers. A tiny in-house AST
   mutator (one mutation at a time, revert after) rather than `mutmut`/`cosmic-ray`, whose
   mutants/-copy model fights this repo's src-layout + editable install (keep it cheap).

       python scripts/mutate.py src/stitchgraph/core/envelope.py \
           -- python -m pytest -x -q tests/test_core.py tests/test_properties.py \
              tests/test_regressions.py tests/test_eval.py

   *Kill-signal must be the tests that cover the module* (a too-narrow signal reports false
   survivors). Use a FAST subset, not the 70s oracle suite. A SURVIVED mutant → add the
   test that kills it (this cycle: 4 un-pinned `envelope` contracts found and pinned → 17/17
   killed). Run on demand / at release, not every push (it mutates source in place — never
   concurrently with a review panel).
   - *Status:* `core/envelope.py` at 100% (17/17). `core/store.py` row mappers + other
     pure/contract code: to add.
   - **Mutation has a sweet spot — not every module belongs in the target set.** It is
     leverage where contracts are pinned by FAST *unit* tests (pure logic: `envelope`,
     row mappers, `config`, `best_path`). It is the WRONG tool for modules whose
     correctness is pinned by an expensive *differential oracle*: `reach.py`'s
     adjacency/reverse/SCC filters are no-ops on clean graphs and its reachability runs
     the GraphBLAS fast-path, so the pure-Python branches + the `dst_id in nodes` dangling
     filter are exercised ONLY by `tests/oracles/` (incremental==full with dangling edges)
     and gb-off runs — a fast-signal mutation reports them as false survivors. **The
     differential oracle IS their bite-check.** So: **mutation target set = fast-unit-
     covered contract modules; `reach`/the incremental pipeline are oracle-owned** (a
     deeper gb-off + oracle-in-signal mutation run is an optional nightly, not the gate).
   - *Equivalent mutants:* some mutations have no observable effect (redundant defensive
     code, e.g. `best_path`'s both-endpoints-missing guard); triage and justify rather than
     chase a blind 100%.

2. **Performance / resource budgets.** *Failure mode: a change makes reindex go
   quadratic, OOM, or leak file handles on large inputs.* Today only ad-hoc reviewer
   stress + two point guards (Go-span bound, JSON depth-bomb). Add a standing budget
   test: an N-file synthetic tree reindexes under a wall-clock + memory ceiling; file
   handles returned to baseline.

3. **Real-world corpus.** *Failure mode: real code contains shapes neither dogfood (self)
   nor synthetic fuzz produces.* Clone a curated set of real OSS repos in CI; assert
   `reindex`/`scan`/`find_stale` never raise and the cardinal invariant holds. Cheap and
   high-signal; complements dogfood (one repo) and fuzz (synthetic).

4. **Durability / concurrency on the write path.** *Failure mode: an interrupted or
   concurrent write produces a corrupt index.* The corrupt-store oracle covers an
   *already-corrupt* DB; this would cover *producing* one — kill mid-`reindex`, or two
   writers, then assert the store still opens and every op returns a Result.

## How this evolves

- When a **panel** finds a new class, the fix includes the cheap layer that now *owns*
  it (a regression test, an oracle generator extension, a matrix cell) — so the panel
  cadence trends down. A panel finding that only gets a point-fix is incomplete.
- When the **architecture moves**, oracles can rot (a chokepoint relocates, an invariant
  changes). Re-validate: parallel-site lint catches a moved chokepoint; mutation testing
  catches a blinded oracle; schema-tied oracles track changed invariants. (See
  CONTRIBUTING, "Oracles are project-specific, and they rot".)
- Keep this table honest: a layer marked ✅ that hasn't run (gated optional deps not
  installed) is not coverage — CI installs the extras so the gated surfaces actually run.
