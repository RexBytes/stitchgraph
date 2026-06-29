# Findings — Experiment 01: structural redundancy / clone detection

**Question (#1):** can the matrix representation surface *reducible* code — redundant or
near-duplicate functions a token-differ would miss?

**Date:** 2026-06-29  ·  **Corpus:** `src/stitchgraph` (476 nodes, 135 functions/methods with ≥3 callees)

## Method

For every `Function`/`Method` node, build a **callee fingerprint** = the set of
`(relation, callee-leaf-name)` over its `CALLS`/`REFERENCES` out-edges. Then:

- **exact structural clones** = nodes with an *identical* fingerprint (≥ `MIN_SIG`=3 callees);
- **near-duplicates** = node pairs with `Jaccard ≥ 0.70` and ≥3 shared callees.

This is a deliberately *within-language, within-codebase* use — one extractor, one language — so
the prior §2 caveat ("topology tracks the extractor, not the function, across languages") does
**not** bite. Structural similarity here reflects real code-shape similarity.

## What the raw pass found (and why it was misleading)

7 exact-clone groups and 46 near-duplicate pairs — at first glance "impressive". But reading the
top candidates shows the headline was **hub-callee noise**: in an edge-building module
`{Edge, Provenance, Relation, append}` are touched by nearly every function (`append`×63,
`Relation`×52, `Store`×41, `Provenance`×34 across 135 functions). Any two edge-emitters therefore
look ~0.8 similar without being redundant. The supposed "genuine refactor candidate" —
`_add_call`/`_add_ref` — are in fact two *distinct* 4-line convenience wrappers (one is `CALLS`
with a caller-supplied weight/provenance, the other is `REFERENCES` fixed at `0.95`/`EXTRACTED`).
They share a *vocabulary*, not a *behaviour*; merging them would reduce clarity, not redundancy.

## The precision pass (`experiment_idf.py`) — the honest answer

Down-weighting callees by IDF (`weight(c)=log(N/df(c))`) and then asking "how many *distinctive*
(rare) helpers does each pair actually share?" collapses the noise. After that filter, the entire
corpus yields **exactly three** pairs with any distinctive shared helper:

| Pair | distinctive shared | disposition |
|---|---|---|
| `reachable_from` ~ `reverse_reachable_from` | `_bfs`, `_Adjacency` | direction-symmetric twins; shared logic **already** extracted into `_bfs`/`_Adjacency` |
| `Store.callers_of` ~ `Store.callees_of` | `_row_to_edge` | direction-symmetric twins; shared logic **already** in `_row_to_edge` |
| `ExpressRouteResolver.resolve` ~ `SpringRouteResolver.resolve` | `_scan` | different frameworks; common scan **already** factored into `_scan` |

Every survivor is a deliberate symmetric pair whose common code is *already* in a shared helper —
i.e. the redundancy the tool would propose has **already been removed**. There is **no actionable
structural redundancy in stitchgraph's own source.**

## Verdict

Two findings, both honest:

1. **The capability is real but lower-precision than the raw numbers suggest.** Structural clone
   detection works, but raw set-Jaccard is dominated by hub callees; the IDF + distinctive-helper
   refinement is *required*, not optional, to get a trustworthy signal. With it, the tool correctly
   reports "nothing to merge here."

2. **stitchgraph's code does not need tidying on these grounds.** The codebase is already
   well-factored — the only structural twins are intentional forward/reverse API pairs whose shared
   work is already extracted. Refactoring the (cardinal-critical, panel-hardened) extractor on the
   strength of the raw heuristic would have churned proven code for **zero** real reduction. The
   tool's most valuable output here was a confident *negative*.

The capability remains the cleanest eventual `src/` promotion (a structural sibling to
`find_similar`), but its honest demo corpus is **another** project, not this one — validating it
needs a codebase that genuinely contains copy-paste duplication.

## Next refinement (deferred)

- Add edge-multiplicity and callee *order* (currently a set — loses sequence/branching).
- Validate against a known refactor: pick a real "extract helper" commit from a third-party repo's
  git history and check the tool flagged the pair *before* the refactor (the §1 bug-fix-commit
  validation pattern) — on a corpus that actually has duplication.
