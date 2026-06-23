# stitchgraph v1.0.0 — "Certified"

Local-first, MCP-native code intelligence. Point it at a codebase to find **stale
code, implementation holes, orientation, and impact** across 12 languages — ranked
by what's actually live, every answer carrying a confidence and a reason to
double-check. **Read-only on your code: it only ever writes to its own index.**

**This is the first stable release.** It is not a feature release — the 14
operations and three surfaces (library / CLI / MCP) are unchanged from 0.4.0.
1.0.0 marks the moment stitchgraph **earned** the release bar: a measurable,
auditable decision rather than a vibe.

> **The release gate (`RELEASE_READINESS.md`):** all hard gates green
> (pytest · ruff · mypy · no-open-defects) **AND** a Release-Readiness Score ≥ 90
> **AND** two consecutive full-diversity clean review panels.
>
> **At 1.0.0:** gates ✅ · **RRS 93.3 / 100** · coverage 86.4% · **clean streak 2**
> (Panels U + V, opus + haiku). `scripts/readiness.py` → **RELEASABLE**.

## Why 1.0.0 now

stitchgraph is a dead-code tool, so the one error that disqualifies it is **flagging
live code as dead**. Reaching 1.0.0 meant proving — adversarially — that it doesn't.
Twenty-two competitive multi-model review panels (A–V) hunted that failure across
every language and scope; each real finding was reproduced, fixed, and pinned with a
regression test that was itself verified to fail on the pre-fix code.

> **The cardinal invariant: precision over recall — a live symbol is never flagged
> dead.** When stitchgraph can't be sure, it lowers confidence and asks for review
> rather than asserting a destructive verdict.

## ✨ What this release certifies

### The "live code flagged dead" class — closed across every scope
A symbol can be *used* in ways that aren't a plain call. Every one is now modeled as
a reachable edge, in both the Python (`ast`) and tree-sitter extractors:

- **By-name references** — callbacks (`register(handler)`), `Color.RED`,
  `Widget.create()`, value assignments.
- **Constructors in every language** — `new Foo()` (JS/TS/C#/C++), `Service.new`
  (Ruby), `new Service` (PHP), `__construct` and class-named constructors; and
  constructing a class reaches its `__init__`/`__new__`/`__post_init__`.
- **Type annotations**, **parameter default values**, **metaclass keywords**, and
  **class-body references** (dispatch tables, class-level attributes/annotations).
- **Public re-exports** (`from .api import Public` in a package `__init__`) and
  **public classes / framework-callback overrides**.
- **Defs nested in *any* host — the class fully closed in 1.0.0.** A function or
  class defined inside a function body, a class body, a **control-flow block**
  (`if`/`for`/`while`/`try`/`with`/`match`), or a **function-expression/arrow
  function** is now modeled as a real node with an enclosing→nested containment edge,
  so a symbol used only there stays live when its host is reachable. The set of
  nesting hosts is finite (lambdas and comprehensions can't contain defs) and now
  completely covered, via a shared `_scope_defs` traversal in Python and full
  arrow-body recursion in tree-sitter.

### Metric integrity
`fan_in`, PageRank, hub ranking, and `get_matrix` don't double-count: parallel edges
are de-duplicated, `REFERENCES` self-loops dropped, a `CALLS` edge subsumes a
redundant `REFERENCES` edge, and `LIVENESS_RELATIONS` excludes query/read/write/ORM
relations so cross-language edges never inflate centrality. The GraphBLAS sweep and
the pure-Python reference agree on thousands of random graphs (0 mismatches).

### Honest envelope, off the happy path
Provenance gates the urgency ceiling on every operation; `find_stale` stays advisory
(`needs_review`, name-based confidence); `ingest_trace` refuses when it grounds
nothing; config is read from the indexed project root, not the cwd; SQLite migration
covers old index files. No operation returns `ok=True` with a vacuous result.

### A test suite you can trust
**148 tests** (up from 134), including one regression test per review-panel finding.
In Panel U both reviewers ran a **test-quality audit** and confirmed the suite is
non-vacuous — the nesting regression tests were shown to *fail* when their fix is
removed, then pass when restored.

## Compatibility

No API changes from 0.4.0. Existing index databases migrate forward automatically.
Core stays stdlib-only; polyglot/algebra/resolver/MCP features remain optional
extras.

## Known limitations & roadmap

- **Documented tradeoffs** (`LIMITATIONS.md`): module-level uses (a class referenced
  only in a module-level table) aren't attributed; `find_stale` is advisory at 0.6
  (0.78 with a runtime trace); `replace_file` incremental update is experimental.
- **Deferred non-blocking polish** for a later release: SQL `MERGE` WRITES labelling;
  `find_holes` urgency on an empty result.
- **Largest roadmap levers** (`docs/STATUS.md`): an **LSP backend** (type-grade,
  multi-language resolution) and **variable-granularity data flow**.

## Install

```bash
pip install 'stitchgraph[all]'      # everything
# or pick: stitchgraph[cli,treesitter,algebra,resolve,precise,mcp]
```

## Reproduce the release decision

Everything needed to audit or resume the hardening campaign lives in the repo:
`CONTRIBUTING.md` (methodology), `RELEASE_READINESS.md` (the rubric),
`scripts/readiness.py` (the RRS/convergence scorer), `REVIEW_HISTORY.md` (the full
A–V panel trajectory), and `review-kit/panel_prompt.template.md` (the panel brief).

```bash
pip install -e '.[all,dev]'
python scripts/readiness.py        # → RELEASABLE
```

**Full changelog:** [`CHANGELOG.md`](../CHANGELOG.md)
