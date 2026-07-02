# stitchgraph — overview

A one-page stocktake of what stitchgraph is, what it offers, and what it's useful
for. For the full design see [`design.md`](design.md); for build status see
[`STATUS.md`](STATUS.md); for the per-language support matrix see
[`LANGUAGES.md`](LANGUAGES.md).

## What it is

**Local-first, MCP-native code intelligence.** Point it at a codebase and ask
plain questions about it — dead code, holes, orientation, impact, full-stack
traces — spanning **12 languages in one graph**. It is **read-only on your
source** (it writes only to its own SQLite index), and every answer carries a
**confidence + provenance + a reason to double-check**. One core library, three
thin surfaces (library / CLI / MCP) over the same operation set.

## The operations (what each answers)

| Operation | Group | Question it answers |
|---|---|---|
| `find_symbol` | Navigate | Where is X defined? |
| `get_callers` | Navigate | Who calls X? |
| `get_callees` | Navigate | What does X call? |
| `find_stale` | Health | What code is dead / unused? (precision-biased — never confidently flags *live* code dead) |
| `find_holes` | Health | What's referenced but missing / stubbed? (the dual of dead code) |
| `scan` | Health | Ranked issue list with urgency (stubs, holes, cycles, god-objects) |
| `orient` | Understand | New here — what are the counts and top hubs to read first? |
| `summarize_subsystem` | Understand | Compact structural summary of a subsystem (for an LLM) |
| `get_matrix` | Understand | Bounded relation submatrix; drills into a function's PDG / value-flow (`layer=call\|statement\|expression`) |
| `find_chokepoints` | Understand | Which nodes fragment the graph if removed? (articulation points × blast radius) |
| `find_subsystems` | Understand | What are the natural subsystems? (spectral clustering + auto-labels) |
| `find_modes` | Understand | What are the *runtime* behavioural modes? (POD/SVD of per-test coverage) — modes, intrinsic dimensionality, and a minimal covering test set; language-agnostic |
| `scaffold_coverage` | Admin | Generate a sandboxed capture kit (Docker/shell/CI) to produce the per-test coverage `find_modes` needs — the tool never runs your code |
| `impact_of` | Change safety | What breaks if I change this? Which tests to run? |
| `trace_path` | Change safety | How does a request flow end-to-end, with confidence? |
| `graph_diff` | Change safety | How do two builds differ — call-level *and* body-shape? |
| `find_similar` | Search | Where's the code that does X? (by name/docs, or by body shape) |
| `risk` | Risk | Which files are most dangerous to touch? (git churn × centrality + hidden coupling) |
| `ingest_trace` | Runtime | Mark what actually executed (coverage.json / LCOV / Go coverprofile) |
| `reindex` | Admin | (Re)index a path into the graph |

## Languages (12, in one graph)

| Depth | Languages |
|---|---|
| **Deep** (stdlib `ast`, + optional jedi `--precise`) | Python |
| **Polyglot** (tree-sitter: defs + call graph + body matrix) | JS/TS/TSX, Rust, C, C++, C#, Go, Java, Ruby, PHP, Bash |

All resolve into a **single typed graph**, so analysis and traces cross language
boundaries.

## Cross-language / full-stack tracing

| Capability | Coverage |
|---|---|
| Web routes | Flask, FastAPI, Django URLconf, Express, Spring (`@*Mapping`) |
| HTML → route | `<form action>` → Route (`SUBMITS_TO`) |
| Client → server | JS `fetch` → backend route |
| Decoupled events | emit/on → Event → handler (`EMITS` / `HANDLES`) |
| SQL | sqlglot → table, `READS` / `WRITES` |
| ORM | SQLAlchemy / Django → table/column (`MAPS_TO`), converges with SQL |
| **The "gem"** | Full-stack trace: form / JS → route → handler → ORM → SQL table → column |

## Surfaces & tooling

| Surface | Notes |
|---|---|
| Library API | `import stitchgraph` — the source of truth (operation registry) |
| CLI | Typer, generated from the registry (`--json`, exit codes) |
| MCP server | FastMCP, generated from the registry — for LLM agents |
| Markdown report | `stitchgraph report` (orientation + issues + risk) |
| `AGENTS.md` | Agent rule file teaching an LLM when to call which tool |
| `watch` | Re-index on change (stdlib polling) |
| CI + PyPI | GitHub Actions publish workflow |

## Design principles (what makes the answers trustworthy)

- **Read-only** — never mutates source; writes only to the index DB.
- **Honest envelope** — every result carries `confidence / provenance /
  needs_review / urgency`; provenance caps the urgency ceiling, so a heuristic
  link can never masquerade as a fact.
- **The cardinal rule** — *live code is never confidently flagged dead*: dead-code
  detection is biased toward precision, driven by dozens of entry-point /
  liveness signals across languages.
- **Sees inside functions** — the "body matrix" models intra-procedural structure
  (value-flow + PDG layers), so a data-flow change that leaves the call graph
  identical still shows up in `graph_diff` / `find_similar(structure)`.
- **Local & private** — plain SQLite, no code leaves the machine; works offline,
  in CI, and in air-gapped environments.

## Optional dependencies (core is stdlib-only)

| Extra | Unlocks |
|---|---|
| `cli` | Typer CLI |
| `mcp` | MCP server |
| `treesitter` / `treesitter-download` | Polyglot extraction (bundled-offline vs download-latest grammar lines) |
| `precise` | jedi type-grade Python resolution (`reindex --precise`) |
| `resolve` | SQL resolver (sqlglot) |
| `algebra` | GraphBLAS-accelerated reachability / PageRank / transitive fan-in |
| `spectral` | scipy sparse eigensolver — uncaps `find_subsystems` on large graphs |

## What it's useful for

| Audience | Use it for |
|---|---|
| **LLM coding agents** (the native fit) | MCP tools that answer "what's dead / what breaks / where's X / trace this" with calibrated confidence — so the agent acts on facts, not guesses |
| **Devs onboarding** | `orient` / `summarize_subsystem` / `find_subsystems` / `risk` to map an unfamiliar codebase and find the dangerous-to-touch files |
| **Refactoring / cleanup** | `find_stale` (dead code), `find_holes` (dangling refs), `find_chokepoints` (fragile bridges), `scan` (ranked issues) |
| **Change safety / review** | `impact_of` (blast radius + tests to run), `trace_path` (full-stack flow), `graph_diff` (plan-vs-actual, translation checks, hidden data-flow changes) |
| **Full-stack debugging** | Trace a request across HTML → route → handler → ORM → SQL, in one graph |
| **Runtime fusion** | `ingest_trace` to blend real coverage into the liveness picture |

## Maturity

Production/Stable, MIT-licensed, dogfooded on its own source. The test suite runs
in the low thousands of cases with high coverage. Development is driven by a
strict adversarial-panel gate — a release is RELEASABLE only after **two
consecutive clean full-diversity review panels** on a frozen commit. Recent line:
the §6 "system-matrix" research wins are landing as operations —
`find_chokepoints` and `find_subsystems` have shipped.
