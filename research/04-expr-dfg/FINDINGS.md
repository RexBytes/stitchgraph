# Findings — Experiment 04: expression-level value-flow graph (copy propagation)

**Question:** experiment 03's statement-PDG *lost* the temp-variable clone case. Does going one
level finer — value flow between *operations*, with variable copies folded away — fix it without
breaking what worked?

**Date:** 2026-06-29  ·  **Fixtures:** `../03-pdg/fixtures/pdg_clones.py`

## Method

Symbolically evaluate the function body, threading an `env: var → producing-node` map. A `Name`
load returns whatever node currently produces that variable, so a pure copy (`flag = r.active`)
*disappears* — `flag` simply points at the `ATTR(active)` node. Nodes are operations (`ATTR`,
`CALL`, `BINOP`, `CMP`, `SEQ`, `COMPR`, …) and control points (`LOOP`, `BRANCH`, `RETURN`); edges
are value flow (`d`) and control (`c`). Same Weisfeiler-Lehman kernel fingerprint as experiment 03,
so the three levels compare head-to-head.

## Result — expr-DFG is the only level that gets all three right

| pair | AST-token (exp 02) | stmt-PDG (exp 03) | **expr-DFG (exp 04)** | truth |
|---|---|---|---|---|
| `collect_direct` ~ `collect_tmp` (temp-var clone) | 0.98 | **0.40** ✗ | **1.00** ✓ | clone |
| `interleave_a` ~ `interleave_b` (reordered clone) | 1.00 | 1.00 | **1.00** ✓ | clone |
| `collect_direct` ~ `scale_list` (unrelated) | **0.96** ✗ | 0.28 | **0.26** ✓ | not a clone |

(AST-token here is histogram-cosine, for apples-to-apples with the WL cosine.)

- **AST-token** over-rates the *unrelated* pair (0.96) — a bag of node kinds can't tell a
  collect-loop from a scale-loop.
- **stmt-PDG** misses the *temp-var* clone (0.40) — copies inflate the statement graph.
- **expr-DFG** recovers the temp-var clone (1.00), keeps the reordered clone (1.00), **and** rates
  the unrelated pair low (0.26). Copy propagation was the missing ingredient.

## Verdict

Expression-level value flow is the strongest of the three body representations for clone/similarity:
it folds away the surface accidents (statement order, temp variables, naming) that fooled the
coarser levels, and it is what a real Type-3/Type-4-tolerant detector would be built on. It is also
the right input to the body-aware graph diff (see `../graphdiff/structure_diff.py` and the Q3
result there).

## Honest limits

- **Still an approximation, not full SSA.** No φ-nodes at branch joins, no fixpoint over loop
  back-edges, no alias analysis — so it can over-merge (two different values reaching a use look the
  same) and is unsound for anything that would feed `find_stale`. Fine for advisory similarity.
- **Constants/literals collapsed** to bare `CONST` leaves — value-equality (`x*x` vs `x**2`) is not
  modelled; this is structural similarity, not semantic equivalence (Type-4 still escapes).
- **Python-only** — the deep `ast`. A 12-language version is the v3.0.0 surface (per `docs/IDEAS.md`
  §5b), with the cardinal-soundness bar applying before it could ever inform liveness.
