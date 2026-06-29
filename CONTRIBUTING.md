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

### Resuming the review in a fresh session

Everything needed to run the next panel lives in the repo — a new session can pick
up cold:

- **`review-kit/panel_prompt.template.md`** — the panel brief. Send the same filled
  text to one reviewer per available model.
- **`REVIEW_HISTORY.md`** — the trajectory table, the per-panel narrative, and the
  running already-fixed list (so reviewers hunt only what's left).
- **`release_readiness.json`** — config (severity weights, `tau`, `available_models`)
  and the panel records.
- **`RELEASE_READINESS.md`** + **`python scripts/readiness.py`** — the release gate
  and the live RRS / convergence / clean-streak verdict.
- **`LIMITATIONS.md`** — documented tradeoffs reviewers must not re-flag.

**To start the next panel**, ask the session to: run the next review panel — one
slot per available model (opus + haiku, plus sonnet when its API is up; otherwise a
pasted third-party core-only review fills the sonnet slot), using
`review-kit/panel_prompt.template.md` filled with the current `HEAD` sha and the
already-fixed list from `REVIEW_HISTORY.md`. Then adjudicate, fix in batches with
regression tests, append the panel to `release_readiness.json` + `REVIEW_HISTORY.md`,
re-run `scripts/readiness.py`, and commit. **Release gate:** hard gates green AND
RRS ≥ 90 AND **2 consecutive full-diversity clean panels** (weighted yield < `tau`).
The maintainer tags/releases manually; the version reads `1.0.0` only at that point.

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

---

## The cardinal-hardening loop (dogfood + docs)

The whole v2.1.x → v2.2.0 hardening line — the per-language cardinal sweep plus the #70–#89
follow-up backlog — was driven by one repeatable loop. If you're extending stitchgraph to a new
idiom, language, or framework, run *this*, not ad-hoc guessing:

**The one invariant everything serves:** *live code is never confidently flagged dead.* Reporting a
**live** symbol as dead — a "cardinal" — is the single failure that destroys trust in a dead-code
tool: once it happens, users stop believing any finding. So the bias is fixed and asymmetric:

- **Under-rooting (a live symbol with no root/edge → flagged dead) is the cardinal sin.** Never ship it.
- **Over-rooting (a genuinely-dead symbol kept alive) is SAFE** — it only lowers dead-code recall.
  When unsure whether something is reachable, root it.

**The loop:**

1. **Dogfood on real repos to surface cases.** Index real-world projects (we used cobra, tokio,
   rxjs, Magento, the Linux kernel, …) and read what `find_stale` flags. A *live* symbol in the
   flagged set is a cardinal — that's your lead. Synthetic tests won't find these; real idioms do.
2. **Read the actual language/framework docs to find ground truth.** Don't guess *why* it's live —
   look up how the runtime actually invokes it: the ECMAScript spec (well-known Symbols, `get`/`set`,
   `toJSON`), the Bash manual (`declare -fx`, `trap`, `PROMPT_COMMAND`), JNI, `#[ctor]`, C++
   range-`for` customization points, the CPython data model (dunders), pytest's hook-discovery rules.
   The docs tell you the *exact* form to root, so you root the right thing instead of pattern-matching
   noise.
3. **Reduce to a minimal scratchpad fixture** that reproduces the false-dead in isolation (the
   smallest file where a live symbol is flagged), and confirm it via `reindex` + `find_stale`.
4. **Fix by adding a root (or edge), never by removing one.** Prefer the additive direction — an
   additive root *cannot* introduce a cardinal, only over-root. Mirror an existing rooting pass
   (`EXPORT_SYMBOL` text-scan, `_bash_callback_refs`, the `callback`/`exported` role unions) rather
   than inventing a mechanism.
5. **Gate it** (`ruff` + `mypy` + full `pytest` + the differential streaming oracle + a mutation
   meta-oracle on the new helper) and add a regression test that pins **both** directions: the live
   symbol is now live **and** a genuinely-dead sibling still flags (the precision guard — this is how
   you prove you didn't just blanket-root).
6. **Adversarially review** with the multi-model panel (below). For a cardinal fix, run two
   consecutive clean full-diversity rounds; for a clearly-additive, cardinal-safe recall fix a lighter
   pass is acceptable — but always include the "does a genuinely-dead symbol still flag?" probe.
7. **If tightening would risk the invariant, don't — document it instead.** Several #70–#89 items
   (e.g. `_framework_classes` name-collision over-masking, the one-word `trap` reset, the broad Go
   selector rooting) are *over*-rooting. "Fixing" them means un-masking, which risks a cardinal, so
   they're left as deliberate cardinal-safe boundaries recorded in `LIMITATIONS.md` /
   `REVIEW_HISTORY.md`. A documented safe boundary beats a precise-but-dangerous change.

The payoff compounds: real repos tell you *where* live code is mis-flagged; the docs tell you *what's
truly invoked*; the additive-only discipline guarantees each fix can't regress the invariant; and the
panels catch the spellings your first fix missed (e.g. `declare -f -x` split flags, `as const`-wrapped
exports). That's the engine — reuse it.

---

## White-box symmetry closure (do this *before* the panel, not after)

The multi-model panel is **black-box**: it samples a structured space and keeps
hitting *different instances of the same gap*. That produces a long tail — you fix
the reported instance, the next panel finds a sibling. The cure is to stop fixing
*instances* and start closing *classes*, white-box, up front.

**The recurring defect is a symmetry gap** (Lesson 5): a guard/behaviour present
in one path but missing in its parallel siblings. The sibling set is almost always
**small, finite, and enumerable by `grep`** — so enumerate it, fix every member in
one pass, and pin the matrix with a test so the panel can never re-discover it.

**Worked example (panels R30–R31).** A round-30 fix added an unknown-receiver
name-based fallback to the *attribute-read* pass — but only in **one** of the three
scope edge-builders. Two later panels then re-found the same class twice:

| Scope edge-builder | `_direct_calls` | `_direct_names` | `_direct_attr_reads` |
|---|---|---|---|
| `_module_scope_edges` (module level) | ✅ | ✅ | ❌ → R31A cardinal |
| `_walk_scope` ClassDef (class body)  | (names) | ✅ | ❌ → R31A cardinal |
| `_walk_scope` FunctionDef (fn body)  | ✅ | ✅ | ✅ (round 30) |

The same round-30 fix also (a) set the **wrong provenance** on its new edge
(`_ref_edges` granted `INFERRED` only for `relation is CALLS`, so the new
`REFERENCES` edge stayed `EXTRACTED` → a heuristic path shouted RED — R31B
inflation) and (b) the parallel **corrupt-value** fix guarded the raw
`all_node_ids` projection but **not** the two row mappers or `get_meta` (R31B
crash). One fix, three follow-on blockers — all sibling sites the author didn't
enumerate.

**The method:**

1. **Name the axes.** For any fix, write down what varies around it:
   *scope* {module, class-body, function}; *expression kind* {call, attr-read,
   name-ref}; *language extractor* {python, tree-sitter ×N}; *column × reader*
   (every str-typed DB column × every site that reads it); *edge producer ×
   provenance*. These axes ARE the matrix Lesson 5 names.
2. **Enumerate the cells with `grep`, not memory.** e.g. `grep -n
   '_direct_calls\|_direct_names\|_direct_attr_reads'` finds *all three* scope
   builders at once; `grep -n 'row\["' src/.../store.py` plus "which reads bypass
   the mappers" finds every corrupt-value site. The set is finite — list it.
3. **Fix the whole column in one pass**, and trace each new artifact through the
   *next* stage (a new `Edge`'s provenance → urgency; a new node id → every string
   op that consumes it). Most R30–R31 fallout was an un-traced second-order effect.
4. **Pin the matrix as an executable test**, so adding a new scope/language/column
   without the guard fails CI instead of waiting for a panel:
   - a **parametrized cardinal test** over `{module, class-body, function} ×
     {call, attr-read, name-ref}` asserting live code is never flagged dead in any
     cell;
   - a **BLOB-in-every-str-column** test asserting no op raises on a corrupt index;
   - a **provenance test**: a name-based member resolution (call *or* read) is
     `INFERRED` → never RED.
   A matrix test is worth more than N point regressions: it fails for the *cell you
   haven't written yet*.

**Rule of thumb:** when a panel finds a symmetry gap, the deliverable is not "patch
that cell" — it's "enumerate the row/column, fix all of it, and add the matrix test
that would have caught every cell." Treat a single-cell fix as incomplete by
default.

## Methods to adopt next (beyond panels + matrices)

Ranked by expected leverage on stitchgraph's remaining tail:

1. **Mutation testing** (`mutmut` / `cosmic-ray`) — measures *test strength*, not
   coverage %. Surviving mutants name the contracts the suite doesn't actually
   pin (e.g. a flipped `>=`/`>` in a confidence gate). Highest signal for "is the
   suite real?"
2. **Metamorphic / differential properties as standing Hypothesis tests** — the
   ad-hoc "incremental == full reindex on find_stale AND fan_in across edit
   orderings" harness should be a permanent property over *random* multi-file
   projects and *random* edit sequences (add / delete / re-add / rename / move).
   Metamorphic relation: final graph is independent of edit order.
3. **Grammar-corpus tests per language** — for each tree-sitter grammar, a corpus
   exercising *every node kind it emits*, asserting extraction maps it (directly
   targets Lesson 7 "reach for the dependency's exact behaviour"; catches the
   `name`/`csharp` class of surprises structurally).
4. **AST-fuzzing the extractors** — Hypothesis strategies that generate random
   *valid* source (or coverage JSON / config) into `reindex`/`ingest_trace`,
   asserting two invariants only: never raise, never flag a reachable seed dead.
   Coverage-guided (`atheris`) on the parse/ingest boundary for the crash class.
5. **Edge-provenance audit** — enumerate every `Edge(...)` construction site and
   assert its provenance is set deliberately (the R31B EXTRACTED-vs-INFERRED bug
   was an un-audited producer). Pair with a "no RED on non-EXTRACTED" property.
6. **Parallel-site lint** — a cheap repo test that asserts structural symmetry
   directly: the scope edge-builders call the same pass set; no raw `row["id"]`
   string-typed read exists outside the guarded mappers. Fails the moment a new
   sibling diverges.

These convert "stochastic panel rediscovery" into "structural guarantee," which is
where the long tail actually ends.

## The differential-oracle harness (the tail-killer)

A review panel is **expensive black-box sampling** (several agents × minutes ×
tokens) of a structured space. A **differential oracle** is **cheap deterministic
sampling of the same space** (seconds, free, every CI run): generate an input,
compute the answer two independent ways, assert they agree. Move tail-hunting from
panels to oracles; reserve panels for discovering *novel classes* no oracle covers.

### The layer insight (why panels kept finding what property tests missed)

stitchgraph is a pipeline: **source → (extract) → graph → (mutate incrementally) →
graph → (algebra) → answer.** Oracles must be installed at *each* layer; a green
oracle one layer down says nothing about the layer above.

| Layer | Oracle that exists today | Status |
|---|---|---|
| **algebra** (graph → answer) | `test_properties.py`: GraphBLAS == pure-Python; `find_stale` never flags a reachable node; reverse-reachable is the inverse — over *random adjacency graphs* | **covered & converged** — no graph-level defect in rounds 28–31 |
| **incremental** (graph → graph) | only fixed-case tests (`test_incremental_*`, the function-move differential) | **partial** — needs a generator |
| **extract** (source → graph) | only fixed-case per-language tests + the scope×attr matrix | **partial** — needs a generator |
| **boundary** (corrupt/hostile input) | fixed BLOB-every-column + safety tests | **partial** — needs a fuzzer |

This is the whole story of rounds 28–31: the property tests stayed green because
the graph layer is solid, while **every real defect lived in the extract and
incremental layers** — which have fixed-case tests but **no generators**. The
tail ends when those two layers get the same generator-backed oracle the graph
layer already has.

### The three oracles to build (Hypothesis, in `tests/test_properties.py`)

1. **Incremental differential** (highest leverage — the richest defect vein, rounds
   22/24/29/31).
   - *Generator:* a random small multi-file project, then a random *edit sequence*
     — add / delete / re-add / rename-symbol / move-symbol-between-files / empty-a-file
     / introduce-a-homonym, applied via `Store.replace_file`.
   - *Oracle:* a full `sg.reindex` of the final on-disk state.
   - *Assert:* incremental == full on `find_stale`, `fan_in`, and `find_holes`.
     (Metamorphic corollary: the final graph is independent of edit order.)
   - Hypothesis *shrinks* a failure to the minimal project+sequence — the repro the
     panel would have spent an agent to construct. Would have auto-caught R29A and
     R31A's fan_in inflations.
2. **Cardinal source-matrix** (rounds 28/30/31).
   - *Generator:* random *valid* source placing a defined-and-used symbol across the
     axes — scope {module, class-body, function} × use-kind {call, attribute-read,
     name-ref, subscript, decorator, annotation} × indirection {direct, via-unannotated-
     param, via-constructor-result, via-subclass}.
   - *Oracle:* the symbol is reachable from a seeded entry point by construction.
   - *Assert:* reachable-by-construction ⟹ never in `find_stale` at confidence ≥ 0.5.
3. **Corrupt-store / hostile-input fuzz** (rounds 29/30/31).
   - *Generator:* take a valid index and mutate it — set a random column to a BLOB /
     NaN / inf / bad-enum string, truncate, drop a column; OR feed random bytes as
     source / coverage / config.
   - *Assert:* every op returns a `Result` (never raises) and emits no `Infinity`/`NaN`
     in `--json`. (`atheris` coverage-guided fuzzing is the heavier upgrade.)

### Economics and division of labour

- **Oracles own the tail.** They re-run every CI push in seconds for $0 and fail on
  the *cell you haven't written yet*. A bug a full panel took ~20 min and 6 agents to
  surface, a generator surfaces (and shrinks) in seconds, repeatably.
- **Panels own novelty.** Their value is finding a *new class* an oracle's generators
  don't yet reach (a new language quirk, a new envelope contract). Once a panel finds
  a class, the deliverable includes *extending the generator* so the oracle owns it
  thereafter — that is how the panel cadence trends to zero.
- **Mutation testing keeps the oracles honest** — it measures whether the suite
  (oracles included) actually pins the contracts, vs merely executing them.

### Keep oracles cheap (or you've just moved the long tail)

An oracle that is getting complicated is a smell: a 500-line "random valid project"
generator has its own bugs and blind spots, and maintaining it *is* a new long tail.
A good oracle is cheap because it leans on something that already exists:

- **a strong invariant at a chokepoint** (the row mapper: "every mapped field has its
  declared dataclass type" — ~40 lines, schema-derived, no generator), or
- **the real codebase as the corpus** (don't *generate* a project; `src/` is already a
  large valid multi-file one — apply a *mechanical* edit and diff incremental vs full), or
- **a small parametrized matrix** (a dozen fixed scope×use-kind cells, not synthesized
  source).

Reach for Hypothesis/`atheris` generators only when none of those three can cover the
case. If an oracle creeps past ~50 lines, stop and find the chokepoint invariant instead.

### Oracles are project-specific, and they rot

There is no generic oracle set — each project's oracles are **harvested from its
architecture** in an explicit design step. The reusable part is the *checklist of
shapes*, not the oracles:

- **Differential** — anywhere the system computes one answer two independent ways, that
  pair is a free oracle (here: full vs incremental reindex; GraphBLAS vs pure-Python).
- **Chokepoint invariant** — a single point all data flows through, with a shape/type
  contract (the row mappers; the `Result` envelope).
- **Metamorphic** — a relation that holds regardless of path (edit-order independence,
  reindex idempotence, add-then-remove == no-op).
- **Declared contract** — never-raise, precision-over-recall, confidence ∈ [0,1].

Same four questions, different answers per project: *where is the redundancy, the
chokepoint, the symmetry, the promise?*

Because oracles are derived from the architecture, **they go stale when the architecture
moves** — budget irregular re-validation:

- **The chokepoint moves.** A new raw query that bypasses the guarded mapper silently
  voids the corrupt-store oracle's coverage (rounds 30–31 were exactly this). Guard with
  the **parallel-site lint** ("no raw `row[...]` read outside the mappers").
- **The oracle passes for the wrong reason** (false-clean — a consumer-level oracle that
  never hit the path). Guard with **mutation testing as the meta-oracle**: inject a bug;
  if nothing catches it, an oracle has gone blind.
- **The invariant itself changes** (a field becomes legitimately optional; a new enum
  value). Guard by **tying the oracle to the single source of truth** (schema/dataclass)
  so it tracks the change instead of needing a hand-edit.

Treat oracles as a **small living layer**, re-derived when a change moves a chokepoint or
alters an invariant — add "did this move a chokepoint / change an invariant? revisit the
oracle" to the review checklist. Cheap oracles are also cheap to keep valid.

## Partition ownership of defect classes (discover → assign → audit)

The strategy that ends the tail: **every defect class has exactly ONE owning verification
layer, and the other layers explicitly disown it.** The win is not "more layers" — it's
that no class is left in a *seam* (half-covered by two layers that both report green) or
left ownerless (rediscovered by panels round after round). One owner per class, the way
the codebase keeps one producer per fact.

The layers and what each is structurally best at owning:

| Layer | Owns the class of… | Cost / cadence |
|---|---|---|
| **Oracle** (`tests/oracles/`) | differential properties (two computations must agree) + chokepoint/metamorphic invariants | cheap, every push |
| **Mutation** (`scripts/mutate.py`) | "do the tests/oracles actually bite?" (test-strength) | medium, on demand / release |
| **Panel** (multi-model) | *novelty* — a class no cheap layer covers yet | expensive, per round |
| **Static** (ruff/mypy) | type/lint defects | cheap, every push |

### How to partition — you DISCOVER it, you don't design it

You cannot enumerate a system's defect classes up front (the spec is discovered through
review — Rice's theorem). So:

1. **Wait and watch first.** Run panels. A class that *recurs across rounds* is the
   signal — it's a class with no owner (it was being "covered" by luck, not construction).
   Don't pre-build oracles for imagined classes; build them for classes the panel proves
   are real and recurring.
2. **Assign by shape.** For the recurring class, ask the four-shape question (differential
   / chokepoint-invariant / metamorphic / declared-contract). The shape that fits names
   the cheap owner. If none fits cheaply, it stays **panel-owned** until a shape emerges.
3. **Verify the owner is EXHAUSTIVE, then disown elsewhere.** A false owner is worse than
   none: a consumer-level "corrupt-store" check that only hits one path *looks* like an
   owner but false-cleans. Make the owner total (schema-derived, chokepoint-located), then
   *write down* that the other layers don't own it (e.g. "reach.py filters are
   oracle-owned; mutation cannot reach them").
4. **Convergence signal.** The panel cadence trending toward zero new classes IS the proof
   the partition is (currently) complete — not "no bugs", but "no *unowned* classes left".

### Occasional retest — ownership rots, so audit the boundaries

A partition is only valid for the architecture it was drawn on. Re-validate on a schedule
(release / nightly / on any structural change), NOT every push:

- **Chokepoint moved?** A new code path that bypasses the owning chokepoint silently voids
  ownership (rounds 30–31: a raw query bypassed the guarded mapper). → **parallel-site lint.**
- **Owner gone blind?** An oracle that passes for the wrong reason. → **mutation as the
  meta-oracle.**
- **Class boundary shifted?** A new sub-case the owner's generator/matrix doesn't reach. →
  re-derive the owner from the single source of truth (schema/dataclass), and when a panel
  *does* surface a new instance, the fix includes *extending the owner* so it stays total.

The discipline in one line: **discover classes by watching panels; give each recurring
class one exhaustive owner; disown it everywhere else; re-audit the boundaries when the
architecture moves.**
