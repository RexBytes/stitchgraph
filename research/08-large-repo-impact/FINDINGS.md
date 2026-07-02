# Large-repo discovery: how precise is `impact_of` on real framework code?

**Question (maintainer):** the dogfood rounds 1–2 used small, self-documenting codebases where an LLM
alone did fine. Does stitchgraph earn its keep on a *large, real* codebase — the regime it's actually
for? Round 3 uses a real downloaded package and a **tool-independent dynamic oracle**.

## Setup

- **Codebase:** Flask 3.1.3 **source + tests**, fetched via `pip download --no-binary` (git clones
  are egress-blocked; the registry works). 67 files / 1710 indexed nodes — ~10× the round-1/2 base
  and full of framework indirection (request dispatch, blueprint registration, url building).
- **Oracle (tool-independent):** the bundled test suite run under `coverage.py` with **per-test
  contexts** (`--cov-context=test`) in an isolated venv (flask's tests need an older pytest; that
  venv is used *only* to compute ground truth — the measurement itself doesn't need it). This yields,
  for any target function, the **exact set of tests that dynamically execute it**.
- **Target:** `_split_blueprint_path` (`src/flask/helpers.py:635-641`) — a deep internal helper
  reached only *transitively* (via blueprint registration / `url_for`); **no test names it**. A pure
  "what depends on this?" discovery question.

## Result — `impact_of` is SOUND but very IMPRECISE on framework code

| Measure | Value |
|---|---|
| Tests that **dynamically** execute the target (ground truth) | **13** |
| `stitchgraph impact_of` static `tests_to_run` | **379** |
| Soundness — true tests captured by the static set | **13 / 13** ✅ (one apparent miss was a pytest param-id artifact: `…::test_blueprint_prefix_slash` stored without the `[-/-/]` suffix) |
| Precision — true / reported | **13 / 379 ≈ 3.4%** |

The reported `blast_radius` includes essentially all of Flask's core (`Flask.__call__`,
`dispatch_request`, `full_dispatch_request`, `url_for`, `make_response`, …). That's the mechanism:
`_split_blueprint_path` is reached from central hubs, so **static reverse-reachability balloons to
nearly the whole framework** — any test that touches request handling is "impacted."

## Interpretation (the honest answer)

- **stitchgraph's `impact_of` is a conservative, sound, *broad* over-approximation, not a precise
  oracle.** On framework code, "which tests are affected?" statically returns ~30× too many. It is
  genuinely useful as a **safety net** ("run at least these and you won't miss a real dependency" —
  it captured 13/13) and it's **fast, deterministic, whole-repo, and needs no test run** — properties
  an LLM reading code cannot reliably reproduce. But it does **not** hand you the precise 13; a
  developer/LLM still needs judgment to narrow it, or a dynamic coverage run to get the exact set.
- **This tempers the pitch honestly:** `impact_of` answers *"what could be affected (soundly)?"* not
  *"what is affected (precisely)?"*. The two are complementary — static over-set (cheap, sound) vs
  dynamic exact-set (needs execution). Neither an LLM+grep nor stitchgraph will surgically name the 13
  by static reasoning on this kind of indirection.
- **Re: "have we wasted time?"** No — but this sharpens *where* the value is: stitchgraph delivers a
  real capability (a fast, deterministic, sound impact over-set across a whole large repo) that grep +
  an LLM can't soundly reproduce; it is **not** a precision tool for impact-to-tests. That's a
  credible, defensible position — and finding its imprecision empirically is exactly the kind of
  honest boundary-mapping the review process values.

## Method notes / caveats

- Single target, single repo (n=1 target). The 3.4% precision figure is specific to a
  centrally-reached helper; a leaf function with few callers would score far higher precision. The
  *pattern* (static reverse-reachability over-approximates for hub-reached functions) is general.
- Static vs dynamic differ by nature; the soundness result (static ⊇ dynamic) is the expected and
  desirable direction for a "don't miss anything" safety net.
- Flask has **no same-named-method decoys**, so the grep-overmatch failure mode (round-2 conclusion's
  other lever) could not be exercised here.

## The agent A/B race — RESULT: a dead tie (stitchgraph gave no measurable edge)

Six fresh Sonnet agents (n=3 per arm) on identical Flask copies, same task: *statically* list the
tests that exercise `_split_blueprint_path` (running the suite / coverage forbidden). Arm A had
stitchgraph; arm B was grep + read only. Graded on precision/recall/F1 vs the 13-test truth
(param-ids normalised).

| Arm | listed | found (of 13) | precision | recall | F1 |
|---|---|---|---|---|---|
| **A — stitchgraph** (mean of 3) | 26.0 | 12 | **0.46** | **0.92** | **0.62** |
| **B — control** (mean of 3) | 25.7 | 12 | **0.47** | **0.92** | **0.62** |

Statistically indistinguishable. All six agents caught the **same 12**, missed the **same 1**
(`test_build_error_handler_reraise` — reaches the function only via a subtle error-reraise path all
six judged out), and over-listed to ~26 (≈14 false positives each).

**Why no edge — the decisive observation:** all three stitchgraph agents **tried `impact_of` and
discarded it**, independently, for the same reason — it returned a 0.51-confidence "ambiguous" blast
radius of ~280–379 tests (nearly the whole app; see the tool-level result above). They fell back to
exactly what the control did: `get-callers` → the **2 direct callers**, then **hand-trace** the
`"." in endpoint` runtime gate out to the tests. And `get-callers` on a *uniquely-named* function is
trivially replicated by one `grep` — which is how the control found the same 2 callers. So the one
stitchgraph query that helped added nothing grep didn't, and its headline feature for this exact
question (`impact_of` → "tests_to_run") was too imprecise to use.

**The real bottleneck was shared and semantic, not structural:** both arms over-approximated ~2×
because the true set depends on whether each blueprint test *dynamically dispatches through a dotted
endpoint* — a runtime-gating judgment neither a call graph nor grep resolves. The 14 false positives
are tests that statically reach the gated path but don't execute the lines; the 1 false negative is a
path all six mis-judged. Neither tool moves that needle.

## Overall conclusion of the dogfood thread (rounds 1–3 + this race)

Across **three regimes** — greenfield build (round 1), multi-session extend of an unfamiliar codebase
(round 2), and precise impact-discovery on a large real framework (round 3 + this race) — **stitchgraph
produced no measurable win for a capable LLM agent**, and in the timed rounds the control was slightly
faster (tool overhead). Its genuine, repeatedly-observed contributions were narrow and real:
`find-stale` caught actual dead code a reader would keep (round 1); `orient`/`get-callers` gave
fast, correct orientation; `impact_of` is a **sound but broad** safety net. None of these changed task
*outcomes* here.

**This does not prove the tool worthless — it maps its boundary honestly.** The regimes it should still
win (untested here, because this sandbox can't cleanly produce them): codebases too large for grep-BFS
to be tractable at all; **decoy-heavy** names where grep over-matches and structural resolution wins
(Flask had none); workflows that value a *cheap, deterministic, sound* over-set (CI gates, "don't miss
a dependency") over precision; and human (non-LLM) users of the CLI/report. For a strong LLM agent on
clean, well-named, in-context-tractable code — which is most of what we tested — **an LLM does the same
work without stitchgraph.** That is the honest answer.
