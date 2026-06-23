# stitchgraph

Local-first, MCP-native code intelligence. Point it at a codebase to find
**stale code, implementation holes, orientation, and impact** — ranked by what's
actually live, every answer carrying a confidence and a reason to double-check.

One core library, three thin surfaces over the same operations:

- **Library API** — `import stitchgraph`
- **CLI** — `stitchgraph find-symbol ...` (`pip install 'stitchgraph[cli]'`)
- **MCP server** — for LLM agents (`pip install 'stitchgraph[mcp]'`)

> **Read-only on your code.** stitchgraph only ever writes to its own index DB.
> Every result is advisory — it returns ranked options for a human/LLM to act
> on; it never edits or deletes source.

See [`docs/design.md`](docs/design.md) for the full design & capability map,
[`docs/STATUS.md`](docs/STATUS.md) for what's built, and [`AGENTS.md`](AGENTS.md)
for the agent rules that teach an LLM when to call which tool.

## Status

M0 working end-to-end on Python projects (dogfoods on its own source):

- **Python extractor** (stdlib `ast`) → nodes (Module/Class/Function/Method/Test)
  and edges (CALLS/IMPORTS/INHERITS), with precision-biased resolution and
  entry-point role tagging. tree-sitter + an LSP are the documented upgrade for
  incremental/polyglot/live-types.
- **SQLite adjacency store** (source of truth) with the `dst_id`/`dst_symbol`
  edge schema and incremental, cross-file-correct updates.
- **Entry-point detector** for the Python library+CLI shape (public API, `main`,
  scripts, tests) with a config override.
- **Universal `Result` envelope** (confidence / provenance / `needs_review` /
  urgency), provenance gating the urgency ceiling.
- **Operations**, all real: `find_symbol`, `get_callers`, `get_callees`,
  `orient`, `find_holes`, `find_stale` (reachability), `impact_of` (reverse
  reachability + tests), `trace_path` (max-× best path), `scan` (ranked issues +
  urgency), `reindex`.
- **All three surfaces** generated from one operation registry (CLI + MCP +
  library), plus a Markdown `report` in urgency tiers.

- **Scope-aware resolution** — `self.method` and locally-typed `var.method`
  resolve precisely (not just by name), with an optional **jedi** precision pass
  (`reindex --precise`) for LSP-grade go-to-definition.
- **Cross-language resolver pipeline** — plugins that enrich the graph after
  extraction: a **web-route resolver** (`@app.get("/x")` → Route → handler), a
  **SQL resolver** (sqlglot: query → table, READS/WRITES), and an **ORM resolver**
  (model → table/column, MAPS_TO). ORM and SQL converge on the same `db::<table>`
  node, so full-stack `trace_path` crosses route → handler → … → table → column.
- **GraphBLAS algebra layer** — whole-graph reachability sweeps and PageRank hub
  ranking via python-graphblas (pure-Python fallback; the two agree by test).

See [`docs/STATUS.md`](docs/STATUS.md) for the full done/to-do table. Next:
HTML-template → route edges, data-loop detection, and runtime-trace fusion.

## Quick look

```python
import stitchgraph as sg

with sg.Store("stitchgraph.db") as store:
    print(sg.find_symbol(store, "UserService"))
    print(sg.find_holes(store))
```

## Develop

```bash
pip install -e '.[dev,cli]'
PYTHONPATH=src python -m pytest -q
```
