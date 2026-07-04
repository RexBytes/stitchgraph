# v3.31.0 — the fast-by-default release

Two things, one philosophy: the last Python-adjacency sweeps move onto the v3.30.0
CSR sidecar, and the install default flips to full power — because every accelerated
path is pinned byte-identical to its pure-Python reference, the fast configuration
costs nothing in trust and there is no reason to hide it behind extras.

## The install flip

`pip install stitchgraph` now brings the whole tool: CLI, MCP server, the 12-language
tree-sitter grammars (bundled/offline line), jedi precision, sqlglot SQL resolution,
numpy (the adjacency sidecar), GraphBLAS and scipy. The lean story is unchanged
underneath — the library core is still stdlib-only with guarded imports:

- `pip install --no-deps stitchgraph` → the stdlib-only core, degrading exactly as
  before (pinned by the core-only CI job, which now installs with `--no-deps`).
- The old extras (`[cli]`, `[mcp]`, `[treesitter]`, …) remain for selective installs
  onto a `--no-deps` base.
- **`--pure`** (both `stitchgraph` and `stitchgraph-mcp`) or `STITCHGRAPH_PURE=1`
  forces the reference paths at runtime with everything installed — identical
  results, for debugging a suspected accelerator or byte-reproducing an old run.
  Scope: pure mode disables only accelerators with identical-result fallbacks
  (sidecar, GraphBLAS); numpy-*required* ops (`find_modes` …) are unaffected.

## Sweeps moved onto the sidecar

- **SCC (scan's cycle pass)**: an iterative int Tarjan over the relation-filtered
  CSR, emitting components in exactly the recursive reference's order — seeds in
  `all_node_ids()` order, neighbours in stored-edge order, members in stack-pop
  order. The full `scan` result is pinned identical with and without the sidecar.
- **Articulation points (`find_chokepoints`)**: the same DFS as the reference over a
  vectorised symmetrised CSR; ascending int order equals the reference's
  `sorted()` string order because sidecar ids are stored sorted.
- **`orient`'s `confident_fan_in` fallback**: one masked-bitcount over the reverse
  CSR instead of the SQL GROUP BY.

## Scan output at scale: the god-object review cap

The 16M-edge field graph produced 11,124 god-object flags, 11,117 of them hedged
(`needs_review`) — individually honest, collectively unusable. `scan` now keeps the
top **500** hedged flags by (confidence desc, node) and reports the suppressed count
in `meta["god_objects_suppressed"]` plus an envelope reason. Confident flags are
never dropped; graphs small enough to read unfiltered are unaffected.

## Measured (16M-edge field graph, warm sidecar)

| | v3.30.0 | v3.31.0 | factor |
|---|---|---|---|
| `find_chokepoints` | 216 s / 3.24 GB | **58.9 s / 2.42 GB** | 3.7× |
| `scan` | 371 s | **307 s** | 1.2× (remaining time is SQL shares + liveness, not adjacency) |
| `scan` output | 11,619 issues | **1,002** (10,617 hedged god-objects suppressed, counted) | — |
| `orient` fan-in fallback | 4–61 s (GROUP BY) | **0.06 s** | — |

(For the arc: `find_chokepoints` was 216 s in v3.29.0 too — its adjacency build was
the cost; `scan` was 625 s in v3.29.0 and MemoryError before that.)

## Tests

- The SCC/articulation/scan equivalence test pins the FULL `scan` result — issue
  order included — equal with and without the sidecar, on a corpus with a mutual-
  recursion cycle and a self-loop.
- The god-object cap test falsifies the cutoff (top-N by confidence, suppression
  count in meta, confident flags immune) against a hand-built provenance-mixed store.
- Pure-mode test: `STITCHGRAPH_PURE=1` refuses sidecar and GraphBLAS, results
  identical, and the switch releases cleanly.
- Full suite green; core-only CI job proves the `--no-deps` lean install.
