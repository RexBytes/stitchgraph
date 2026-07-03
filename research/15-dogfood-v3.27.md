# 15 — Dogfood: v3.27.0 on itself — the graph watches its own refactor

**Thread:** post-v3.27.0 (2026-07-03). Same battery as `research/14`, run on the release that
contains the D2 dedup — which makes this round a controlled experiment: research/14 measured the
codebase *before* the nine body-matrix frontends were deduplicated, this round measures *after*.
Three questions: is the refactor visible in the graph? did it preserve runtime behaviour? and
does the toolkit catch anything new?

Setup: fresh index of `src` (866 nodes, was 899); full suite under per-test coverage
(2,350 tests → 940 logical rows × 754 executed functions, was 790).

## Headline: the tool caught its reviewer's dead code

`find_stale` returned **two** candidates this round: the known `supported_languages` advisory —
and **`structure_common.py::parse_tree`**. The second is real: the D2 stage-2 patch *added* the
shared walk-guard helper but the transformation script never wired the nine `_walk` functions to
call it, and `ruff --fix` then silently removed the unused imports, hiding the slip from every
gate (the fingerprint differential and the oracle battery measure *outputs*, and dead code has
none). It shipped in v3.26.0 as genuinely dead.

`find_gaps` corroborated from the runtime side — its `untested_dead` list is exactly
`{supported_languages, parse_tree}` — static reachability and runtime coverage agreeing on both.
Fixed in v3.27.1 by wiring `parse_tree` into the nine walk guards (its intended job) and, while
there, pointing the seven remaining `_walk`-local `text` helpers at the shared `node_text`.

The meta-lesson is the same one the review process keeps re-learning: **every gate measures what
it measures.** Output-equivalence oracles prove a refactor changed nothing — including, in this
case, that the new helper changed nothing because nothing called it. Only a liveness view sees
that. The two views want to be run together, which is precisely what this dogfood does.

## The refactor, as the graph sees it

| Signal | v3.25.x (research/14) | v3.27.0 | Reading |
|---|---|---|---|
| nodes | 899 | 866 | −387 lines of dedup |
| `orient` hubs | seven byte-identical `_walk.text` clones @ fan_in 122 | same clones @ 97 (residual — fixed in v3.27.1) | duplication literally ranked as "hubs" shrinks as it's removed |
| `find_subsystems` | 434 / **329** / 127 — frontends their own cluster | 411 / **320** / 126 | the D2 cluster shrinking, not gone (`ev`/`do` semantics deliberately stay per-language) |
| `scan` findings | 192 (3 orange) | 167 (same 3 orange) | 25 name-ambiguity god-object artifacts evaporated with the duplicate helpers |
| `find_stale` | 1 advisory | 1 advisory + the real `parse_tree` catch | above |

The three oranges are the same three deliberate constructs verified in research/14
(`_object_members ↔ _collect`, `_plain ↔ to_dict`, the `_M2V_TRIED` latch) — stable, as they
should be.

## `coverage_drift` — first real cross-release use

Fed the v3.25.1 coverage artifact as `old` and the v3.27.0 one as `new`, the op narrates the
refactor from runtime evidence alone:

- **lost coverage (48):** the nine deleted `similar.py::_*_fn_fingerprints` iterators and every
  frontend's per-file `new_id` / `data_from` / `text` / `freevar` closure copies;
- **gained coverage (12):** `structure_common.{nc, first, last, make_parser, op_text, node_text,
  vfg_state, pdg_state, pdg_state.new_id, pdg_state.data_from, …}` and the one generic
  `similar.py::_ts_fn_fingerprints`.

That is a behavioural changelog of the dedup, derived without reading a line of the diff.

## POD stability — behaviour-preservation, measured at runtime

- **Intrinsic dimensionality: 27 → 27.** The refactor moved ~400 lines and the suite still
  exercises exactly 27 independent behaviours — the strongest runtime statement of
  "behaviour-preserving" available, complementing the byte-identical output oracle.
- Minimal cover 64 → 65 tests; redundant pairs 1,076 → 1,056; mode spectrum unchanged at the top
  (entry-point machinery 45.8%, tree-sitter extraction 18.8%) — and mode 5 is now literally
  auto-labelled **"pdg state data from"**: the shared scaffolding is a visible behavioural mode.
- `runtime_risk` (fixed in v3.25.1, exercised again here): `treesitter.py` remains the
  churn×behaviour hotspot (73 × 12,675); `operations.py` stays demoted vs its static-risk rank.

## Verdict

Dogfooding a refactor with the full battery gives three independent confirmations you can't get
from any single gate: outputs identical (fingerprint differential), structure thinner in exactly
the intended places (orient/subsystems/scan deltas), behaviour untouched (POD invariants + drift
narrating only the intended moves) — plus one genuine catch (`parse_tree`) that only the liveness
view could see. This is the workflow the toolkit exists for.
