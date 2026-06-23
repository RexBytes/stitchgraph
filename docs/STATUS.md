# stitchgraph — status

Snapshot of what's built vs. planned. See [`design.md`](design.md) for the full
spec and section references.

## Done / To do

| Area | Capability | Status | Notes |
|---|---|---|---|
| **Core** | `Result` envelope (confidence/provenance/needs_review/urgency) | ✅ Done | provenance gates urgency ceiling |
| Core | SQLite adjacency store (`dst_id`/`dst_symbol`) | ✅ Done | source of truth |
| Core | Incremental, cross-file-correct updates (`replace_file`) | ✅ Done | worklist re-resolve + invalidate |
| Core | Stable `path::qualified.name` ids | ✅ Done | survives reformatting |
| Core | Read-only invariant (never mutates source) | ✅ Done | writes only to index |
| **Surfaces** | Library API = operation registry | ✅ Done | the source of truth |
| Surfaces | CLI (Typer, generated from registry) | ✅ Done | `--json`, exit codes |
| Surfaces | MCP server (FastMCP, generated from registry) | ✅ Done | optional dep |
| Surfaces | Markdown report (orientation + issues + risk) | ✅ Done | `stitchgraph report` |
| Surfaces | Agent rule file (adoption) | ✅ Done | AGENTS.md |
| **Extraction** | Python extractor (stdlib `ast`) | ✅ Done | Module/Class/Function/Method/Test |
| Extraction | Scope-aware resolution (`self.`, locally-typed `var.`) | ✅ Done | + decorator refs |
| Extraction | jedi precise resolver (`--precise`) | ✅ Done | optional, in-process LSP-grade |
| Extraction | tree-sitter / external LSP backend | ⬜ To do | incremental, polyglot, live types |
| **Entry points** | Python library+CLI detector | ✅ Done | exported API, main, scripts, tests, routes |
| Entry points | `stitchgraph.toml` override | ✅ Done | include roots, ignore globs, threshold, hub metric |
| **Operations** | `find_symbol` / `get_callers` / `get_callees` | ✅ Done | structural primitives |
| Operations | `orient` (counts + hubs) | ✅ Done | PageRank hubs via GraphBLAS |
| Operations | `find_stale` (dead code) | ✅ Done | honest `needs_review` (name-based) |
| Operations | `find_holes` (dangling refs) | ✅ Done | dual of dead code |
| Operations | `impact_of` (blast radius + tests) | ✅ Done | reverse reachability |
| Operations | `trace_path` (best-confidence path) | ✅ Done | full-stack, (max,×) semiring |
| Operations | `scan` (ranked issues + urgency) | ✅ Done | stubs/holes/cycles/god-objects |
| Operations | `reindex` | ✅ Done | full rebuild; `--precise`; ignore globs |
| Operations | `get_matrix` (bounded submatrix) | ✅ Done | refuses broad scope (no dense dump) |
| **Algebra** | GraphBLAS reachability sweeps | ✅ Done | frontier BFS, pure-Python fallback |
| Algebra | GraphBLAS transitive fan-in (hub ranking) | ✅ Done | boolean closure; orient default |
| Algebra | GraphBLAS PageRank centrality | ✅ Done | alt hub metric via config |
| **Cross-language** | Web-route resolver (decorator → Route → handler) | ✅ Done | Flask/FastAPI patterns |
| Cross-language | HTML template → route (`SUBMITS_TO`) | ✅ Done | `<form action>` → Route |
| Cross-language | SQL resolver (sqlglot → table, READS/WRITES) | ✅ Done | query string literals |
| Cross-language | ORM resolver (model → table/column, MAPS_TO) | ✅ Done | SQLAlchemy/Django; converges with SQL |
| Cross-language | Full-stack trace (form → route → handler → table) | ✅ Done | the "gem", end to end |
| **Data flow** | Data-loop detection (🟡) | ✅ Done | mutable-global feedback; surfaced in `scan` |
| **Risk** | git-history churn × centrality (`risk`) | ✅ Done | hotspots + hidden coupling |
| **Runtime** | runtime-trace fusion (`ingest_trace`) | ✅ Done | coverage.json → live seeds, +confidence |
| **Semantic** | `find_similar` retrieval | ✅ Done | token similarity; embedding model = drop-in |
| **Quality** | precision/recall eval harness | ✅ Done | precision-over-recall stance asserted |
| Quality | dogfood on own source | ✅ Done | genuine dead code found; risk hotspots ranked |

## Test coverage

53 tests (`tests/`): envelope, store + incremental, extractor, operations,
config, `get_matrix`, cross-language resolvers (routes/HTML/SQL/ORM) + full-stack
trace, the GraphBLAS algebra (accelerated sweeps agree with the pure-Python
reference), git-risk fusion, runtime-trace fusion, and a precision/recall eval
harness.

## Known seams (honest)

- `find_stale` is `needs_review` at 0.6 (0.78 with a runtime trace) — resolution
  is name/scope-based, not type-grade. `--precise` (jedi) and a future LSP raise
  this further.
- Framework callbacks that override an *external* base (e.g. an `HTMLParser`
  `handle_starttag`) look uncalled, since the base isn't in the project graph —
  the remaining class of stale false-positive (an LSP resolving the base fixes it).
- Context managers (`with`) and property/attribute reads are now modelled, so
  those usages no longer false-flag as stale.
