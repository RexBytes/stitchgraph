# stitchgraph

**Local-first code intelligence for humans and LLM agents.** Point stitchgraph at a
codebase and ask it plain questions — *what's dead? what breaks if I change this? how
does a request flow end to end? which tests should I run?* It indexes 12 languages
into a single SQLite graph on your machine, answers through three identical surfaces
(Python library, CLI, MCP server), and attaches a **confidence, a provenance, and a
reason to double-check** to every answer, so you always know how much to trust it.

## Supported language versions

Every bound below is verified against the bundled parsers by syntax probes
(newest-feature and oldest-baseline snippets must parse clean); per-feature
depth is in [docs/LANGUAGES.md](docs/LANGUAGES.md).

| Language | Oldest | Newest | Language | Oldest | Newest |
|---|---|---|---|---|---|
| **Python** | 3.0 | 3.13 | **Go** | 1.0 | 1.22+ |
| **JavaScript** | ES5 | ES2024 | **Java** | 7 | 21 |
| **TypeScript** | 2.0 | 5.x | **Ruby** | 1.9 | 3.4 |
| **Rust** | 2015 edition¹ | 2024 edition | **PHP** | 5 | 8.4 |
| **C** | C89 | C23 | **Bash** | 3 / POSIX sh | 5 |
| **C++** | C++98 | C++20 | **C#** | 2 | 12 |

¹ except the long-deprecated `try!` macro (`try` is a reserved word in the
modern grammar); Rust 2018+ parses in full.

Python is analysed by the running interpreter's stdlib `ast` (deep, scope-aware
resolution; interpreter ≥ 3.11) and falls back to tree-sitter for syntax newer
than the interpreter (e.g. PEP 695 under 3.11). Python 2 is not supported.

Two design commitments make it different:

- **It never guesses confidently.** Every result rides a universal envelope
  (`confidence / provenance / needs_review / urgency`), and the *cardinal rule* —
  live code is never confidently flagged dead — biases every liveness decision
  toward precision. When stitchgraph isn't sure, it says so and tells you why.
- **It measures what code *does*, not just what it says.** Beyond the static graph,
  the behavioural toolkit decomposes a per-test coverage matrix (POD/SVD) into your
  suite's *runtime behavioural modes* — how many independent behaviours you actually
  test, which 6% of tests cover everything, which functions co-run with no static
  link. These are answers no amount of reading source can produce.

Everything runs offline against a plain SQLite file. stitchgraph is **read-only on
your code** — it writes only to its own index, never executes your project, and every
finding is advisory: ranked options for a human or agent to act on.

---

## Contents

