# Findings — Experiment 03: PDG (program-dependence-graph) body matrix

**Question:** go one level below experiment 02 — instead of the AST *token sequence*, build the
actual **dependence matrix** of a function (control + data edges) and fingerprint it
order-invariantly. Does it catch clones the token sequence misses? And what does "the matrix of
the implementation code" actually look like?

**Date:** 2026-06-29  ·  **Corpus:** `src/stitchgraph` (Python, stdlib `ast`)

## The body matrix, made concrete

For `collect_direct(data)` the prototype emits this statement-level adjacency matrix — literally
the matrix of the implementation code:

```
         0  1  2  3  4  5
 0 ENTRY   . C D  .  . C
 1 Assign  .  .  .  . D D
 2 For     .  .  . D D  .
 3 If      .  .  .  . C  .
 4 Expr    .  .  .  .  .  .
 5 Return  .  .  .  .  .  .
  (rows=from, cols=to; C=control dep, D=data dep)
```

Read it: ENTRY (the params) control-deps the `For` and data-feeds `result`/`Return`; the `For`
control-deps the `If` and data-feeds the body; `Assign result=[]` data-feeds the `Expr` (append) and
the `Return`. This is a PDG: nodes = statements, edges = *why* one statement depends on another.
The fingerprint is a Weisfeiler-Lehman **kernel** (accumulate node labels across refinement
iterations), which is order- and name-invariant.

## What it gets right — and where it loses (honest)

| Pair | PDG | AST-token (exp 02) | reading |
|---|---|---|---|
| `interleave_a` ~ `interleave_b` (independent blocks, **reordered**) | **1.00** | 0.70 | **PDG wins by construction** — reordering independent statements changes the token *sequence* but not the dependence graph |
| `collect_direct` ~ `collect_tmp` (Type-3, **temp-var factoring**) | 0.40 | **0.79** | **PDG loses** — temp vars add nodes and lengthen def→use chains, so the crude sequential reaching-def over-penalises |
| `collect_direct` ~ `scale_list` (unrelated) | 0.28 | 0.72 | both rate it lowest of its row ✓ |

**The honest conclusion: PDG is complementary to the AST-token fingerprint, not a strict upgrade.**
It is the right tool for *reordering* and is the only one of the three levels that models data flow —
but a naive PDG is *worse* at temp-variable refactors, and a real system would combine both signals
(or use a proper reaching-def with copy-propagation to fold temp vars away).

## Corpus: precision lesson + independent corroboration

At a loose threshold the PDG over-matched: 30 "clones", dominated by tiny `for x: out.append(...)`
bodies that all share a generic shape at coarse WL depth — **the Q1 hub-callee noise, reincarnated
at the dependence level.** Requiring ≥10 statements and cosine ≥ 0.95 collapses it to **6**, and the
survivors are real:

```
  1.00 [20 stmts]  dataloop.py::strongconnect  ~  reach.py::strongconnect      <- the Tarjan dup
  1.00 [13 stmts]  python.py::descendants      ~  store.py::descendants
  1.00 [11 stmts]  python.py::_direct_names     ~  python.py::_direct_calls    (intentional family)
  1.00 [11 stmts]  python.py::_direct_attr_reads ~ python.py::_direct_names
  1.00 [10 stmts]  python.py::add_enclosing    ~  treesitter.py::add_enclosing
```

The headline is **cross-validation**: the Tarjan SCC duplication that experiment 02 found by
*normalised AST* is independently re-found here by *dependence structure* — top of the list, the
largest match. Two different body-matrix methods agreeing on the same real duplication is much
stronger evidence than either alone. It also surfaces two more cross-module dups
(`descendants`, `add_enclosing`) worth a look.

## Verdict

1. **The dependence matrix is the right substrate for the v3.0.0 "higher granularity" feature.** It
   models control + data flow (not just call edges), it is order-invariant, and it cross-validates
   the redundancy findings. This is the representation that would also carry the deferred
   variable-granularity data-flow work.
2. **It is not a free win for clone detection.** Like the call graph (needed IDF) it needs precision
   work (min-size, rare-structure weighting, temp-var folding) before it beats the simpler AST-token
   fingerprint across the board. No metric inflation: PDG lost one of the three fixture pairs, and
   that's reported.
3. **Same cardinal caveat as everything in `research/`:** advisory only. A dependence matrix that
   ever fed `find_stale` would have to be sound enough never to under-root — a high bar that gates
   the whole v3.0.0 direction.

## Limits of this prototype (be specific)

- **Sequential reaching-def, not a real fixpoint** — ignores branch joins and loop back-edges; a
  production DFG needs proper reaching-definitions. This is why temp-var folding fails.
- **Statement granularity** — expression-level data flow (which *sub-expression* feeds which) is not
  modelled; a true PDG/SSA would go finer.
- **Python-only** — same reason as exp 02 (deep `ast`); 12-language CFG/DFG is the large deferred
  surface.
