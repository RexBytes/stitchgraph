# Languages & frameworks

stitchgraph extracts a unified graph across languages. **Python** is the deepest
(stdlib `ast` with scope/type-aware resolution); **JS/TS, Rust, C/C++, C#, Go,
Java, Ruby, PHP, Bash** are extracted via **tree-sitter** (definitions + call
graph); and a set of **cross-language resolvers** stitch the boundaries (routes,
HTML forms, SQL, ORM) so a full-stack trace works end to end.

Adding a language is a small `LangSpec` (node types → kinds, the call node +
callee field): name-based call graphs are cheap. The genuinely hard part is
*type-correct* resolution (which `save` does `x.save()` mean?), not parsing —
and since v3.46.0 the **LSP backend** (`reindex --lsp`, research/24) closes it:
an installed language server (rust-analyzer, typescript-language-server, gopls,
clangd; `[lsp.servers]` adds others) answers go-to-definition per call site and
the true target gains a confident EXTRACTED edge, while the name-based arms stay
as the honest fallback. `type_at` exposes hover-grade type info on demand.

## Language progress table

Legend: ✅ full · 🟡 partial · ⬜ not yet · — n/a

| Language | Backend | Defs (fn/class/method) | Call graph | Imports | Inheritance | Entry points | Dead code / orient / trace |
|---|---|---|---|---|---|---|---|
| **Python** | stdlib `ast` (+ `jedi`) | ✅ | ✅ scope-aware | ✅ | ✅ | ✅ (API/main/scripts/tests/routes/runtime) | ✅ |
| **JavaScript** | tree-sitter | ✅ (+ arrow consts) | ✅ | ✅ | ✅ extends | 🟡 `export`, `main`, tests | ✅ |
| **TypeScript / TSX** | tree-sitter | ✅ | ✅ | ✅ | ✅ | 🟡 `export`, `main`, tests | ✅ |
| **Rust** | tree-sitter | ✅ (fn/struct/enum/trait/impl) | ✅ | ✅ `use` | ✅ `impl Trait for Type` | 🟡 `pub`, `main` | ✅ |
| **C** | tree-sitter | ✅ (fn/struct) | ✅ | ✅ `#include "…"` | — | 🟡 `main` | ✅ |
| **C++** | tree-sitter | ✅ (fn/class/struct/methods) | ✅ | ✅ `#include "…"` | ✅ base clause | 🟡 `main` | ✅ |
| **C#** | tree-sitter | ✅ (class/struct/iface/methods) | ✅ | ✅ `using` | ✅ base list | 🟡 `public`/`Main` | ✅ |
| **Go** | tree-sitter | ✅ (func/method/type) | ✅ | ✅ | — | 🟡 capitalised/`main`/`Test*` | ✅ |
| **Java** | tree-sitter | ✅ (class/iface/enum/methods) | ✅ | ✅ | ✅ extends/implements | 🟡 `public`/`main`/tests | ✅ |
| **Ruby** | tree-sitter | ✅ (class/module/methods) | ✅ | ✅ `require`(`_relative`) | ✅ superclass | ⬜ | ✅ (with config roots) |
| **PHP** | tree-sitter | ✅ (class/trait/methods) | ✅ | ✅ `use` | ✅ extends/implements | 🟡 `public` | ✅ |
| **Bash / Shell** | tree-sitter | ✅ (functions) | ✅ | ✅ `source`/`.` | — | ⬜ | ✅ (with config roots) |
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

## Installing the grammars (offline by default)

`pip install 'stitchgraph[treesitter]'` is **self-contained and works offline**: it
pins the bundled-grammar line of `tree-sitter-language-pack` (`>=0.7,<1.0`), whose
wheels ship the compiled parsers. No network is touched at runtime. (The `1.x` line
dropped bundling for a download-from-GitHub-on-first-use model that breaks offline /
CI / air-gapped installs — issue #12.)

- **Want the newest grammars instead?** `pip install 'stitchgraph[treesitter-download]'`
  installs the `1.x` line — a smaller wheel that fetches parsers over the network on
  first use (and caches them). stitchgraph's loader uses whichever pack is installed,
  and on the download line it will fetch a missing grammar at runtime when it can.
- **Check your install:** `stitchgraph doctor` reports the pack version, whether it's
  the bundled or download model, the cache dir (download model), and which of the
  supported grammars load. `stitchgraph doctor --strict` exits non-zero if any can't —
  useful as a CI gate that the polyglot graph will actually be complete.
- If a grammar still can't be loaded (e.g. download line, offline), those files are
  **skipped with a warning**, not silently dropped (issue #7); Python is unaffected.

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
