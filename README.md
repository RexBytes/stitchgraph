# stitchgraph

Local-first, MCP-native code intelligence. Point it at a codebase to find
**stale code, implementation holes, orientation, and impact** — ranked by what's
actually live, every answer carrying a confidence and a reason to double-check.

One core library, three thin surfaces over the same operations:

- **Library API** — `import stitchgraph`
- **CLI** — `stitchgraph find-symbol ...` (`pip install 'stitchgraph[cli]'`)
- **MCP server** — for LLM agents (`pip install 'stitchgraph[mcp]'`, then
  `stitchgraph-mcp --db /path/to/stitchgraph.db`; env `STITCHGRAPH_DB` also works)

> **Read-only on your code.** stitchgraph only ever writes to its own index DB.
> Every result is advisory — it returns ranked options for a human/LLM to act
> on; it never edits or deletes source.

See [`docs/OVERVIEW.md`](docs/OVERVIEW.md) for a one-page stocktake (operations,
languages, surfaces, use cases), [`docs/design.md`](docs/design.md) for the full
design & capability map, [`docs/STATUS.md`](docs/STATUS.md) for what's built, and
[`AGENTS.md`](AGENTS.md) for the agent rules that teach an LLM when to call which
tool.

## What it delivers

Point it at a repo and ask plain questions about it. Every answer is ranked and rides a
**confidence + a reason to double-check** — so you (or an LLM) know how much to trust it.

- **What's dead?** — `find_stale`: likely-unused code, precision-biased so it never confidently
  flags *live* code dead (the cardinal rule).
- **What breaks if I change this?** — `impact_of`: everything reachable / at risk downstream.
- **How does a request flow end-to-end?** — `trace_path`: UI → route → handler → ORM → SQL table →
  column, *across languages*.
- **Where's the code that does X?** — `find_similar`: by name/docs (semantic) or, new in v3.0.0, by
  **body shape** (finds renamed / reordered clones a text search misses).
- **How do two builds differ?** — `graph_diff`: call-level deltas **plus** body-shape changes
  (Python + JS/TS/TSX + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash), so a data-flow bug that leaves the call graph identical still shows up.
- **I'm new here — orient me.** — `orient` / `summarize_subsystem` / `risk`: central modules, entry
  points, and the files most dangerous to touch.
- **What's referenced but missing?** — `find_holes`: dangling references that don't resolve.