- [Install](#install)
- [Five-minute quickstart](#five-minute-quickstart)
- [The operations](#the-operations)
- [The behavioural toolkit (runtime analysis)](#the-behavioural-toolkit-runtime-analysis)
- [For LLM agents (MCP)](#for-llm-agents-mcp)
- [Trust model](#trust-model)
- [Languages](#languages)
- [Scale](#scale)
- [Develop](#develop)

---

## Install

```bash
pip install stitchgraph              # the full tool: CLI, MCP, 12 languages, all accelerators
pip install --no-deps stitchgraph   # lean: stdlib-only library core (Python analysis)
```

**The default install is the full-power one** (since v3.31.0): CLI, MCP server,
polyglot grammars, jedi precision, SQL resolution, the mmapped adjacency sidecar
(numpy), GraphBLAS and sparse solvers. Every accelerated path is pinned
byte-identical to its pure-Python reference by the test suite, so fast-by-default
costs nothing in trust. Two opt-outs:

- **Lean install**: `pip install --no-deps stitchgraph` — the library core is
  stdlib-only (every dependency is a guarded import) and degrades gracefully;
  a CI job pins this. The old extras (`[cli]`, `[mcp]`, `[treesitter]`, …) still
  exist for picking individual capabilities onto a `--no-deps` base.
- **Pure run**: `stitchgraph --pure …`, `stitchgraph-mcp --pure`, or
  `STITCHGRAPH_PURE=1` — everything installed, but sweeps use the reference
  implementations (identical results; for debugging or byte-reproducing old runs).

Run `stitchgraph doctor` (add `--strict` in CI) to check which grammars load.

## Five-minute quickstart

Index once, then ask questions. The index is a single SQLite file; re-run `reindex`
after large changes (or leave `stitchgraph watch .` running).

```bash
cd your-project
stitchgraph reindex . --db stitchgraph.db     # build the graph (12 languages, one pass)

stitchgraph orient --db stitchgraph.db        # new here? counts, entry points, top hubs
stitchgraph find-stale --db stitchgraph.db    # likely-dead code, precision-biased
stitchgraph scan --db stitchgraph.db          # ranked issues: stubs, holes, cycles, god objects
stitchgraph impact-of UserService --db stitchgraph.db   # blast radius + which tests to run
stitchgraph trace-path loadUsers users --db stitchgraph.db  # full-stack: JS → route → SQL table
stitchgraph report --db stitchgraph.db        # one Markdown report of all of the above
```

Every command takes `--json` for the raw envelope (machine-readable, full payload —
text output truncates long lists). Exit codes: `0` clean, `1` RED findings exist,
`2` operational failure (missing/unopenable `--db`) — safe to gate CI on.

The same operations, as a library:

```python
import stitchgraph as sg

with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, ".")
    print(sg.find_stale(store))       # every result is a Result envelope:
    print(sg.impact_of(store, "UserService"))  # .ok .result .confidence .needs_review
```

## The operations

Thirty-one operations, one question each. All advisory, all read-only, all carrying
the envelope.

| Ask | Operation(s) |
|---|---|
| Where is X, who calls it, what does it call? | `find_symbol`, `get_callers`, `get_callees` |
| I'm new here — orient me | `orient`, `summarize_subsystem`, `find_subsystems` |
| What's dead? What's referenced but missing? | `find_stale`, `find_holes` |
| Sweep the repo for issues, ranked | `scan` |
| What breaks if I change this? | `impact_of` |
| How does a request flow end to end? | `trace_path` (HTML form → route → handler → ORM → SQL table) |
| What's dangerous to touch? | `risk` (git churn × centrality), `find_chokepoints` (cut vertices × blast radius) |
| Where's the code that does X / clones of this? | `find_similar` — by tokens, or `mode="structure"` for **body-shape** clone detection (renamed/reordered clones a text diff misses) |
| How do two builds differ? | `graph_diff` — call-level deltas **plus** body-shape changes (catches a data-flow bug that leaves the call graph identical) |
| Drill into one function | `get_matrix(layer="call" \| "statement" \| "expression")` — call graph → program-dependence graph → value-flow graph |
| Ground liveness in reality | `ingest_trace` (coverage.py JSON / LCOV / Go coverprofile) |
| Rebuild the index | `reindex` (admin; `--precise` adds jedi) |

…plus the eleven behavioural operations below.

## The behavioural toolkit (runtime analysis)

The static graph describes structure. The behavioural toolkit measures **what your
test suite actually executes**, and answers questions that cannot be answered by
reading code — this is the part of stitchgraph that tells you things you don't
already know.

It consumes one inert artifact: a per-test coverage matrix (*which test executed
which function*). stitchgraph **never runs your code** — it generates a sandboxed
capture kit and you run it in your own jail:

```bash
stitchgraph scaffold-coverage --db stitchgraph.db     # writes Docker/shell/CI recipes
# run the generated kit (it runs YOUR tests in YOUR sandbox) → coverage_modes.json

stitchgraph find-modes --coverage coverage_modes.json --db stitchgraph.db
```

| Ask | Operation |
|---|---|
| How many *independent behaviours* does my suite exercise? What are they? | `find_modes` — POD/SVD of the coverage matrix: behavioural modes, intrinsic dimensionality, a **minimal covering test set** |
| Which tests should CI run for this change / this PR? | `select_tests` (runtime evidence fused with the static blast radius; accepts comma-separated changesets) |
| What code moves together with X? | `co_change` |
| What co-runs but has **no static link**? (hidden coupling) | `find_coupling` |
| Which live functions does no test execute? | `find_gaps` (fuses coverage with reachability: live-untested vs dead) |
| What order surfaces failures fastest? | `test_order` (greedy new-coverage-first; the prefix is a minimal cover) |
| Which tests are coverage-identical? | `redundant_tests` (review aid — parametrized tests share profiles legitimately; never auto-delete) |
| What's the always-on core? | `find_core` |
| Which tests do something nothing else does? | `find_outlier_tests` |
| Which files change often AND carry many behaviours? | `runtime_risk` (churn × behavioural centrality) |
| What gained/lost test exposure between two snapshots? | `coverage_drift` |

Dogfood example (this repo, `research/14`): 2,349 tests turn out to exercise **27
independent behaviours**; **64 tests** cover every executed function; the one
untested-dead function `find_gaps` reports is exactly the one `find_stale` flags
statically — and `find_coupling` located a real config↔envelope side-channel blind.

## For LLM agents (MCP)

stitchgraph is MCP-native: every operation above is an MCP tool with the same name
and the same JSON envelope. Launch the server pointed at a **built index** (build it
first with `reindex` — the server refuses to answer from a missing or never-indexed
database rather than confidently reporting an empty codebase):

```bash
pip install 'stitchgraph[mcp,treesitter]'
stitchgraph reindex /path/to/project --db /path/to/stitchgraph.db
stitchgraph-mcp --db /path/to/stitchgraph.db     # or env STITCHGRAPH_DB
```

Claude Desktop / Claude Code configuration:

```json
{
  "mcpServers": {
    "stitchgraph": {
      "command": "stitchgraph-mcp",
      "args": ["--db", "/absolute/path/to/stitchgraph.db"]
    }
  }
}
```

### Rules of engagement for agents

The full rule file — written to be dropped into an agent's context — is
[`AGENTS.md`](AGENTS.md). The essentials:

- **Query the graph before grepping.** `orient` first on unfamiliar code;
  `impact_of <name>` before editing anything; `get_callers`/`get_callees` instead of
  text search; `trace_path` for end-to-end flows.
- **Respect the envelope.** `needs_review: true` means *"unreached by my analysis"*,
  not *"proven dead"* — verify dynamic dispatch, plugins, and framework callbacks
  before acting. `confidence` and `provenance` (`extracted` > `inferred` >
  `ambiguous`) tell you whether a result is a fact or a ranked guess.
- **Never delete on `find_stale` alone.** It is precision-biased and advisory by
  design; treat results as candidates to verify.
- **Use `scan` for triage**, ordered by `urgency` (🔴 fix now / 🟠 look closer /
  🟢 cleanup); a finding capped 🟢 with `needs_review` rests on name-ambiguous edges
  and is likely a resolution artifact.
- **Prefer `select_tests` over "run everything"** when a coverage artifact exists —
  it returns the tests that *actually executed* the changed symbols.
- Refusals are honest: a bare-name collision, a too-broad `get_matrix` scope, or a
  missing index returns an explanation and a suggested next call, not a guess.

## Trust model

- **The envelope.** Every answer: `ok`, `result`, `confidence` (0–1), `provenance`
  (`extracted` = read from syntax; `inferred` = heuristic; `ambiguous` = several
  candidates), `needs_review` + human-readable reasons, and for findings an
  `urgency`. Provenance caps urgency — a heuristic link can never shout RED.
- **The cardinal rule.** Live code is never confidently flagged dead. Dozens of
  per-language liveness signals (exports, framework callbacks, dunders/magic
  methods, FFI/linker attributes, test conventions…) root the graph; ambiguity
  widens edges rather than dropping them. The deliberate trade-offs are documented
  — decision by decision — in [`LIMITATIONS.md`](LIMITATIONS.md).
- **Read-only, local, private.** No code leaves your machine; nothing executes;
  the only file written is the index (plus explicitly requested reports/kits).
- **Verified.** ~2,300 tests including differential oracles (streaming index ==
  in-memory, incremental == full reindex, GraphBLAS == pure Python), per-language
  completeness batteries, and ground-truthing against ~47 real projects (Linux
  kernel core, WordPress, Magento, NestJS…) with zero crashes. Hostile inputs
  degrade to a smaller index, never a wrong confident answer.

## Languages

| Depth | Languages |
|---|---|
| **Deep** (stdlib `ast`; optional jedi `--precise`) | Python 3.11+ |
| **Full graph via tree-sitter** (definitions, calls, imports/inheritance, tests, body matrix) | JavaScript, TypeScript/TSX, Go, Rust, C, C++, C#, Java, Ruby, PHP, Bash |
| **Type-grade upgrade via `--lsp`** (v3.46.0): an installed language server answers go-to-definition per call site, so the true target of an ambiguous name gains a confident edge — including picking the right *override* | TS/JS (typescript-language-server), Rust (rust-analyzer), Go (gopls), C/C++ (clangd); `[lsp.servers]` adds more |
| **Cross-language seams** | Flask/FastAPI/Django/Express/Spring routes, HTML forms, JS `fetch`, events, SQL (sqlglot), SQLAlchemy/Django ORM — all converging in one graph, so `trace_path` crosses language boundaries |

Per-language support matrix: [`docs/LANGUAGES.md`](docs/LANGUAGES.md).

## Scale

`reindex` streams the graph to SQLite in constant memory (auto-enabled for large
on-disk trees), byte-identical to the in-memory path and pinned by a differential
oracle. Measured:

- Magento (4,300 PHP files): **269 MB** peak instead of 3.2 GB.
- A homonym-fanout Python corpus producing **8.6M edges: 50 MB** peak (the pre-v3.28.0
  Python path materialized its edge list first — a field report of Home Assistant
  OOMing at 7 GB exposed it; fixed and now **gated in CI** by a hard-memory-cap test).
- Home Assistant 2024.3.3 (6,728 Python files, 59k nodes, 16.0M edges): the repo from
  the field report completes a clean end-to-end streaming reindex under a **4 GB
  address-space ulimit at 158 MB peak RSS** (~34 min).
- Query sweeps use a derived mmapped adjacency sidecar (`<db>.adjcache/`, built lazily
  on the first sweep, auto-invalidated by a generation counter): on the 16M-edge graph,
  `find_stale` drops from 119 s / 1.97 GB to **2.1 s / 516 MB** warm. Without numpy the
  sweeps stream from SQLite instead (~2 GB on the same graph).

Details: [`docs/V2_STREAMING_DESIGN.md`](docs/V2_STREAMING_DESIGN.md). Wondering
how long *your* repo will take? [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) has
measured anchors and an estimation method (edges drive cost, not files; watch the
db growth rate for a live ETA; a flat ~150 MB RSS with an active WAL is healthy).

## Develop

```bash
pip install -e '.[all,dev]'
PYTHONPATH=src python -m pytest -q
```

CI runs the suite on Python 3.11/3.12 plus a no-extras job that guards the
stdlib-only core. Design: [`docs/design.md`](docs/design.md) · capability map:
[`docs/OVERVIEW.md`](docs/OVERVIEW.md) · status/roadmap: [`docs/STATUS.md`](docs/STATUS.md)
· release history: [`CHANGELOG.md`](CHANGELOG.md) and `docs/RELEASE_NOTES_v*.md`
(campaign overview: [`docs/RELEASE_SUMMARY_v3.28-v3.38.md`](docs/RELEASE_SUMMARY_v3.28-v3.38.md);
field validation — the call graph measured at **99.1% recall** against Home Assistant's
real test-run coverage: [`research/18`](research/18-ha-pod-field-validation.md)) ·
review process: [`CONTRIBUTING.md`](CONTRIBUTING.md), [`REVIEW_HISTORY.md`](REVIEW_HISTORY.md).

MIT licensed.
