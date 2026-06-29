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

## What it found

7 exact-clone groups and 46 near-duplicate pairs. The signal sorts into three buckets:

**1. Genuine refactor candidates (true positives).** The edge-building family —
`_add_call` / `_add_ref` / `_module_load_edge_qual` / `_ref_edges` — share an *identical*
5-callee fingerprint `{Edge, Provenance, Relation, _Project, append}`. They really are the same
shape: construct an `Edge` with a `Provenance`, look up a `Relation`, append to the project. That
is exactly the "extract a common `_emit_edge` helper" candidate a human would want surfaced.

**2. Structural twins that *should* stay separate (true neutrals).** `fan_in`/`fan_out`,
`reachable_from`/`reverse_reachable_from`, `Store.callers_of`/`Store.callees_of` — symmetric
direction-pairs with identical call shape. The tool is *correct* that they're structural clones;
the right disposition is "leave them, the symmetry is the point." This is the on-brand outcome:
**the graph proposes, a human disposes.**

**3. Hub-callee noise (the limitation).** Many near-dup pairs collapse onto a few ubiquitous
callees (`Edge`, `Provenance`, `Relation`, `append`). Any two edge-emitters look 0.83-similar
because *everything* in that module calls those four. Raw set-Jaccard over-weights popular callees.

## Verdict

**Q1 is real and the strongest of the three questions** — the structural matrix surfaces
mergeable code (bucket 1) that a token-level differ misses, *and* it does so cheaply (one index,
two set passes). The caveat is precision: the ranking needs an **IDF-style down-weight** on common
callees so hub edges don't drown the signal. That is a small, well-understood fix, not a dead end.

This is also the cleanest candidate to eventually promote to a real operation
(`find_similar` already exists for token/embedding similarity; a *structural* sibling
`find_similar(..., mode="structure")` is the natural home).

## Next refinement (deferred)

- IDF-weight callees: `weight(c) = log(N_funcs / df(c))`; score = weighted-Jaccard.
- Add edge-multiplicity and callee *order* (currently a set — loses sequence).
- Validate against a known refactor: pick a real "extract helper" commit from git history and
  check the tool flagged the pair *before* the refactor (the §1 bug-fix-commit validation pattern).
