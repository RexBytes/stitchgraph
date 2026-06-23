# Changelog

All notable changes to stitchgraph. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [0.1.0] — 2026-06-23

First tagged release. A local-first, MCP-native code-intelligence graph for
Python that finds **stale code, implementation holes, orientation, and impact** —
ranked by what's actually live, every answer carrying a confidence and a reason
to double-check. One core library, three thin surfaces (library API, CLI, MCP),
plus a Markdown report. **Read-only on your code — it never edits source.**

### Highlights

- **One graph, three surfaces.** A single operation registry is the Python
  library API; the Typer CLI and the FastMCP server are generated from it, so
  they map 1:1 by construction. Optional deps are lazy-imported (core is
  stdlib-only).
- **The refuse-when-unsure envelope.** Every result carries
  `confidence / provenance / needs_review / urgency`. Confidence is load-bearing:
  it gates review and propagates through the path algebra; provenance caps the
  urgency ceiling so nothing low-confidence shouts red.
- **Dead code & its dual, holes.** `find_stale` (unreachable from entry points)
  and `find_holes` (references with no target) — advisory, never asserted.
- **Full-stack cross-language tracing** — the "gem": HTML form → route → handler
  → … → DB table/column in one `trace_path`, confidence propagated.
- **GraphBLAS algebra** for whole-graph sweeps (reachability, transitive fan-in,
  PageRank) with a pure-Python fallback that the two agree on by test.
- **Git-risk fusion, runtime-trace fusion, semantic retrieval, data loops** —
  see operations below.

### Operations (14)

`find_symbol`, `get_callers`, `get_callees`, `orient`, `find_stale`,
`find_holes`, `impact_of`, `trace_path`, `scan` (ranked issues + urgency),
`reindex` (`--precise` for jedi), `get_matrix` (bounded submatrix), `risk`
(git churn × centrality + hidden coupling), `ingest_trace` (coverage fusion),
`find_similar` (token retrieval). Plus `stitchgraph report` and `AGENTS.md`
agent rules.

### Languages & frameworks

- **Fully analysed:** Python 3.11+ (stdlib `ast`; optional `jedi`).
- **Detected at the cross-language boundary:** web routes (Flask/FastAPI/
  blueprints), HTML templates (`<form action>`), SQL (via sqlglot), ORM
  (SQLAlchemy/Django). See [`docs/LANGUAGES.md`](docs/LANGUAGES.md).

### Configuration

`stitchgraph.toml`: entry-point overrides (the trust escape hatch), index ignore
globs, review threshold, hub metric. See `stitchgraph.toml.example`.

### Quality

53 tests, including a precision/recall eval harness that asserts the
precision-over-recall stance (never flag live code as dead). Dogfoods on its own
~235-node source: clean scan, holes 0, and a short list of genuine dead-code
candidates — all `needs_review`.

### Known limitations (honest)

- `find_stale` is `needs_review` at 0.6 (0.78 with a runtime trace): resolution
  is name/scope-based, not type-grade. `--precise` (jedi) and a future LSP raise
  this.
- Framework callbacks overriding an *external* base class (e.g. an `HTMLParser`
  method) can look uncalled — the remaining stale false-positive class.
- Python-only first-class analysis for now; JS/TS/Java and a tree-sitter/LSP
  backend are the next step.

[0.1.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v0.1.0
