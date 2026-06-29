# stitchgraph v3.0.0 — the intra-procedural body matrix (Python)

stitchgraph has always modelled code *between* definitions: a graph of functions/classes/modules
linked by CALLS / REFERENCES / INHERITS / IMPORTS. **v3.0.0 adds the level below that** — a matrix
of what happens *inside* a function — and two advisory features built on it.

A new representation earns the MAJOR bump, but it is **backward-compatible**: every existing
operation, the schema, and on-disk indexes are unchanged. The new capabilities are opt-in.

## Why

The matrix-as-oracle research (in `research/`, not shipped) showed the call graph is blind to a
whole class of signal: two functions can call the same helpers yet do completely different things,
and two functions can do the same thing while calling nothing in common. The redundancy and
fidelity signal lives *inside* the function. That research independently re-found — and drove — the
v2.3.0 `tarjan_scc` de-duplication; v3.0.0 turns the approach into real features.

## Added

### `core/structure.py` — a Python function-body fingerprint
A per-function **value-flow graph** (operations + control points; data + control edges) built from
the body AST with **copy propagation**, fingerprinted **order- and name-invariantly** via a
Weisfeiler-Lehman kernel. Consequence: renamed locals, reordered independent statements, and
temp-variable factoring all fingerprint as the *same shape*.

### `find_similar(mode="structure")`
Rank stored Python functions by **body shape** instead of name/docstring/callees. Finds
renamed/reordered/temp-var clones (Type-2/Type-3) a token differ misses. The default
`mode="semantic"` is unchanged.

### `graph_diff` — a new operation
Structurally diff this index against **another built index** (a stitchgraph `.db` path):

- **call-level deltas** — located node/edge differences. `mode="id"` is exact (same codebase: did a
  refactor change the graph? does the actual match the plan?); `mode="leaf"` reduces names to their
  tail so two *different* codebases (e.g. a translation) can be compared (advisory — cross-language
  topology tracks the extractor).
- **body-level changes** — for Python functions present in both, those whose *body shape* diverged.
  The headline: a data-flow bug that leaves the **call graph identical** is invisible to the
  call-level diff but caught here.

Exposed on the library API (`sg.graph_diff`), the CLI (`graph-diff OTHER_DB --mode / --body /
--body-threshold`), and the MCP tool schema.

## Cardinal-safety & scope

All three features are **advisory and read-only** — they never feed `find_stale` / liveness rooting,
so the cardinal rule (*live code is never confidently flagged dead*) is structurally unaffected by
this release. They are **Python-only** (built on the deep stdlib `ast`); extending the body matrix to
the other languages is future work (`docs/IDEAS.md` §5b, with a layered/Code-Property-Graph design
in §5c). The fingerprint is a structural approximation, **not** sound data flow — copy propagation
but no SSA φ-nodes, loop fixpoint, or alias analysis, and constants are collapsed (so
algorithmically-equivalent-but-differently-structured code can still escape). Limits are documented
in the module and `research/04-expr-dfg/FINDINGS.md`.

## Quality gate

- ruff + mypy clean; full suite **698** passing.
- Differential oracle suite **85**, including a new **graph_diff dogfood oracle**: stitchgraph's own
  source, indexed twice, self-diffs to *equivalent* (id and leaf, no body changes) — a real-code
  determinism guard.
- Mutation meta-oracle: the `structure.py` core 15/15 (kill-signal `pytest tests/test_structure.py
  tests/oracles/test_structure_completeness.py`) and the `graphdiff` core 9/9 (kill-signal `pytest
  tests/test_graph_diff.py`) mutants killed by their own unit suites.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean.

## Upgrading

Nothing to do — indexes don't need rebuilding and existing calls are unchanged. To try the new
features:

```python
import stitchgraph as sg
with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")
    print(sg.find_similar(store, open("some_func.py").read(), mode="structure"))
    print(sg.graph_diff(store, "other_index.db", mode="id"))   # body-aware by default
```
