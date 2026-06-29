# Findings — Experiment 02: function-body matrix

**Question:** does matrixifying the *inside* of a function (the level below the call graph)
catch redundancy the callee-fingerprint (experiment 01) cannot?

**Date:** 2026-06-29  ·  **Corpus:** `src/stitchgraph` (379 functions with ≥14 body tokens)

## Method

Python-only (deep stdlib `ast`). Per function: a **normalised token sequence** — pre-order AST
walk with identifiers/literals anonymised (`Name→VAR`, `Constant→CONST`, `arg→ARG`; nested
def/class/lambda collapsed to `NESTED`) so a renamed copy has an *identical* sequence — plus a
node-type histogram (cosine backstop) and a callee count (to mark functions experiment 01 is
structurally blind to). Clones = identical normalised sequence (exact) or `SequenceMatcher ≥ 0.85`
(near).

## The headline: it found real duplication the call-graph missed

**`dataloop._tarjan` and `reach.strongly_connected_components` carry a byte-identical Tarjan SCC
core** — 186 identical normalised tokens. Verified by reading both: same `index`/`low`/`on_stack`/
`stack`/`counter`/`out` setup, the same recursion-limit raise (down to the *identical*
`panel QQQ LOW: don't leak a raised limit to the host` comment), and the same ~40-line
`strongconnect` nested function and try/finally driver. Only the adjacency construction and the
output post-filter differ. **This is a genuine `_tarjan_scc(adj)` extract-helper candidate** — and
experiment 01 (call-graph) never surfaced it.

Other **call-fingerprint-BLIND** candidates (both functions <3 callees, so experiment 01 cannot
see them at all):

| Pair | r | nature |
|---|---|---|
| `cli._require_typer` ~ `mcp._require_mcp` | 0.94 | duplicated "lazy-import the optional dep or raise a friendly error" guard across the two surface modules |
| `_assign_rhs_name` ~ `_name_of` (×3 copies, python/events/routes) | 0.97 / exact | the small "pull a name out of an AST/expr node" helper, copied per module |
| `entrypoints._dir_of` ~ `store._file_of` | 0.95 | path-component helper |

And, as in experiment 01, the deliberate forward/reverse twins reappear (`fan_in`/`fan_out`,
`get_callers`/`get_callees`, `callers_of`/`callees_of`, `reachable_from`/`reverse_reachable_from`,
the `_direct_*` AST-walker family) — correctly flagged, correctly left alone.

## Why this is the better signal (vs experiment 01)

The call-graph fingerprint compares functions by *who they call*, so it is blind to:
- functions with **few or no callees** (25% of the corpus here) — the SCC helpers, the import
  guards, the name-extractors all live here;
- **what the body actually does** — two functions calling the same helpers can have wildly
  different control/data flow, and two functions doing the same thing can call nothing in common.

The body matrix sees control + data *shape*, so it recovers exactly those — demonstrated cleanly by
the fixture (`fixtures/clones.py`): `sum_even_squares` ~ `accumulate_even` are an exact body clone
(renamed locals, zero callees) flagged **call-fingerprint BLIND**, while the unrelated `split_csv`
scores far below threshold.

## Verdict

**Matrixifying function contents materially improves the redundancy signal.** Where experiment 01
returned a confident *negative* on this repo, the body matrix found a real, mergeable
cross-module duplication (Tarjan SCC) plus several smaller copied helpers. The user's intuition —
that the *implementation* matrix, not just the call matrix, is where redundancy lives — is borne
out.

This is also the representation that would sharpen **Q2** (verify a translation preserved
control/data shape, not just the call list) and **Q3** (planned-vs-actual at statement granularity).

## Honest limits

- **Structural, not semantic.** A normalised-AST match is a Type-1/Type-2 clone detector; it will
  miss algorithmically-equivalent code written with different control structure (Type-4), and can
  over-match boilerplate-shaped bodies. Every hit stays advisory.
- **Python-only.** Done on `ast` for soundness; a 12-language version needs per-language CFG/DFG,
  which is the deferred "variable-granularity data flow" roadmap item (and must clear the cardinal
  bar before it could ever feed `find_stale`).
- **Order/nesting partially lost.** The histogram is a bag; the token sequence keeps order but is
  edit-distance sensitive. A true PDG (control + data edges) would be the next refinement.

## Actionable follow-up (separate, gated)

The Tarjan duplication is a legitimate tidy-up: extract a shared `_tarjan_scc(adj) -> list[list]`
and have both call sites build their adjacency + post-filter around it. It touches graph-algorithm
code (not the cardinal dead-code path), but it is still production code — so it would go through the
full gate (ruff/mypy + pytest + oracles + mutation) and a two-round panel, as its own change. Left
for the maintainer to green-light.
