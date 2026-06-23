# stitchgraph v0.4.0 — "Precision"

Local-first, MCP-native code intelligence. Find **stale code, implementation
holes, orientation, and impact** across a multi-language codebase — ranked by
what's actually live, every answer carrying a confidence and a reason to
double-check. **Read-only on your code.**

v0.2.0 made stitchgraph polyglot; v0.3.0 made it deeper and wider. **v0.4.0 makes
it trustworthy.** This release is the result of a sustained, adversarial hardening
campaign against the one error that matters most for a dead-code tool: **flagging
live code as dead.** Seventeen competitive multi-model review panels (A–Q) hunted
that failure across every language and scope; each finding was reproduced, fixed,
and pinned with a regression test.

> **The cardinal invariant: precision over recall — a live symbol is never flagged
> dead.** When stitchgraph can't be sure, it lowers confidence and asks for review
> rather than asserting a destructive verdict.

## ✨ Highlights

### The "live code flagged dead" class — closed, axis by axis
A symbol can be *used* in many ways that aren't a plain function call. Every one of
these is now modeled as a reachable edge, so the symbol stays live:

- **By-name references** — a function passed as a callback (`register(handler)`), a
  class accessed as `Color.RED` or `Widget.create()`, a value assigned by name.
- **Constructors in every language** — `new Foo()` (JS/TS/C#/C++), `Service.new`
  (Ruby), `new Service` (PHP), `__construct`, and class-named constructors
  (Java/C#/C++); and constructing a class now reaches its `__init__`/`__new__`/
  `__post_init__`.
- **Type annotations** — a class used only as `def f(x: Config) -> Result`.
- **Parameter default values** — `def f(strategy=Strategy, cb=handler)`.
- **Metaclass / class-definition keywords** — `class X(metaclass=Meta)`.
- **Class-body references** — class-level attributes, dispatch tables
  (`TABLE = {"a": handle_a}`), and class-level annotations.
- **Function-local classes & closures** — a symbol used only inside a nested
  class/closure reachable from a live entry point.
- **Public re-exports** — `from .api import Public` in a package `__init__` is live
  public API, not dead code.
- **Public classes & overrides** — PHP public classes and methods overriding an
  external framework base are roots.

### Sharper metrics
Parallel-edge de-duplication, dropped `REFERENCES` self-loops, and a rule where a
`CALLS` edge subsumes a redundant `REFERENCES` edge — so `fan_in`, PageRank, hub
ranking, and `get_matrix` no longer double-count the same relationship.

### Correct off the happy path
Found by reviewing in a **core-only environment** (stdlib + SQLite, no extras):

- **Config is loaded from the indexed project root**, not the current working
  directory — operations run from anywhere now honor the right roots.
- **`ingest_trace` refuses** (rather than claiming success) when a runtime trace
  grounds nothing.
- **SQLite migration** covers both `nodes` and `edges` tables, with index creation
  ordered after migration so old index files keep working.

### Tooling & process
- **`ruff` + `mypy` gates**, both clean across the source tree.
- **The release-review kit is in the repo** — methodology (`CONTRIBUTING.md`), the
  release rubric (`RELEASE_READINESS.md`), the RRS/convergence scorer
  (`scripts/readiness.py`), the live panel trajectory (`REVIEW_HISTORY.md`), and a
  ready-to-run panel-prompt template (`review-kit/panel_prompt.template.md`), so the
  hardening campaign is fully reproducible and resumable.

## A note on the version

This is a **0.4.0 progress marker**, not the 1.0.0 release. The release gate
(documented in `RELEASE_READINESS.md`) requires all hard gates green, a
Release-Readiness Score ≥ 90, **and two consecutive full-diversity clean review
panels**. The hunt continues toward that bar; the version will read `1.0.0` only
when it is genuinely met.

## What's left (roadmap)

Unchanged from v0.3.0, and still the two largest levers:

- **LSP backend** (type-grade, multi-language resolution) — lifts the accuracy
  ceiling and would push `find_stale` past its name-based 0.6 confidence.
  `--precise` (jedi) is the Python path today.
- **Variable-granularity data flow** — unlocks non-global data loops and argument
  provenance/taint.

Module-level uses (e.g. a class referenced only in a module-level table) remain the
one documented attribution limitation — see [`LIMITATIONS.md`](../LIMITATIONS.md).

## Install

```bash
pip install 'stitchgraph[all]'      # everything
# or pick: stitchgraph[cli,treesitter,algebra,resolve,precise,mcp]
```

## Tests

133 tests (1 skipped), including a **50-test regression suite — one test per review
finding** — plus polyglot extraction (Python + 11 languages), the framework / event
resolvers, multi-format runtime traces, schema migration, a property-based
GraphBLAS-vs-pure-Python agreement check, and a precision/recall harness asserting
the never-flag-live-code-as-dead stance. Verified clean under both the full
`[all,dev]` matrix and a core-only environment.

**Full changelog:** [`CHANGELOG.md`](../CHANGELOG.md)
