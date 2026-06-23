# stitchgraph v0.3.0 — "Depth & Breadth"

Local-first, MCP-native code intelligence. Find **stale code, implementation
holes, orientation, and impact** across a multi-language codebase — ranked by
what's actually live, every answer carrying a confidence and a reason to
double-check. **Read-only on your code.**

v0.2.0 made stitchgraph polyglot. v0.3.0 makes the polyglot support *deeper* and
the cross-language web *wider* — plus CI, packaging, and multi-language runtime
fusion.

## ✨ Highlights

### Wider cross-language web
- **Framework routes:** Django URLconf, **Express** (JS), and **Spring** (Java
  `@GetMapping`/`@RequestMapping`) join Flask/FastAPI.
- **JS `fetch` → backend route:** a client call links to the route, so one
  `trace_path` runs **JS function → route → backend handler → DB table**.
- **Events (EMITS/HANDLES):** `emit`/`on` create `Event` nodes — `trace_path`
  now crosses decoupled pub/sub boundaries the call graph can't see.

### Deeper polyglot support
- **Imports**, **inheritance** (`INHERITS` from class heritage — JS/Java/C++/C#/
  Ruby/PHP), and **per-language test entry points** for the tree-sitter languages.
  `impact_of` flows through inheritance now. (See the per-language matrix in
  [`docs/LANGUAGES.md`](docs/LANGUAGES.md).)
- **Framework-callback handling:** methods overriding an *external* base (e.g.
  `HTMLParser.handle_starttag`) are roots, not dead-code false positives — the
  last false-positive class is gone.

### Multi-language runtime fusion
- `ingest_trace` now auto-detects **coverage.py JSON**, **LCOV** (JS/nyc,
  C/C++ gcov), and **Go coverprofiles** — runtime grounding beyond Python.

### Tooling & ergonomics
- **`watch`** — re-index on file changes.
- **`summarize_subsystem`** — compact map of a subsystem (counts, hubs, public
  surface, dependencies). **`get_matrix`** now includes a small dense grid.
- **Pluggable embeddings** for `find_similar` (`set_embedder()`); token default,
  optional model2vec/sentence-transformers — **no model bundled**.
- **CI** (GitHub Actions) + **PyPI** publish workflow. SQLite schema migration so
  older index files keep working.

## What's left (roadmap)

The two largest deferred items, both documented in
[`docs/STATUS.md`](docs/STATUS.md#roadmap-whats-left):

- **LSP backend** (type-grade resolution, multi-language) — lifts the whole
  accuracy ceiling and would push `find_stale` past its name-based 0.6 confidence.
  Needs language-server binaries; `--precise` (jedi) is the Python path today.
- **Variable-granularity data flow** — unlocks non-global data loops and argument
  provenance/taint.

Also queued: gRPC/OpenAPI resolvers, more ORMs/frameworks, large-scale (~100k
node) validation, and true incremental reindex.

## Install

```bash
pip install 'stitchgraph[all]'      # everything
# or pick: stitchgraph[cli,treesitter,algebra,resolve,precise,mcp]
```

## Tests

72 tests, including polyglot extraction (Python + 11 languages), the framework /
event resolvers, multi-format runtime traces, a pluggable-embedder check,
file-watching, schema migration, and a precision/recall harness asserting the
never-flag-live-code-as-dead stance.

**Full changelog:** [`CHANGELOG.md`](../CHANGELOG.md)
