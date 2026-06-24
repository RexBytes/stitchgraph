# Changelog

All notable changes to stitchgraph. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [1.0.4] — 2026-06-24

**Field-fix patch — confidence honesty for receiver calls and structural findings
(issues #10, #11, #15).** Three reporting/confidence fixes from the field audit, none
changing reachability: every one keeps the cardinal invariant (live code is never flagged
dead). Confirmed by full three-model panels.

### Fixed

- **Receiver-based calls resolving to a single same-named symbol are now `INFERRED`, not
  `EXTRACTED`** (issue #10). A call like `obj.save()` whose name matches exactly one
  project definition was asserted at full `EXTRACTED` confidence — but without type
  inference the receiver's type is unknown, so it may be a homonym `save` on a different
  class. The edge is now labelled `INFERRED` (a guess), detected receiver-aware across all
  tree-sitter languages (member/field/selector/scoped access, plus Java `object` / Ruby
  `receiver` fields). **Weight is unchanged (1.0)**, so the edge still counts fully for
  reachability / `find_stale` — only the asserted confidence is lowered, never the
  liveness (cardinal-safe). Direct calls (`save()`) and constructors stay `EXTRACTED`.
- **`scan` structural findings now reflect the provenance of the edges they rest on**
  (issue #11). A cycle or god-object that exists *only* because of `AMBIGUOUS`
  (over-approximated homonym) or `INFERRED` (heuristic) edges was reported at the same
  🟠 urgency as one backed by confident `EXTRACTED` edges — on a language without type
  resolution this made most structural findings indistinguishable artifacts. Each cycle /
  god-object now carries a `confidence` and `needs_review` derived from its participating
  edges, reports the confident-only degree, and is capped to 🟢 (sinking in the ranking)
  when the coupling is dominated by name-ambiguous/heuristic edges. Confidently-linked
  findings keep their 🟠 "look closer."

### Changed

- **`LIMITATIONS.md`: corrected the `--precise` escape-hatch description** (issue #15). It
  no longer implies `--precise` "disambiguates" by pruning the `AMBIGUOUS` siblings; it
  documents the actual **additive** behaviour (adds a confident go-to-definition edge,
  never removes the competing candidates) and why that is deliberate — pruning a live
  symbol's only caller on a single jedi mis-resolution would be the cardinal sin.

## [1.0.3] — 2026-06-24

**Field-fix patch — offline-by-default grammars (issue #12).** `tree-sitter-language-pack`
changed its install model at v1.0.0: it stopped bundling grammars in the wheel and
switched to downloading them from a GitHub release on first use — which breaks offline /
CI / air-gapped installs and undercuts the "local-first" promise. The 1.0.1 `<2` bound did
not fix this (the download line is `<2`). Verified the bundled cutoff on PyPI (0.1.2–0.13.0
bundle; 1.0.0+ download) and that all 12 grammars stitchgraph uses are MIT-licensed.
Confirmed by a full three-model panel (JJ).

### Fixed

- **`pip install 'stitchgraph[treesitter]'` is offline / self-contained again** (issue #12).
  The `[treesitter]` extra now pins the **bundled** grammar line
  (`tree-sitter-language-pack>=0.7,<1.0`, `tree-sitter>=0.25.2,<1`), whose wheels ship the
  compiled parsers — no network at runtime. Verified end-to-end in a clean venv: all 12
  grammars load and a full multi-language reindex runs with the network off.

### Added

- **Opt-in `[treesitter-download]` extra** — the `1.x` line (smaller wheel + newest grammars,
  fetched over the network on first use) for users who want the latest grammars and have
  runtime network.
- **Adaptive grammar loader** (`_load_grammar`): loads a grammar the easiest available way —
  bundled loads directly; if missing and the installed pack supports it, downloads once and
  retries; a genuine failure still surfaces as the issue-#7 warning (file skipped, never a
  silent empty graph). Behaviour-preserving: the extraction graph is byte-identical.
- **`stitchgraph doctor`** (+ `--strict`) — a grammar self-check reporting the pack version,
  bundled-vs-download model, cache dir, and which supported grammars load. `--strict` exits
  non-zero if any can't load (a CI gate that the polyglot graph will be complete).

## [1.0.2] — 2026-06-24

**Field-fix patch — export-rooting + test call-receivers.** After 1.0.1 shipped, the
review panel was restored to full three-model diversity (opus + sonnet + haiku). The
freshly-returned **sonnet** immediately caught two cardinal false-deads that the
opus/haiku pair had missed across the entire 1.0.1 cycle, and the follow-up panels
surfaced a third. Fixed as a batch and confirmed by two consecutive full-diversity
clean panels (HH + II → readiness RELEASABLE). All three are the cardinal class — live
code flagged dead by `find_stale`. See the [release notes](docs/RELEASE_NOTES_v1.0.2.md).

### Fixed

- **JS/TS/CJS public exports are no longer flagged dead** (precision; panels FF/GG/HH).
  Only `export { X }` and inline `export class/function` were recognized, so a symbol
  defined and then exported separately was reported stale. Now the whole export-rooting
  class is closed: `export default Foo;` (the canonical React/Angular/Vue/Node idiom —
  pre-existing since 1.0.0), CommonJS `module.exports = Foo` / `module.exports = { A, B }`
  / `exports.x = Foo`, and TypeScript `export = Foo`. Matched precisely — anonymous
  defaults (`export default () => {}`) and locals named `exports` are not over-rooted, so
  genuinely-dead code still flags.
- **A class used only as a call receiver in a test block stays live** (precision; panel
  FF — a regression the 1.0.1 polyglot work introduced). In a call-based suite a class
  referenced as `Service.run` inside an RSpec/Jest `describe`/`it` block got a `CALLS`
  edge to the method but no reference to the class, so the live class was flagged dead.
  The test-file module scan (`_module_uses`) now collects name-references like the per-
  function scan does, and no longer descends into uncalled function-expression bodies
  (so a dead class referenced only inside an uncalled helper still flags).

## [1.0.1] — 2026-06-24

**Field-fix patch.** Three issues raised against 1.0.0 in real use on a Rust
crate (#7/#8/#9), plus — once #8 revealed the same test-detection gap in every
other language — a **polyglot generalization of test detection**. Fixed as a
batch and confirmed by nine review panels (W–EE). The cardinal invariant — live
code is never flagged dead — is preserved throughout; the panels found and closed
four successive cardinal gaps in cross-language test-class liveness (direct →
inherited/nested → combined fixed point → Python/tree-sitter `is_test_file`
asymmetry), with Panel DD declaring the class **closed** and **DD + EE giving two
consecutive clean panels at full diversity (the release gate)**. See the
[release notes](docs/RELEASE_NOTES_v1.0.1.md).

### Fixed

- **Idiomatic tests are recognized across all languages** (issue #8 generalized,
  precision; panels Y–DD). The root cause behind the Rust flood was universal:
  file-level test context never seeded the `test` role, so only the
  `test*`/`Test*` **name** convention did — flagging live tests (and the helpers
  they reach) dead in every language whose tests aren't name-convention. Now:
  **annotation/attribute** tests — Java `@Test`/`@BeforeEach`/… (JUnit/TestNG),
  C# `[Fact]`/`[Theory]`/`[Test]`/… (xUnit/NUnit/MSTest), PHP `#[Test]` (PHPUnit)
  — seed the `test` role; **call-based** suites with no named test function —
  JS/TS Jest/Mocha/Vitest, Ruby RSpec — root their `test()`/`it()`/`describe()`
  module-level call sites; and **test classes** are seeded transitively across
  nesting and inheritance (the JUnit abstract-base + thin-subclass idiom; pytest
  `class TestWidget:` / `unittest.TestCase`). `is_test_file` is now a single
  directory-aware heuristic shared by both extractors (a prior Python-vs-tree-sitter
  drift was itself a cardinal gap). A test helper/class reached by no test, and
  unused production code, still flag.
- **Rust inline unit tests no longer flood `find_stale`** (issue #8, precision).
  Idiomatic Rust tests live in `#[cfg(test)] mod tests { … }` with free-form
  names, so the `test*`/`Benchmark*`/`Example*` name convention never fired — the
  `#[test]` functions and every helper they reached were reported stale. The
  `#[test]` / `#[tokio::test]` (any `*::test`) attribute and the `#[cfg(test)]`
  module gate now seed the `test` role (a root). Matching is on the attribute
  **path**, not a raw `"test"` substring, so `#[cfg(feature="testing")]` and
  `#[doc="…test…"]` do **not** wrongly mark production code (which would *hide*
  dead code). A test helper reached by no test, and unused production code, still
  flag — consistent with a dead helper in any test file. Third-party runner macros
  (`#[rstest]`, `#[test_case]`) are a documented limitation (`LIMITATIONS.md`).
- **Grammar-load failures are surfaced, not swallowed** (issue #7). A tree-sitter
  grammar that can't load (offline/proxied environments, version drift) collapsed
  into a silent empty graph with a success exit. `treesitter.extract` now records
  the failure and emits a `RuntimeWarning` naming the affected languages and the
  number of skipped files; `extract_project` warns instead of a blanket swallow.
  Python extraction is unaffected either way; a normal run with grammars present
  emits no warning.
- **`impact_of` on an ambiguous name surfaces candidates and can be scoped**
  (issue #9). A bare common name (e.g. `get`) now lists the matching symbols in
  `alternatives` instead of a blank refusal, and the resolver accepts a fully
  qualified `Type.method` or a full `path::qual` id to scope to exactly one. The
  upgraded resolver also gives `get_callers` / `get_callees` / `trace_path` the
  same scoping; names that legitimately contain dots (`index.html`) still resolve
  directly.

### Changed

- **Dependencies bounded** (CONTRIBUTING lesson, prompted by issue #7):
  `tree-sitter>=0.22,<1` and `tree-sitter-language-pack>=0.1,<2`, so a future
  breaking major can't silently break extraction on a fresh install.

## [1.0.0] — 2026-06-23

**First stable release — precision, certified.** 1.0.0 is not a feature release;
it is the point at which the **release-readiness gate** is met: all hard gates
green (pytest / ruff / mypy / no-open-defects), **RRS 93.3 / 100**, and **two
consecutive full-diversity clean review panels**. The full trajectory is in
[`REVIEW_HISTORY.md`](REVIEW_HISTORY.md); the rubric in
[`RELEASE_READINESS.md`](RELEASE_READINESS.md). See the
[release notes](docs/RELEASE_NOTES_v1.0.0.md).

### Hardened

- **The cardinal invariant — live code is never flagged dead — is closed across
  every scope.** The dominant defect class through 22 review panels was a
  Python↔tree-sitter asymmetry where a live symbol used in some unmodeled way was
  reported stale. It is now resolved for by-name references, constructors (every
  language), type annotations, parameter default values, metaclass keywords, class
  bodies, public re-exports, and — closing the class — **defs nested in any host**:
  function bodies, class bodies, **control-flow blocks** (`if`/`for`/`while`/`try`/
  `with`/`match`), and **function-expression/arrow functions**. A shared
  `_scope_defs` traversal (Python) and full arrow-body recursion (tree-sitter) cover
  the complete, finite set of nesting hosts.
- **Metric integrity** — `_dedup_edges` collapses parallel and CALLS-subsumes-
  REFERENCES edges language-agnostically; `LIVENESS_RELATIONS` excludes
  query/read/write/ORM relations so cross-language edges don't inflate `fan_in` /
  PageRank; the GraphBLAS sweep and the pure-Python reference agree on thousands of
  random graphs (0 mismatches).
- **Envelope contract** — provenance gates the urgency ceiling on every operation;
  `find_stale` stays advisory (`needs_review`, name-based confidence); `ingest_trace`
  refuses when it grounds nothing; no operation returns `ok=True` with a vacuous
  result.
- **Regression suite** grew to **148 tests** (from 134), every review-panel finding
  pinned by a test confirmed to fail on the pre-fix code (non-vacuous).

### Notes

- No API changes from 0.4.0 — the 14 operations and three surfaces (library / CLI /
  MCP) are unchanged. Deferred, non-blocking polish tracked for a future release: SQL
  `MERGE` WRITES labelling; `find_holes` empty-list urgency. The **LSP backend** and
  **variable-granularity data flow** remain the two largest roadmap items
  ([`docs/STATUS.md`](docs/STATUS.md)).

[1.0.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v1.0.0

## [0.3.0] — 2026-06-23

**Depth & breadth.** Polyglot languages get first-class structure, the
cross-language web of frameworks widens, runtime fusion goes multi-language, and
the toolchain gets CI + packaging. See [`docs/STATUS.md`](docs/STATUS.md) for the
full table and the **Roadmap** of what's left.

### Added

- **Framework routes:** Django URLconf, Express (JS), and Spring (Java `@*Mapping`)
  join Flask/FastAPI — all produce `Route` nodes linked to handlers.
- **JS `fetch` → backend route:** client-side calls link to the matching route, so
  `trace_path` runs from a JS function through the backend to the DB table.
- **Events (EMITS/HANDLES):** `emit`/`publish` + `on`/`subscribe` create `Event`
  nodes, so `trace_path` crosses decoupled pub/sub boundaries.
- **Polyglot depth:** imports, inheritance (`INHERITS` from class heritage), and
  per-language test entry points for the tree-sitter languages; `impact_of` now
  flows through inheritance.
- **Framework-callback handling:** methods overriding an *external* base (e.g.
  `HTMLParser.handle_starttag`) are treated as roots — the last dead-code
  false-positive class is gone.
- **Multi-language runtime traces:** `ingest_trace` now reads coverage.py JSON,
  **LCOV** (JS/nyc, C/C++ gcov), and **Go coverprofiles**.
- **`summarize_subsystem`** operation; **`get_matrix`** small dense grid.
- **`watch`** command: re-index on file changes.
- **Pluggable embeddings:** `set_embedder()` for `find_similar` (token default;
  optional model2vec/sentence-transformers — no model bundled).
- **CI** (GitHub Actions) + **PyPI** publish workflow; SQLite schema migration for
  older index files.

### Notes

- The **LSP backend** (type-grade resolution) and **variable-granularity data
  flow** remain the two largest deferred items — see the Roadmap. `find_stale`
  stays advisory (`needs_review`); `--precise` (jedi) is the Python type-grade path.

### Tests

72 (up from 58): polyglot extraction, framework/event resolvers, multi-format
runtime traces, pluggable embedder, file-watching, schema migration, and the
precision/recall harness.

[0.3.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v0.3.0

## [0.2.0] — 2026-06-23

**stitchgraph goes polyglot.** The headline: a config-driven tree-sitter
extractor adds **11 more languages** alongside Python, all in one graph — so
dead-code, orientation, impact, and full-stack tracing now work across a
multi-language codebase.

### Added

- **Polyglot extraction (tree-sitter).** JavaScript, TypeScript/TSX, Rust, C,
  C++, C#, Go, Java, Ruby, PHP, and Bash — definitions (functions / methods /
  classes / structs / traits) and the call graph, in the same node/edge ontology
  as the Python `ast` extractor.
- **Config-driven languages.** Each language is a small `LangSpec` (node types →
  kinds, the call node + callee field). Adding a language is a spec, not new code.
- **Per-language resolution.** A JS call never binds to a Rust function of the
  same name; precision-biased (single match → confident, several → AMBIGUOUS to
  all, unknown → dropped as external).
- **Per-language entry-point roles.** `export` (JS/TS), `pub` (Rust), `public`
  (Java/PHP/C#), capitalised (Go), and `main` seed reachability.
- **Polyglot dispatcher.** `extract_project` merges Python + tree-sitter into one
  graph; a parse error in one language never breaks another.
- **[`docs/LANGUAGES.md`](docs/LANGUAGES.md)** — a language progress / support
  matrix (defs · calls · imports · inheritance · entry points per language).
- Packaging extras reorganised: `treesitter`, `precise` (jedi), `resolve`
  (sqlglot), `algebra` (graphblas), `all`.

### Notes / honest limits

- For the tree-sitter languages, **imports and inheritance aren't modelled yet**
  (calls still resolve cross-file by name, so dead-code/orient/trace work), and
  **entry-point detection is thin** (export/pub/public/capitalised/main) — so
  `find_stale` leans on `stitchgraph.toml` roots or honestly refuses without them.
- The hard part of any language is *type-correct* resolution (which `save` does
  `x.save()` mean?), not parsing — that's the deferred LSP upgrade.

### Tests

58 tests (up from 53), including a polyglot suite covering JS/Rust/Bash/Go/Java/
Ruby/PHP extraction, per-language call graphs, cross-language dead code, and the
no-cross-language-false-links guarantee.

[0.2.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v0.2.0

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
