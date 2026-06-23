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

## Languages

- **Deepest:** Python 3.11+ (stdlib `ast`; optional `jedi` for `--precise`).
- **Extracted via tree-sitter** (definitions + call graph → dead-code, orient,
  impact, trace): **JavaScript, TypeScript/TSX, Rust, C, C++, C#, Go, Java, Ruby, PHP, Bash**.
- **Detected at the cross-language boundary:** web routes (Flask/FastAPI,
  Django, Express, Spring), HTML `<form action>`, JS `fetch`, events (emit/on),
  SQL (sqlglot), ORM (SQLAlchemy/Django) — powering the full-stack `trace_path`.

Full support matrix in [`docs/LANGUAGES.md`](docs/LANGUAGES.md).

## Status (v0.3.0)

Working end-to-end and dogfooding on its own source. See
[`docs/STATUS.md`](docs/STATUS.md) for the full table + roadmap.

- **Polyglot extraction** — Python (deep, stdlib `ast` + optional `jedi`) and 11
  more languages via tree-sitter, in one graph: definitions, call graph, imports,
  inheritance, and per-language test entry points. Precision-biased, per-language
  resolution (a JS call never binds to a Rust function).
- **SQLite adjacency store** (source of truth) with cross-file-correct
  incremental updates and forward-compatible schema migration.
- **Universal `Result` envelope** — `confidence / provenance / needs_review /
  urgency`; provenance gates the urgency ceiling.
- **14 operations**, all real: `find_symbol`, `get_callers`, `get_callees`,
  `orient`, `find_stale`, `find_holes`, `impact_of`, `trace_path`, `scan`,
  `get_matrix`, `summarize_subsystem`, `risk`, `ingest_trace`, `find_similar`,
  plus admin `reindex`. Generated as **library API + CLI + MCP**, plus a Markdown
  `report` and a `watch` command.
- **Cross-language resolver pipeline** — routes (Flask/FastAPI/Django/Express/
  Spring), HTML forms, JS `fetch`, events, SQL, and ORM; ORM and SQL converge on
  the same `db::<table>` node, so `trace_path` crosses HTML/JS → route → handler →
  … → table → column.
- **GraphBLAS algebra** — whole-graph reachability, transitive fan-in, and
  PageRank (pure-Python fallback; the two agree by test).
- **Risk** (git churn × centrality + hidden coupling), **runtime fusion**
  (coverage.py JSON / LCOV / Go coverprofile), **semantic** `find_similar`
  (token default; pluggable dense embedder), **data-loop** detection.

**Biggest deferred items:** an LSP backend (type-grade resolution) and
variable-granularity data flow — see the [roadmap](docs/STATUS.md#roadmap-whats-left).

## Quick look

```python
import stitchgraph as sg

with sg.Store("stitchgraph.db") as store:
    print(sg.find_symbol(store, "UserService"))
    print(sg.find_holes(store))
```

## Develop

```bash
pip install -e '.[all,dev]'   # all extras so the full suite runs
PYTHONPATH=src python -m pytest -q
```

CI runs the suite on Python 3.11 and 3.12 (see `.github/workflows/ci.yml`).
Tests that need an optional dependency skip gracefully when it's absent.
