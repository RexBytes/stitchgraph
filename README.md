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

## Why stitchgraph

- **Local-first & private.** Everything runs on your machine against a plain
  SQLite file. No code leaves the box; a full multi-language reindex works
  offline, in CI, and in air-gapped environments.
- **Honest answers, not guesses.** Every result rides a universal envelope —
  `confidence / provenance / needs_review / urgency` — so you (or an LLM) know
  how much to trust each finding and why. Provenance caps the urgency ceiling,
  so a heuristic link can never masquerade as a fact.
- **Liveness-aware.** The cardinal rule: **live code is never confidently
  flagged dead.** Dead-code detection is biased toward precision (it would
  rather miss a corpse than accuse a living function), driven by dozens of
  entry-point/liveness signals across languages.
- **Polyglot, in one graph.** Python (deep) + 11 more languages via tree-sitter,
  plus cross-language resolvers, all resolving into a single typed graph — so a
  trace can cross an HTML form → route → handler → ORM → SQL table → column.
- **Scales to monorepos.** The v2 **constant-memory streaming indexer** indexes
  tens-of-thousands-of-file repos (e.g. Magento, 24k PHP files) without holding
  the whole graph in RAM — see below.

## Status (v2.0.0 — constant-memory streaming indexer)

Working end-to-end and dogfooding on its own source. See
[`docs/STATUS.md`](docs/STATUS.md) for the full table + roadmap.

### Headline: the streaming indexer (GB → MB)

The big v2 change. `reindex` can now stream the graph straight to SQLite instead
of building it all in Python first, so peak memory tracks one file's working set
— **not** the size of the whole repo.

Measured on a Magento module (`lib/`, 4,304 PHP files → 30,412 nodes, ~15.5M raw
edges from name-based fan-out):

| | peak RSS | output |
|---|---|---|
| in-memory (`streaming=False`) | **3,183 MB** | 30,412 nodes / 3,926,345 edges |
| streaming (`streaming=True`)  | **269 MB** | 30,412 nodes / 3,926,345 edges |

**~12× less memory, byte-identical output** (verified row-for-row), ~40% slower.
Repos that used to OOM now index on a laptop. The streamed index is pinned
byte-for-byte against the in-memory one by a differential oracle
(`tests/oracles/test_streaming_differential.py`) across Python + JS/TS + Go +
Ruby + C/C++ + Rust + PHP, so the lower-memory path can never silently diverge.

```python
import stitchgraph as sg

with sg.Store("stitchgraph.db") as store:      # an on-disk DB realises the win
    sg.reindex(store, "/path/to/huge/monorepo", streaming=True)
    print(sg.find_stale(store))
```

How it works (details in [`docs/V2_STREAMING_DESIGN.md`](docs/V2_STREAMING_DESIGN.md)):
each file's AST / parse-tree **and** source bytes are dropped after pass 1 (only a
tiny per-definition record survives), and edges are deduplicated per-source on the
fly and written to SQLite in committed batches — so neither the parse trees nor the
millions of edges are ever all resident at once.

### Core capabilities

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
  `report`, a `watch` command, and a `doctor` grammar self-check.
- **Cross-language resolver pipeline** — routes (Flask/FastAPI/Django/Express/
  Spring), HTML forms, JS `fetch`, events, SQL, and ORM; ORM and SQL converge on
  the same `db::<table>` node, so `trace_path` crosses HTML/JS → route → handler →
  … → table → column.
- **GraphBLAS algebra** — whole-graph reachability, transitive fan-in, and
  PageRank (pure-Python fallback; the two agree by test).
- **Risk** (git churn × centrality + hidden coupling), **runtime fusion**
  (coverage.py JSON / LCOV / Go coverprofile), **semantic** `find_similar`
  (token default; pluggable dense embedder), **data-loop** detection.

### What each operation answers

| Operation | Question it answers |
|---|---|
| `find_symbol` / `get_callers` / `get_callees` | Where is this defined, who calls it, what does it call? |
| `orient` | I'm new here — what are the central modules and entry points? |
| `find_stale` | What code is (confidently) dead and safe to consider removing? |
| `find_holes` | Where does the code reference something that doesn't resolve? |
| `impact_of` | If I change X, what is reachable / at risk downstream? |
| `trace_path` | How does a request flow end-to-end (UI → route → handler → DB)? |
| `risk` | Which files are most dangerous to touch (churn × centrality × coupling)? |
| `scan` | Give me a ranked sweep of issues across the whole repo. |
| `summarize_subsystem` | What is this package/folder, in one shot? |
| `find_similar` | What else looks like this (duplication / refactor targets)? |
| `ingest_trace` | Fuse runtime coverage so "live" reflects what actually ran. |

> Trust model: every answer carries a confidence and provenance. `find_stale` is
> precision-biased (never confidently flags live code dead); heuristic/cross-language
> links are marked as such and are capped at a lower urgency than extracted facts.

## Languages

- **Deepest:** Python 3.11+ (stdlib `ast`; optional `jedi` for `--precise`).
- **Extracted via tree-sitter** (definitions + call graph → dead-code, orient,
  impact, trace): **JavaScript, TypeScript/TSX, Rust, C, C++, C#, Go, Java, Ruby, PHP, Bash**.
- **Detected at the cross-language boundary:** web routes (Flask/FastAPI,
  Django, Express, Spring), HTML `<form action>`, JS `fetch`, events (emit/on),
  SQL (sqlglot), ORM (SQLAlchemy/Django) — powering the full-stack `trace_path`.

Full support matrix in [`docs/LANGUAGES.md`](docs/LANGUAGES.md).

The tree-sitter grammars ship **offline by default**: `pip install
'stitchgraph[treesitter]'` (or `[all]`) pins the bundled-grammar line, so a full
multi-language reindex runs with no network — local-first, CI- and air-gap-safe.
Prefer the newest grammars fetched on first use? Install `[treesitter-download]`
instead. Run `stitchgraph doctor` (`--strict` for a CI gate) to see which grammars
load.

## Tested for robustness

Ground-truthed against ~47 real-world projects across 9 languages (incl. IOCCC,
Linux kernel core, WordPress, Magento, NestJS, flake8) with **0 crashes**, plus a
three-layer release gate: adversarial model panels, a suite of differential
oracles (incremental == full, streaming == full, GraphBLAS == pure-Python), and a
mutation meta-oracle. Hostile inputs (non-UTF-8 paths, embedded NULs, pathologically
deep ASTs, broken symlinks, FIFOs) degrade gracefully to a smaller index — never a
crash.

## Quick look

```python
import stitchgraph as sg

with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, ".", streaming=True)   # build/refresh the index
    print(sg.find_symbol(store, "UserService"))
    print(sg.find_stale(store))              # likely-dead code, ranked
    print(sg.find_holes(store))              # dangling references
```

## What's next

**Biggest deferred items:** an LSP backend (type-grade resolution) and
variable-granularity data flow — see the
[roadmap](docs/STATUS.md#roadmap-whats-left). The constant-memory indexer (the
former top deferred item) shipped in v2.

## Develop

```bash
pip install -e '.[all,dev]'   # all extras so the full suite runs
PYTHONPATH=src python -m pytest -q
```

CI runs the suite on Python 3.11 and 3.12 (see `.github/workflows/ci.yml`).
Tests that need an optional dependency skip gracefully when it's absent.
