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
| Surfaces | Markdown report (urgency tiers) | ✅ Done | `report.py` |
| **Extraction** | Python extractor (stdlib `ast`) | ✅ Done | Module/Class/Function/Method/Test |
| Extraction | Scope-aware resolution (`self.`, locally-typed `var.`) | ✅ Done | + decorator refs |
| Extraction | jedi precise resolver (`--precise`) | ✅ Done | optional, in-process LSP-grade |
| Extraction | tree-sitter / external LSP backend | ⬜ To do | incremental, polyglot, live types |
| **Entry points** | Python library+CLI detector | ✅ Done | exported API, main, scripts, tests |
| Entry points | `stitchgraph.toml` override | ⬜ To do | config escape hatch (spec'd) |
| **Operations** | `find_symbol` / `get_callers` / `get_callees` | ✅ Done | structural primitives |
| Operations | `orient` (counts + hubs) | ✅ Done | PageRank hubs via GraphBLAS |
| Operations | `find_stale` (dead code) | ✅ Done | honest `needs_review` (name-based) |
| Operations | `find_holes` (dangling refs) | ✅ Done | dual of dead code |
| Operations | `impact_of` (blast radius + tests) | ✅ Done | reverse reachability |
| Operations | `trace_path` (best-confidence path) | ✅ Done | full-stack, (max,×) semiring |
| Operations | `scan` (ranked issues + urgency) | ✅ Done | stubs/holes/cycles/god-objects |
| Operations | `reindex` | ✅ Done | full rebuild; `--precise` |
| **Algebra** | GraphBLAS reachability sweeps | ✅ Done | frontier BFS, pure-Python fallback |
| Algebra | GraphBLAS PageRank centrality | ✅ Done | whole-graph hub ranking |
| Algebra | CSR matrix export (`get_matrix`) | ⬜ To do | bounded submatrix for an LLM |
| **Cross-language** | Web-route resolver (decorator → Route → handler) | ✅ Done | Flask/FastAPI patterns |
| Cross-language | SQL resolver (sqlglot → table, READS/WRITES) | ✅ Done | query string literals |
| Cross-language | ORM resolver (model → table/column, MAPS_TO) | ✅ Done | SQLAlchemy/Django; converges with SQL |
| Cross-language | HTML template → route (`SUBMITS_TO`) | ⬜ To do | next resolver |
| **Data flow** | Data-loop detection (🟡) | ⬜ To do | needs READS/WRITES at var granularity |
| **Risk** | git-history churn × centrality | ⬜ To do | hidden coupling, hotspots |
| **Runtime** | runtime-trace fusion (`RUNTIME_HITS`) | ⬜ To do | observed vs static |
| **Semantic** | vector `find_similar` | ⬜ To do | embeddings + sqlite-vec |
| **Quality** | golden-fixture eval harness | 🟡 Partial | per-feature tests exist; no precision/recall harness yet |
| Quality | dogfood on own source | ✅ Done | 7 genuine dead-code candidates found |

## Test coverage

31 tests (`tests/`): envelope, store + incremental, extractor, operations,
cross-language resolvers (routes/SQL/ORM), and the GraphBLAS algebra (asserts the
accelerated sweeps agree with the pure-Python reference).

## Known seams (honest)

- `find_stale` is `needs_review` at 0.6 — resolution is name/scope-based, not
  type-grade. `--precise` (jedi) and a future LSP raise this.
- Context-manager (`with`) and attribute-read (property) usages aren't modelled
  yet, so a few real-but-implicit usages surface as stale candidates.
- PageRank hub ranking rewards transitively-depended-on nodes; tuning (e.g.
  transitive fan-in) is a possible refinement.
