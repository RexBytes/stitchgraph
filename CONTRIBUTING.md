# Contributing to stitchgraph

This document records **how stitchgraph is tested and reviewed**. It is the
project's quality contract: changes follow the testing philosophy below, and
significant changes are expected to survive the review approach below.

```bash
pip install -e ".[dev]"
pytest -q
ruff check src tests
mypy
```

---

## Testing philosophy

Write tests that would **fail if a promise were broken**, not tests that confirm
the code you just wrote happened to run.

1. **Contracts are promises.** Every operation returns a `Result` envelope whose
   fields are promises: `confidence`, `provenance`, `needs_review`, `urgency`,
   `ok`. Every docstring sentence and named threshold is a promise too. Before
   testing a function, list its promises and write one test per promise. If you
   can't, the docstring is aspirational — fix the docstring.

2. **Boolean / classification functions: all four corners.** For a predicate
   like "is this node dead?" cover confirmed-true (an orphan), confirmed-false (a
   reachable function), false-for-a-*different*-reason (an exported public symbol
   with no internal caller — live *because* it's the API, not because it's
   called), and true-under-adversarial-input (a framework-callback override that
   only *looks* dead). These exact cases live in `tests/`.

3. **Every parameter: empty, boundary, and the messy real input.** `reindex` on
   an empty dir, a dir with a malformed/binary file, and a real multi-language
   tree. `get_matrix` at the scope limit and one past it.

4. **Pin thresholds just above and below.** `find_stale` confidence is `0.6`
   name-based, `0.78` with a runtime trace, `0.1` with no roots; the
   `REVIEW_THRESHOLD` (0.80) gate; `get_matrix`'s node `limit`. A "limit of N"
   means nothing unless N passes and N+1 refuses.

5. **Precision over recall is the load-bearing invariant.** The cardinal sin is
   flagging *live* code as dead. Resolution deliberately **over-approximates**
   (ambiguous name → edges to *all* candidates) so reachability never under-
   counts. A test that finds live code flagged stale is a release blocker; a
   missed dead function is a documented limitation. `tests/test_properties.py`
   asserts this over random graphs.

6. **Two implementations must agree.** The GraphBLAS sweep and the pure-Python
   frontier BFS are independent; a Hypothesis property asserts they return the
   *same* reachable set on every random graph. Any divergence is a real bug in one.

7. **Reach for the dependency's exact behaviour.** Bugs hide in *which node type
   a tree-sitter grammar emits* (PHP's identifier is literally `name`; C#'s
   grammar is `csharp` not `c_sharp`) and *which schema a library builds* (pydantic
   can't model a Protocol-typed MCP param). Probe the real grammar/library, not
   an assumption.

8. **Cross-language and full-stack as round-trips.** Build a synthetic project
   (HTML form / JS `fetch` → route → handler → SQL) and assert `trace_path`
   returns the whole chain end to end. Per-language: assert a call graph and that
   dead code is found *and* live code isn't, for each language.

9. **When a test reveals a real source bug, keep the test.** Pin the contract it
   restores; don't quietly fix source in the same pass that changed the test.

Test layout: `tests/test_<area>.py` plus cross-cutting `test_properties.py`
(Hypothesis invariants) and `test_safety.py` (read-only / malformed / traversal).
Tests must not modify source.

---

## Review approach: competitive multi-model panel

stitchgraph is reviewed by **spinning up several review agents on *different*
underlying models, pointed at the same code**, then adjudicating their reports.

**Why it works.** Different models have different blind spots. Run head-to-head
on identical scope, their *overlap* is high-confidence and their *singletons* are
leads to verify.

**How to run it.**

1. **Identical brief, different models.** Same scope and brief to each reviewer,
   varying only the model. The brief **must include the full testing philosophy
   above, verbatim** — every model is entitled to the same standard. Tell each to
   read `LIMITATIONS.md` first and not re-flag documented tradeoffs, to prove
   findings with a real-input repro, and that false/mock-only findings count
   against them. **Sub-agents stay on the same model** as their panel slot;
   cross-model mixing happens only at the panel level.

2. **Coordinator adjudicates; never relay unverified.**
   - **Consensus** (≥2 models) → fix.
   - **Singleton** → reproduce it yourself before touching code.
   - **False / mock-only / documented** → dismiss explicitly with the reason.
   - Reconcile reviewers' line numbers against current `HEAD`.

3. **Fix in adjudicated batches with regression tests.** Every fix lands with a
   test pinning the contract it restores (four corners, just-above/below,
   precision-preserving).

4. **The done signal.** A panel that returns only confirmations of prior fixes
   (no new real defects) — and reviewers that *cite* `LIMITATIONS.md` when
   triaging — is the signal the surface has converged.

### Mind the blind spots

A panel only converges on code it actually exercises. stitchgraph's tree-sitter
languages, graphblas, sqlglot, jedi, and the MCP server are **gated by optional
deps**; without them installed those tests *skip* and a "clean" panel means
nothing for that surface. Before trusting convergence, install the extras (CI's
`[all,dev]` job does) and list the paths a panel could not run.

## Lessons (reusable)

1. **Multi-model panels beat any single reviewer** — diverse blind spots.
2. **Give every reviewer the full philosophy, verbatim.**
3. **Demand real-input evidence; reject mock-only findings.**
4. **Convergence is non-monotonic and never reaches zero** — measure residual
   risk (weighted yield, clean streak, confidence), don't chase "no bugs".
5. **The late-stage defect class is symmetry gaps** — a guard present in one path
   but not its siblings (one language extractor vs the others, one resolver vs
   its twins, a method vs its wrapper). Audit by a path×behaviour matrix.
6. **CI is non-negotiable** — gated/optional paths skip locally; CI runs
   tests+lint+type on every push, installs the extras so they actually run, and
   has a no-extras job to prove the stdlib-only core.
7. **Pin and bound dependencies** — an open `>=` will eventually remove an API.
8. **Make the release decision explicit and measurable** (the readiness rubric).
9. **Write down deliberate tradeoffs** (`LIMITATIONS.md`) so panels don't
   re-litigate and agents don't "fix" intended behaviour.
10. **Be honest in the bookkeeping** — dismiss false positives with a reason;
    keep the tree committed and `HEAD` verified.
