# stitchgraph v2.0.0 — constant-memory streaming indexer

The major feature of v2: **`reindex` can stream the graph straight to SQLite instead of
building it all in Python first**, so peak memory tracks one file's working set — *not* the
size of the whole repo. Tens-of-thousands-of-file monorepos (Magento, 24k PHP files) that used
to need >12 GB and OOM now index on a laptop. The streamed index is **byte-identical** to the
in-memory one, pinned by a differential oracle across Python + JS/TS + Go + Ruby + C/C++ +
Rust + PHP.

Measured on the Magento `Framework` core (4,304 PHP files → 30,412 nodes, ~15.5M raw edges):

| | peak RSS | output |
|---|---|---|
| in-memory (`streaming=False`) | **3,183 MB** | 30,412 nodes / 3,926,345 edges |
| streaming (`streaming=True`)  | **269 MB** | 30,412 nodes / 3,926,345 edges |

**≈12× less memory, byte-identical output** (verified row-for-row — including `weight`,
`provenance`, and the internal `name_based` flag), ~40% slower.

## Highlights

- **Monorepo-scale indexing on modest hardware** — the former top limitation ("very large
  monorepos are indexed as one in-memory graph") is resolved. Peak memory is bounded by symbol
  count + one file's working set, not by the millions of edges a big polyglot repo produces.
- **Automatic — you don't have to think about it.** `reindex` now decides for you: it streams
  large on-disk repos and keeps the slightly faster in-memory path for small ones. Force it
  either way with `streaming=True` / `streaming=False` (CLI: `--streaming` / `--no-streaming`;
  also on the MCP tool).
- **Identical results, guaranteed.** Streaming changes *how* the graph is built, never *what*
  it contains. A differential oracle pins `streaming == full` byte-for-byte on a polyglot
  corpus plus heavy-fan-out / cross-group stress fixtures, so the low-memory path can never
  silently diverge.

## How to use it

```python
import stitchgraph as sg

with sg.Store("stitchgraph.db") as store:      # an on-disk DB realises the memory win
    sg.reindex(store, "/path/to/huge/monorepo")   # AUTO: streams when the tree is large
    print(sg.find_stale(store))
```

`streaming` is tri-state:

- `None` (default) — **AUTO**: stream when the store is on-disk **and** the tree is large
  (≥ 2,000 indexable source files). Small repos use the faster in-memory path.
- `True` / `False` — force streaming / in-memory.

> Streaming saves memory only with an **on-disk** `Store` — a `:memory:` database holds the
> rows in RAM regardless — so AUTO never picks it for `:memory:`.

## How it works

- **Parse trees + source are dropped after pass 1.** Each file's Python AST / tree-sitter
  parse tree *and* its source bytes are freed once definitions are collected; only a tiny
  per-definition record survives into the edge-resolution pass. Neither all the trees nor all
  the source are ever resident at once.
- **Edges stream to SQLite, deduplicated per source on the fly.** The dominant cost on a big
  repo is the edge set — name-based ambiguous fan-out yields ~15.5M edges on a single Magento
  module. Because every dedup key is scoped to an edge's source, each source's fan-out is
  collapsed in memory the moment it's complete, and only the survivors are written (in
  committed batches). The raw millions never materialise in Python or on disk. A final
  global dedup pass in the store reconciles the rare cross-group / resolver overlap.

Full design: [`docs/V2_STREAMING_DESIGN.md`](V2_STREAMING_DESIGN.md).

## Notes & trade-offs

- Streaming reindex commits in batches rather than as one transaction, so a crash mid-rebuild
  can leave a partial index; a re-run rebuilds cleanly (it clears first). The default
  in-memory path remains crash-atomic. AUTO only engages streaming for large on-disk repos —
  exactly where the in-memory alternative is an out-of-memory failure.
- No public API break: `extract_project` / `treesitter.extract` still return `(nodes, edges)`;
  the streaming machinery is internal. The major version reflects the new default behaviour
  (AUTO streaming) and the scale milestone.

## Compatibility

- Existing indexes and the on-disk schema are unchanged; no migration needed.
- All v1 operations, the CLI, and the MCP server behave exactly as before — only `reindex`'s
  memory profile (and its new `streaming` knob) changed.

## Quality gate

Shipped under stitchgraph's standard three-layer release gate: the full test suite, the
differential **oracle** suite (incremental == full, **streaming == full**, GraphBLAS ==
pure-Python), and the **mutation** meta-oracle, plus multi-model adversarial review **panels**
(opus + sonnet + haiku) driven to convergence. ruff + mypy clean. The streaming path is
specifically pinned by the streaming differential oracle (now comparing every load-bearing
edge field, `name_based` included) and a mutation run over its correctness core.
