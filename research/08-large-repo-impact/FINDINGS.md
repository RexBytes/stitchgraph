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

## Not run (pending maintainer go-ahead): the agent A/B race

The originally-planned head-to-head — a fresh agent-with-stitchgraph vs agent-with-grep, both asked to
statically list the affected tests, graded against the 13 — is *set up* but **not executed** (it is
several expensive large-context agent runs). Given this tool-level result already answers the core
question with a clear, honest boundary, the agent race is offered as an explicit, costed next step
rather than run automatically. Its remaining value would be measuring whether an agent's *judgment on
top of* `impact_of` (a broad safety net) or grep (manual tracing) lands closer to the true 13.
