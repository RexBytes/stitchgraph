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
| Surfaces | `watch` (re-index on change) | ✅ Done | stdlib polling; differential apply via `replace_file` (v3.38.0), full-reindex fallback on deletions / streaming-scale trees |
| Surfaces | CI + PyPI publish workflow | ✅ Done | GitHub Actions |
| **Extraction** | Python extractor (stdlib `ast`) | ✅ Done | Module/Class/Function/Method/Test |
| Extraction | Scope-aware resolution (`self.`, locally-typed `var.`) | ✅ Done | + decorator refs |
| Extraction | jedi precise resolver (`--precise`) | ✅ Done | optional, in-process LSP-grade |
| Extraction | **Polyglot tree-sitter extractor** | ✅ Done | JS/TS, Rust, C/C++, C#, Go, Java, Ruby, PHP, Bash (defs + call graph) |
| Extraction | Polyglot imports / inheritance / test entry points | ✅ Done | per-language (see LANGUAGES.md matrix) |
| Extraction | Framework-callback handling (external base) | ✅ Done | overrides of a framework base aren't dead |
| Extraction | External multi-language LSP backend | ⬜ To do | type-grade resolution per language |
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
| Operations | `get_matrix` (bounded submatrix) | ✅ Done | refuses broad scope; small dense grid; `layer=call\|statement\|expression` (§5c drill-down into a function's PDG / value-flow graph) |
| Operations | `summarize_subsystem` | ✅ Done | counts, hubs, public surface, deps |
| Operations | `ingest_trace` (runtime fusion) | ✅ Done | coverage.py JSON / LCOV / Go coverprofile |
| Operations | `risk` (git × structure) | ✅ Done | hotspots + hidden coupling |
| Operations | `find_chokepoints` (criticality) | ✅ Done | articulation points ranked by blast radius (§6); advisory |
| Operations | `find_subsystems` (decomposition) | ✅ Done | spectral clustering + token labels (§6); numpy-dense, sparse via `[spectral]`; advisory |
| Operations | `find_modes` (runtime behaviour) | ✅ Done | POD/SVD of per-test coverage (§6); modes + intrinsic dim + minimal test set; language-agnostic; numpy-required, sparse via `[spectral]`; advisory |
| Operations | `scaffold_coverage` (capture kit) | ✅ Done | generates sandboxed Docker/shell/CI recipe for per-test coverage; tool never executes code |
| Operations | `select_tests` (change → tests) | ✅ Done | runtime coverage × static blast radius (§6); both/runtime_only/static_only; no numpy; advisory |
| Operations | `co_change` (outcome → code) | ✅ Done | functions co-activating with a symbol (§6); behavioural neighbourhood; no numpy; advisory |
| Operations | `find_coupling` (implicit deps) | ✅ Done | pairs that co-run but have no static edge (§6); runtime∖structure; no numpy; advisory |
| Operations | `find_gaps` (coverage gaps) | ✅ Done | untested functions × reachability → live (gap) vs dead (§6); no numpy; advisory |
| Operations | `test_order` (fail-fast) | ✅ Done | greedy new-coverage-first ordering; prefix = minimal cover (§6); no numpy; advisory |
| Operations | `redundant_tests` (clusters) | ✅ Done | identical-profile test groups (§6); review aid, not auto-delete; no numpy; advisory |
| Operations | `find_core` (always-on core) | ✅ Done | functions by activation frequency (§6); runtime companion to find_chokepoints; no numpy |
| Operations | `feature_map` (mode → code × tests) | ✅ Done | per-mode functions × files × tests (§6); POD/SVD; numpy-required; advisory |
| Operations | `find_outlier_tests` (unique vs smoke) | ✅ Done | mode-space reconstruction residual (§6); POD/SVD; numpy-required; advisory |
| Operations | `runtime_risk` (churn × behaviour) | ✅ Done | git churn × behavioural centrality (§6); runtime companion to risk; no numpy |
| Operations | `coverage_drift` (snapshots) | ✅ Done | functions gained/lost test exposure between two coverage snapshots (§6); no numpy |
| Operations | `find_similar` (semantic-ish) | ✅ Done | token default; pluggable dense embedder |
| Operations | `find_component` (purpose locator) | ✅ Done | research-validated 76% P@1 (v3.32.0); test-excluded, public-boosted |
| Operations | `audit_graph` (call-graph precision audit) | ✅ Done | static reach vs runtime ground truth; resolver-gap worklist (v3.33.0) |
| **Storage** | **Homonym-group edge compression** | ✅ Done | v3.41.0: content-addressed candidate-set interning + `edges_all` view; Django index 41.6s→24.0s, 278MB→25MB, byte-identical answers; HA field: 4 min / 317MB for 16.1M logical edges (research/20) |
| Algebra | **Sampled transitive fan-in past the closure cap** | ✅ Done | v3.42.0: deterministic source sampling over the bit-parallel sidecar sweep; exact within budget, honestly named when sampled; orient's transitive ranking at any scale |
| Core | **Persistent symbol table + single-file extraction** | ✅ Done | v3.43.0: symtab table + store-backed _Project views; watch fast path at any scale (HA edit loop 4min→13.6s, research/21); honest resolver gating |
| **Algebra** | GraphBLAS reachability sweeps | ✅ Done | frontier BFS, pure-Python fallback |
| Algebra | GraphBLAS transitive fan-in (hub ranking) | ✅ Done | boolean closure; orient default |
| Algebra | GraphBLAS PageRank centrality | ✅ Done | alt hub metric via config |
| **Cross-language** | Web routes (Flask/FastAPI, **Django**, **Express**, **Spring**) | ✅ Done | decorator / URLconf / app.get / @*Mapping |
| Cross-language | HTML template → route (`SUBMITS_TO`) | ✅ Done | `<form action>` → Route |
| Cross-language | **JS `fetch` → backend route** | ✅ Done | client → server full-stack |
| Cross-language | **Events (EMITS/HANDLES)** | ✅ Done | emit/on → Event → handler (decoupled trace) |
| Cross-language | SQL resolver (sqlglot → table, READS/WRITES) | ✅ Done | query string literals |
| Cross-language | ORM resolver (model → table/column, MAPS_TO) | ✅ Done | SQLAlchemy/Django; converges with SQL |
| Cross-language | Full-stack trace (form/JS → route → handler → table) | ✅ Done | the "gem", end to end |
| Cross-language | **OpenAPI/Swagger + gRPC proto contract resolvers** | ✅ Done | spec/proto → ROUTE nodes + handler/Servicer binding (v3.35.0) |
| Cross-language | **Prisma + TypeORM** ORM resolvers | ✅ Done | schema.prisma / @Entity → DBTable MAPS_TO (v3.35.0) |
| **Data flow** | Data-loop detection (🟡) | ✅ Done | mutable-global feedback; surfaced in `scan` |
| **Risk** | git-history churn × centrality (`risk`) | ✅ Done | hotspots + hidden coupling |
| **Runtime** | runtime-trace fusion (`ingest_trace`) | ✅ Done | coverage.json → live seeds, +confidence |
| **Semantic** | `find_similar` retrieval | ✅ Done | token similarity; embedding model = drop-in |
| **Quality** | precision/recall eval harness | ✅ Done | precision-over-recall stance asserted |
| Quality | dogfood on own source | ✅ Done | genuine dead code found; risk hotspots ranked |

## Test coverage

2,380+ tests (`tests/`): envelope, store + incremental + migration, polyglot
extraction (Python + 11 tree-sitter languages), operations, config, `get_matrix`,
the body-matrix walkers + value-flow/PDG layers (all 12 languages), cross-language
resolvers (routes/Django/Express/Spring/HTML/JS-fetch/events/SQL/ORM) + full-stack
traces, the GraphBLAS algebra (accelerated sweeps agree with the pure-Python
reference), spectral subsystem decomposition + articulation-point criticality,
git-risk fusion, multi-format runtime traces, a pluggable-embedder check,
file-watching, and a precision/recall eval harness.

## Roadmap (what's left)

Agreed order (2026-07-06): the **dependency-free batch first** (persistent
symbol table, variable-granularity data flow, remaining tree-sitter
imports/inheritance, sidecar CSR group-sharing, module-level use attribution)
→ release → then the **LSP backend**, the one item that adds external
requirements (per-language server binaries, strictly optional at runtime).
Completing both phases closes everything currently known.

| Item | Effort | Why deferred |
|---|---|---|
| **LSP backend** (type-grade resolution, multi-language) + `type_at` | L | Needs language-server binaries + network; `--precise` (jedi) covers Python. Lifts the whole accuracy ceiling. |
| **Variable-granularity data flow** (beyond globals) | L | Big extractor lift; unlocks non-global data loops + argument provenance/taint. |
| Module-level use attribution (decorator/constructor applied at import) | S/M | The one Known-seams residual: module-level-only symbols can surface as needs_review stale candidates because their uses aren't attributed to a caller. Folded into the dependency-free batch (2026-07-06 plan). |
| Scope `_propagate_overrides` on the incremental path | M | The one remaining whole-graph pass per replace_file (~half of the 13.6s HA edit latency, research/21); its scoping semantics (changed subtrees/members/bound targets) are the subtlest of the three and reserved for a careful dedicated pass. |
| Sidecar CSR group-sharing (store an interned candidate set once in the mmap instead of expanding it per call site) | M | v3.41.0 compressed the STORE 10.5× (homonym-group interning, research/20); the adjacency sidecar still materialises the full expansion — correct and structurally pinned, but the same sharing would shrink the CSR and its build. Deferred: sidecar size hasn't been the bottleneck. |

## Known seams (honest)

- `find_stale` is `needs_review` at 0.6 (0.78 with a runtime trace) — resolution
  is name/scope-based, not type-grade. `--precise` (jedi) and a future LSP raise
  this further. This is the single biggest accuracy lever left.
- Context managers (`with`), property/attribute reads, framework-callback
  overrides, AND module-level / class-body uses (a decorator or constructor
  applied at import) are now modelled, so those no longer false-flag as stale.
  (The module-level seam was verified closed 2026-07-06: `_module_scope_edges`
  attributes module-level executable code to the module node, decorator refs
  run in the enclosing scope, and class-body statements are walked — empirical
  corpora with live roots flag only genuinely-dead symbols.)
