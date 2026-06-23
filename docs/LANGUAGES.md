# Languages & frameworks

stitchgraph extracts a unified graph across languages. **Python** is the deepest
(stdlib `ast` with scope/type-aware resolution); **JS/TS, Rust, C/C++, C#, Go,
Java, Ruby, PHP, Bash** are extracted via **tree-sitter** (definitions + call
graph); and a set of **cross-language resolvers** stitch the boundaries (routes,
HTML forms, SQL, ORM) so a full-stack trace works end to end.

Adding a language is a small `LangSpec` (node types → kinds, the call node +
callee field): name-based call graphs are cheap. The genuinely hard part — and
the reason an LSP is the deferred upgrade — is *type-correct* resolution
(which `save` does `x.save()` mean?), not parsing.

## Language progress table

Legend: ✅ full · 🟡 partial · ⬜ not yet · — n/a

| Language | Backend | Defs (fn/class/method) | Call graph | Imports | Inheritance | Entry points | Dead code / orient / trace |
|---|---|---|---|---|---|---|---|
| **Python** | stdlib `ast` (+ `jedi`) | ✅ | ✅ scope-aware | ✅ | ✅ | ✅ (API/main/scripts/tests/routes/runtime) | ✅ |
| **JavaScript** | tree-sitter | ✅ (+ arrow consts) | ✅ | 🟡 | 🟡 extends | 🟡 `export`, `main` | ✅ |
| **TypeScript / TSX** | tree-sitter | ✅ | ✅ | 🟡 | 🟡 | 🟡 `export`, `main` | ✅ |
| **Rust** | tree-sitter | ✅ (fn/struct/enum/trait/impl) | ✅ | ⬜ | ⬜ | 🟡 `pub`, `main` | ✅ |
| **C** | tree-sitter | ✅ (fn/struct) | ✅ | ⬜ | — | 🟡 `main` | ✅ |
| **C++** | tree-sitter | ✅ (fn/class/struct/methods) | ✅ | ⬜ | ⬜ | 🟡 `main` | ✅ |
| **C#** | tree-sitter | ✅ (class/struct/iface/methods) | ✅ | ⬜ | ⬜ | 🟡 `public`/`Main` | ✅ |
| **Go** | tree-sitter | ✅ (func/method/type) | ✅ | ⬜ | — | 🟡 capitalised/`main` | ✅ |
| **Java** | tree-sitter | ✅ (class/iface/enum/methods) | ✅ | ⬜ | ⬜ | 🟡 `public`/`main` | ✅ |
| **Ruby** | tree-sitter | ✅ (class/module/methods) | ✅ | ⬜ | ⬜ | ⬜ | ✅ (with config roots) |
| **PHP** | tree-sitter | ✅ (class/trait/methods) | ✅ | ⬜ | ⬜ | 🟡 `public` | ✅ |
| **Bash / Shell** | tree-sitter | ✅ (functions) | ✅ | ⬜ | — | ⬜ | ✅ (with config roots) |
| **HTML** | resolver | — | — | — | — | — | detected: `<form action>` → route |
| **SQL** | resolver (sqlglot) | — | — | — | — | — | detected: query → table, READS/WRITES |

Notes:
- **Call resolution is per-language** — a JS call never binds to a Rust function
  of the same name. It's precision-biased: one match → confident, several →
  AMBIGUOUS to all candidates, unknown → dropped as external.
- **Entry points** drive dead-code accuracy. Python's are auto-detected richly;
  other languages currently seed from `export`/`pub`/`main` plus anything pinned
  in `stitchgraph.toml` `[entry_points] include`. Without roots, `find_stale`
  honestly refuses rather than guessing.
- **🟡 Imports/inheritance** for the tree-sitter languages: calls already resolve
  cross-file by name, so dead-code/orient/trace work without modelling imports;
  richer import/inheritance edges are incremental additions per `LangSpec`.

## Cross-language resolvers (the full-stack "gem")

Pattern detectors that add edges spanning languages — enable only what you use:

| Target | Detected | Produces |
|---|---|---|
| **Web routes** | Flask / FastAPI / `APIRouter` / blueprint decorators | `Route` → `ROUTES_TO` → handler (routes are entry points) |
| **HTML templates** | `.html`/`.htm`/`.jinja`/`.j2` `<form action>` | `Template` → `SUBMITS_TO` → route |
| **SQL** | SQL string literals (sqlglot) | `DBTable` + `QUERIES`/`READS`/`WRITES` |
| **ORM** | SQLAlchemy / Django models | `DBTable` + `DBColumn` + `MAPS_TO` (converges with SQL on `db::<table>`) |

Result: `trace_path` crosses HTML form → route → handler → … → DB table/column.

## Runtime

`coverage.py` JSON (`coverage run -m pytest && coverage json`) → `ingest_trace`
marks executed nodes as live and raises dead-code confidence. (Python today;
other languages need their own coverage format mapped.)

## Adding a language

A new tree-sitter language is a small `LangSpec` in
`core/extract/treesitter.py` (node types → kinds, the call node + callee field).
The store, algebra, resolvers, and the 14 tools are untouched — the extractor
contract is just `(root, ignore) → (nodes, edges)`.
