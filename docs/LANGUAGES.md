# Languages & frameworks supported

stitchgraph is **Python-first** (the language it fully analyses), with
**cross-language detection** at the boundaries a Python web/data app touches:
HTML templates, SQL, ORMs, and web routes. Broader first-class languages
(JS/TS, Java, …) are the documented next step, behind the same extractor contract.

## Fully analysed

| Language | How | What's extracted |
|---|---|---|
| **Python** (3.11+) | stdlib `ast` (+ optional `jedi` for `--precise`) | Modules, packages, classes, functions, methods, tests; `CALLS`, `IMPORTS`, `INHERITS`, `REFERENCES`, `READS`/`WRITES` (global state) edges. Scope-aware resolution (`self.`, locally-typed receivers, decorators, `with`, properties). Stubs (`NotImplementedError`/`...`), arity, docstrings. |

## Detected at the cross-language boundary

These are resolver plugins (`core/resolve/`) — pattern-based, so results are
`INFERRED` with a confidence < 1, and you enable only the stacks you use.

| Target | Detected | Produces |
|---|---|---|
| **Web routes** | Flask / FastAPI / `APIRouter` / blueprint decorators — `@app.route/get/post/put/delete/patch(...)` | `Route` nodes, `ROUTES_TO` → handler (routes are entry points) |
| **HTML templates** | `.html` / `.htm` / `.jinja` / `.j2` `<form action="…">` | `Template` nodes, `SUBMITS_TO` → matching route |
| **SQL** | SQL string literals, parsed with **sqlglot** (broad dialect support) | `DBTable` nodes, `QUERIES` + `READS`/`WRITES` |
| **ORM** | **SQLAlchemy** & **Django** model classes (`Column`/`mapped_column`/`*Field`) | `DBTable` + `DBColumn` nodes, `MAPS_TO`; converges with SQL on the same `db::<table>` node |

The payoff is the full-stack trace: an HTML form → route → handler → … → DB
table/column, all in one `trace_path`, with confidence propagated end to end.

## Entry-point detection (Python)

Roots for reachability / dead-code, auto-detected:

- Public API — names in `__all__` and package `__init__` exports (never flagged
  dead for lack of internal callers)
- `[project.scripts]` / `[project.entry-points]` and `if __name__ == "__main__"`
- Tests (pytest collection)
- Web routes (above)
- **Runtime-hit** nodes from an ingested coverage trace
- Anything pinned in `stitchgraph.toml` `[entry_points] include`

## Runtime traces

`coverage.py` JSON (`coverage run -m pytest && coverage json`) — marks nodes that
actually executed, grounding liveness in reality.

## Not yet (planned)

First-class **JS/TS, Java, Go, …** extractors. These need the tree-sitter /
LSP backend (incremental, polyglot, type-grade). The extractor contract is
`path → nodes + edges`, so a new language is an additive plugin — it doesn't
touch the store, algebra, resolvers, or tools.
