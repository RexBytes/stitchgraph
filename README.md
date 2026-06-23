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

See [`docs/design.md`](docs/design.md) for the full design & capability map.

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

Next: the cross-language resolver (M3), GraphBLAS for whole-graph sweeps at
scale (M2 algebra), and an LSP for live types (raising `find_stale` confidence
above today's name-based resolution).

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