It runs **fully local** against a plain SQLite index (no code leaves your machine), spans **12
languages** in one graph, and exposes the same operations as a **library, a CLI, and an MCP server**
for LLM agents. The full operation list and the question each answers is in the table
[below](#what-each-operation-answers).

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
- **Sees inside functions, not just between them.** The **body matrix**
  (Python + JS/TS/TSX + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash) fingerprints each function's value flow order- and
  name-invariantly, so `find_similar(mode="structure")` catches renamed / reordered
  clones and `graph_diff` flags a data-flow change that leaves the call graph
  identical — advisory and read-only, never feeding dead-code detection.
- **Layered — one graph, pick the depth (§5c).** `get_matrix(layer="call")` is the
  inter-procedural relation graph; `layer="statement"` (v3.9.0) drills into a
  function's program-dependence graph (control/data deps, Python + the JS family + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash — every body-matrix language);
  `layer="expression"` (v3.8.0) drills into its value-flow graph (all 12 languages).
  Same primitives, call → statement → expression depth; the deeper layers are advisory
  and computed on demand.
- **Scales to monorepos.** The v2 **constant-memory streaming indexer** indexes
  tens-of-thousands-of-file repos (e.g. Magento, 24k PHP files) without holding
  the whole graph in RAM — see below.

## Status (v3.26.0 — the external-review hardening release: 24 findings fixed across the POD math, body matrix, extractor, streaming, and adapters — see `docs/RELEASE_NOTES_v3.25.0.md`; the toolkit itself: `select_tests`/`co_change`/`find_coupling`/`find_gaps`/`test_order`/`redundant_tests`/`find_core`/`feature_map`/`find_outlier_tests`/`runtime_risk`/`coverage_drift` atop `find_modes`/`find_subsystems`/`find_chokepoints`, over the layered call ↔ statement ↔ expression code-property graph)

Working end-to-end and dogfooding on its own source. The per-language **cardinal
sweep is complete across all supported languages** (Python + 11 via tree-sitter),
and the body matrix (v3.0.0) now spans Python, the JS family (v3.2.0), Go (v3.3.0),
Rust (v3.4.0), C/C++ (v3.5.0), Java + C# (v3.6.0), and Ruby + PHP + Bash (v3.7.0) — all 12 languages.
**v3.8.0 makes it a layered graph (§5c):** `get_matrix`/`graph_diff` now drill from the
call layer into a function's value-flow (expression) layer, and the statement (PDG)
layer spans every one of the 12 body-matrix languages. **v3.19.0–v3.20.0 add the first
spectral-analysis operations (§6):** `find_chokepoints` (articulation points ranked by
blast radius) and `find_subsystems` (spectral clustering of the call graph into
auto-labelled subsystems). **v3.21.0 adds behavioural analysis (§6 win 3):**
`find_modes` (POD/SVD of a per-test coverage matrix → runtime behavioural modes,
intrinsic dimensionality, minimal covering test set) and `scaffold_coverage` (generates
a sandboxed Docker/shell/CI kit so you capture that coverage in your own jail —
stitchgraph never runs your code, it only reads the inert matrix). **v3.22.0–v3.23.0 turn
that matrix into a complete forward-looking toolkit (§6):** `select_tests` (which tests to
run for a change/changeset), `co_change` (what code moves together), `find_coupling`
(implicit coupling — co-run but no static edge), `find_gaps` (untested functions, live vs
dead), `test_order` (fail-fast ordering), `redundant_tests` (identical-profile clusters),
`find_core` (the always-on core), `feature_map` (mode ↔ code ↔ tests), `find_outlier_tests`
(unique-behaviour vs smoke), `runtime_risk` (churn × behavioural centrality), and
`coverage_drift` (behavioural changelog across snapshots). All advisory, all read-only;
the set-math ones need no numpy. See
[`docs/OVERVIEW.md`](docs/OVERVIEW.md) for the one-page capability map and
[`docs/STATUS.md`](docs/STATUS.md) for the full table + roadmap.

### Headline: the intra-procedural body matrix (Python + JS/TS/TSX + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash)

Every prior release modeled code *between* definitions — a graph of functions /
classes linked by CALLS / REFERENCES / INHERITS / IMPORTS. **v3.0.0 added the level
below that**, **v3.2.0 extended it to JavaScript/TypeScript**, **v3.3.0 added Go**,
**v3.4.0 added Rust, v3.5.0 added C/C++, v3.6.0 added Java and C#**, and **v3.7.0 adds Ruby, PHP and Bash** (all 12 languages): a per-function
**value-flow fingerprint** (`core/structure.py`
for Python, `core/structure_js.py` for the JS family, `core/structure_go.py` for Go,
`core/structure_rust.py` for Rust, `core/structure_cpp.py` for C/C++, `core/structure_java.py` for
Java, `core/structure_csharp.py` for C#) — operations + control points, data + control
edges, copy propagation — fingerprinted
**order- and name-invariantly** via a Weisfeiler-Lehman kernel. Renamed locals,
reordered independent statements, and temp-variable factoring all read as the *same
shape* (an arrow rewrite and TS type annotations included). It powers two advisory
capabilities:

- **`find_similar(mode="structure")`** — rank stored functions by **body shape**,
  finding renamed / reordered / temp-var clones (Type-2/Type-3) a token differ
  misses. The snippet's language is auto-detected and ranked same-language only.
- **body-aware `graph_diff`** — structurally diff two built indexes: call-level
  node/edge deltas *plus*, for functions in both, those whose **body shape
  diverged** — so a data-flow change that leaves the call graph identical (the
  classic translation / plan-vs-actual bug) is still caught.

Both are **advisory and read-only** — they never feed `find_stale`, so the cardinal
rule is structurally unaffected. **Python is stdlib-only; the JS/TS, Go, Rust,
C/C++, Java, C#, Ruby, PHP, and Bash layers need the tree-sitter extra.** Cross-language *body* comparison stays
oracle-only (topology tracks the extractor); the features rank/diff within one
language. It is a structural *approximation*, not sound data flow — full scope and
limits in [`docs/RELEASE_NOTES_v3.7.0.md`](docs/RELEASE_NOTES_v3.7.0.md).

```python
import stitchgraph as sg

with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")
    # rank stored functions by body shape (renamed/reordered clones; Python, JS/TS, Go, Rust, C/C++, Java, C#, Ruby, PHP, or Bash)
    print(sg.find_similar(store, open("some_func.py").read(), mode="structure"))
    # body-aware structural diff against another built index
    print(sg.graph_diff(store, "other_index.db"))   # body-aware by default
```

### Scale (v2): the streaming indexer (GB → MB)

`reindex` can stream the graph straight to SQLite instead of building it all in
Python first, so peak memory tracks one file's working set — **not** the size of
the whole repo.

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
- **30 operations**, all real: `find_symbol`, `get_callers`, `get_callees`,
  `orient`, `find_stale`, `find_holes`, `impact_of`, `trace_path`, `scan`,
  `get_matrix`, `summarize_subsystem`, `risk`, `ingest_trace`, `find_similar`,
  `graph_diff`, `find_chokepoints`, `find_subsystems`, `find_modes`,
  `scaffold_coverage`, `select_tests`, `co_change`, `find_coupling`, `find_gaps`,
  `test_order`, `redundant_tests`, `find_core`, `feature_map`, `find_outlier_tests`,
  `runtime_risk`, `coverage_drift`, plus admin `reindex`. Generated as **library API + CLI + MCP**, plus a Markdown
  `report`, a `watch` command, and a `doctor` grammar self-check.
- **Intra-procedural body matrix (Python v3.0.0; JS/TS/TSX v3.2.0; Go v3.3.0; Rust v3.4.0; C/C++ v3.5.0;
  Java + C# v3.6.0, Ruby + PHP + Bash v3.7.0)** — a
  per-function value-flow fingerprint (`core/structure.py`, `core/structure_js.py`,
  `core/structure_go.py`, `core/structure_rust.py`, `core/structure_cpp.py`, `core/structure_java.py`,
  `core/structure_csharp.py`) that sees *inside* a
  function, not just its call
  edges. Powers `find_similar(mode="structure")` (rank by body shape — finds renamed / reordered /
  temp-var clones a token differ misses) and the body-aware layer of `graph_diff`. Advisory and
  read-only (never feeds `find_stale`); ranks/diffs within one language. The JS/TS, Go, Rust,
  C/C++, Java, C#, Ruby, PHP, and Bash layers need the tree-sitter extra; Python is stdlib-only.
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
| `find_chokepoints` | Which code entities (functions/methods/classes) are structural chokepoints — sole bridges whose removal fragments the graph — ranked by blast radius? |
| `find_subsystems` | What are the codebase's natural subsystems? — spectral clustering of the call graph, each cluster auto-labelled with its distinctive tokens. |
| `find_modes` | What are the codebase's *runtime* behavioural modes? — POD/SVD of a per-test coverage matrix; also yields the intrinsic dimensionality and a **minimal test set** that covers every executed function. Language-agnostic. |
| `scaffold_coverage` | Generate a **sandboxed** capture kit (Docker / shell / CI) that produces the per-test coverage artifact `find_modes` needs — stitchgraph never runs your code, you run the kit in your jail. |
| `scan` | Give me a ranked sweep of issues across the whole repo. |
| `summarize_subsystem` | What is this package/folder, in one shot? |
| `find_similar` | What else looks like this (duplication / refactor targets)? — token (default) or `mode="structure"` body-shape (Python + JS/TS/TSX + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash). |
| `graph_diff` | How do two indexes differ (translation fidelity / plan-vs-actual)? Call-level deltas + body-shape changes (Python + JS/TS/TSX + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash). |
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
