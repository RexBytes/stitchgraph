# Synthesis — the matrix-as-oracle research thread (2026-06-29)

Capstone for the post-v2.2.1 research push. Started from three questions about whether
stitchgraph's matrix can do generative-adjacent work; ended with a validated granularity ladder, an
oracle primitive, and one safe, implementable result.

## The thesis (held up)

**The matrix encodes structure, not semantics — so it is an oracle, not a generator.** It never
*writes* code; it *plans* structure and *verifies* structure. The LLM supplies meaning; the matrix
proposes candidates and checks the result has the intended shape. Everything stays advisory and
confidence-carrying, per the cardinal stance. The prior §2 finding (topology tracks the
extractor, not the function, across languages) constrained every cross-language claim to
"located candidate a human triages", never proof.

## The three questions — answers

**Q1 — surface reducible/redundant code?** *Yes, but the level matters.*
- Call-graph clones (`01-structural-redundancy/`): a confident **negative** on this repo — raw
  callee-fingerprint is hub-callee noise; IDF + distinctive-helper filtering leaves zero actionable
  candidates. stitchgraph's call structure is already well-factored.
- Body-level clones (`02`, `03`, `04`): found a **real** cross-module duplication — a byte-identical
  Tarjan SCC core in `dataloop._tarjan` and `reach.strongly_connected_components` — that the call
  graph is structurally blind to. Triple-confirmed (normalised-AST, statement-PDG, expr-DFG).

**Q2 — translate a codebase?** *Reframed: scaffold + verifier, not translator.*
The graph-diff oracle in leaf mode is a high-recall fidelity check: a faithful Python↔JS twin of a
recursive-descent calculator matched at **100%** node/edge recall (after normalising the lone
`__init__`/`constructor` convention). For a *restructured* translation it instead *locates* the real
delta. Cross-language is oracle-only (extractor asymmetry is a confound); same-language is exact.

**Q3 — is matrix-first development faster for an LLM?** *Reframed: plan + verify spine — and this is
where the body matrix pays off.*
`graphdiff/structure_diff.py` diffs a *plan* against an *actual build* at body granularity. Demo: a
buggy build with an **identical call graph** (same functions, same calls) but a data-flow bug — the
call-level oracle says "EQUIVALENT", the body-aware oracle locates `score()` (similarity 0.63),
because a parameter no longer flows where it should. The gap that matters is usually *inside* a
function; only the value-flow matrix can check it.

## The granularity ladder (the core technical result)

| Level | What it sees | Verdict |
|---|---|---|
| call graph (shipped) | defs ↔ defs | already well-factored here; blind to body redundancy & body bugs |
| normalised-AST body | syntax shape | found the Tarjan dup; over-rates unrelated bodies (histogram) |
| statement-PDG | control + data deps | order-invariant; **loses** on temp-var refactors; cross-validates the dup |
| **expr-DFG (copy-propagated)** | value flow between operations | **only level that gets all 3 fixtures right**; the Q3 verifier |

The arc: each level down folds away a surface accident (call set → statement order → temp variables)
until the fingerprint tracks what the code *does*, not how it's spelled.

## What we can implement in the real codebase

**Now (safe, validated, this PR):**
- **Extract `tarjan_scc` shared helper** (`docs/IDEAS.md §5a`). The one concrete redundancy the
  research found and triple-confirmed. Graph-algorithm code, not the cardinal dead-code path;
  behaviour-preserving; goes through the full gate. This is the research→codebase payoff.

**Later (v3.0.0, prototyped here, not yet promotable):**
- **An intra-procedural (body/CFG/DFG/PDG) matrix** as a real representation (`docs/IDEAS.md §5b`),
  powering `find_similar(mode="structure")`, a body-aware `graph_diff`, and the deferred
  variable-granularity data flow. Gated on: scale (vs the streaming indexer), **cardinal soundness**
  (the prototypes are approximations — no SSA/φ/alias analysis — and must never under-root before
  they could touch `find_stale`), and 12-language breadth (Python-first today).

**Promotion checklist for the graph-diff oracle** lives in `graphdiff/FINDINGS.md`.

## Honesty ledger (what did *not* work, kept on the record)
- Q1 call-graph redundancy: nothing actionable on this repo (reported as a negative, not buried).
- stmt-PDG: lost the temp-var clone to AST-token (0.40 vs 0.79) — reported, then fixed at expr level.
- expr-DFG: still an approximation; constants collapsed, so Type-4 (algorithmically-equivalent,
  differently-structured) clones escape. Cross-language body diff not possible (Python-only).
