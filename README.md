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

Early scaffold (M0 in progress). Working today:

- SQLite adjacency store (source of truth) with the `dst_id`/`dst_symbol` edge
  schema and incremental, cross-file-correct updates.
- The universal `Result` envelope (confidence / provenance / `needs_review` /
  urgency), with provenance gating the urgency ceiling.
- Operations backed by the store: `find_symbol`, `get_callers`, `get_callees`,
  `find_holes` (dangling references), `find_stale` (reachability), `orient`.
- All three surfaces generated from one operation registry (CLI + MCP + library),
  plus a Markdown `report`.

Not yet wired (refuse honestly for now): the Python extractor (tree-sitter +
LSP), the entry-point detector, and the algebra layer (`impact_of`, `trace_path`,
`scan`).

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
