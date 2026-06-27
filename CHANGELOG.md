# Changelog

All notable changes to stitchgraph. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [2.1.3] — 2026-06-27

**Rust FFI/linker-export cardinal fix, from doc-driven hunting.** Reading each language's
reference enumerates its *complete* implicit-invocation surface (magic methods, operator
overloads, FFI exports, reflection hooks) — gaps a repo only surfaces if it happens to exercise
them. The Rust reference's "the `no_mangle` attribute" / `export_name` made a gap visible that no
scanned repo had hit: a non-`pub` `#[no_mangle]` export. A minimal fixture confirmed it was a
cardinal false-positive (panel R69).

### Fixed

- **Rust `#[no_mangle]` / `#[export_name]` functions are no longer flagged dead.** These
  attributes export the function's symbol to the linker / foreign (C) code regardless of `pub`
  visibility — the function is a public-ABI entry point with no in-tree caller (the Rust analogue
  of C's `EXPORT_SYMBOL`). A `pub fn` was already export-rooted, but a non-`pub`
  `#[no_mangle] extern "C" fn` (valid Rust — the symbol is still exported) had no `pub` to trigger
  that rooting, so it *and everything its body reached* were false-flagged dead (cardinal). Such
  functions are now rooted `exported`. Covers the bare form *and* the wrapped forms —
  `#[unsafe(no_mangle)]` / `#[unsafe(export_name = "…")]` (the **required** spelling in the Rust
  2024 edition; panel R70) and `#[cfg_attr(<pred>, no_mangle)]` (conditionally applied). Cardinal-safe
  (only adds roots). A `_is_rust_export_attr` helper isolates the attribute test; owned by
  regression tests and pinned by mutation.

## [2.1.2] — 2026-06-26

**C# custom-attribute cardinal fix, from continuing the cross-language hunt** (jackson-core,
mockito, okhttp, serilog). Most findings were the documented external-framework-annotation
limitation (ByteBuddy `@Advice.*` on mockito's advice methods, Moshi `@ToJson`/`@FromJson` on
okhttp's sample adapters — unrecognised framework annotations, now cited in LIMITATIONS).
serilog surfaced a clean, general extraction bug.

### Fixed

- **C# in-tree attribute classes used via `[Foo]` are no longer flagged dead.** C# applies an
  attribute with the `Attribute` suffix omitted — `[NoEnumeration]` names class
  `NoEnumerationAttribute` — so the bare reference never resolved, and a custom attribute class
  used only via `[Foo]` was false-flagged dead (serilog `NoEnumerationAttribute`, applied in
  `Guard.AgainstNull`; panel R64, cardinal). An `attribute` usage now also emits the suffixed
  reference (`Foo` → `FooAttribute`), so the attribute class is kept live. Cardinal-safe (adds a
  reference only; the suffixed name resolves only if such a class exists). Covered in both
  `_direct_refs` (attributes on methods/classes/structs) and `_module_uses` (attributes on
  `enum`/`delegate` declarations, which aren't in the C# `defs` set; panel R66). Owned by
  regression tests.

## [2.1.1] — 2026-06-26

**Ruby operator-method cardinal fix, from dogfooding across a Rust/Go/Ruby hunt** (serde, clap,
gorm, cobra, gin, logrus, grape). Go and Rust came back clean (0 cardinal false-positives;
serde's library is fully live, only test-suite/trybuild fixtures flag). Ruby surfaced a real
one.

### Fixed

- **Ruby operator methods (`def []`, `def []=`, `def <=>`, `def ==`, `def <<`, …) are now
  captured and rooted.** Their name node is tree-sitter type `operator`, which `_trailing_id`
  didn't recognize — so the method was dropped from the graph entirely. Two consequences, both
  fixed: (1) the operator method was un-navigable / un-analyzable; (2) anything used *only*
  inside its body was false-flagged dead — e.g. in grape, `def []=` does `ValueArray.new(value)`,
  so `ValueArray`'s constructor was flagged dead though the class is instantiated (cardinal,
  panel R61). Operator methods are invoked through operator/index **syntax** (`a[k]`, `a[k]=v`,
  `a <=> b`, `sort`), never a by-name call, so they're rooted as `callback` — the Ruby analogue
  of the existing C++ special-member pass. Cardinal-safe (only adds roots). Owned by a
  regression test.

## [2.1.0] — 2026-06-26

**Constant-memory *queries*, from dogfooding v2.0.1 across a multi-repo Python hunt** (Django,
Salt, Ansible, CPython stdlib, Home Assistant). v2.0.0 made *indexing* memory-bounded; this
release extends that to the *reachability* sweeps so `find_stale` / `impact_of` scale to the
graphs the indexer can now build.

### Changed

- **Reachability streams its adjacency (the find_stale scalability ceiling).** Home Assistant
  indexed fine — 6,728 files, **~16M edges**, ~4 GB — but `find_stale` then OOM'd: every
  reachability/centrality sweep funnelled through `Store.resolved_edges()`, which does
  `SELECT * … fetchall()` and builds **all 16M `Edge` objects at once**. A new lean
  `Store.iter_resolved()` streams `(src, relation, dst_id, weight)` tuples cursor-by-cursor;
  `algebra._Adjacency` and the `reach.py` sweeps (`reachable_from`, `reverse_reachable_from`,
  `fan_in`/`fan_out`, `best_path`, SCC) build directly from it. Byte-identical results (the
  GraphBLAS==pure-Python and incremental/streaming oracles stay green); peak on a 6M-edge graph
  dropped to ~840 MB (was multi-GB), so a 16M-edge graph now queries in ~2 GB instead of OOM.

### Fixed

- **SQL resolver no longer treats prose as SQL.** `_sql_literals` matched any string whose
  *first word* was a SQL verb, so English docstrings — `"Create a list…"`, `"Update the…"`,
  `"Delete a…"`, `"With this…"` — were handed to sqlglot, flooding output with parse warnings
  (hundreds per file on Django/Salt) and occasionally minting phantom tables. It now requires
  real statement structure (`SELECT … FROM`, `INSERT INTO`, `UPDATE … SET`, `DELETE FROM`,
  `CREATE <table|index|view|…>`, `WITH … AS … SELECT`). Real queries still resolve; prose
  doesn't. Owned by a regression test.

### Docs

- **LIMITATIONS:** documented plugin-loader / string-name dynamic dispatch (Salt's loader,
  pluggy, entry-point registries) as a static-analysis blind spot — on Salt 3008, `find_stale`
  flags 3,907 loader-dispatched functions that are live at runtime. Escape hatch: pin the public
  surface via `[entry_points]` globs or `ingest_trace`.

## [2.0.1] — 2026-06-26

**PHP precision fix from dogfooding v2.0.0 on Magento.** Running `find_stale` on the Magento
Framework surfaced a cardinal-class false-positive: methods invoked through PHP's
**`[$this, 'method']` callable-array idiom** (the `usort` / `uasort` / `preg_replace_callback`
comparator pattern) were flagged dead, because the method name is a *string*, not a syntactic
call, so the call scan never saw it.

### Fixed

- **PHP array callables are now recognized** (tree-sitter extractor). A 2-element callable
  array `[$this|self::class|static::class|'Class'|$obj, 'method']` — the `usort` / `uasort` /
  `preg_replace_callback` / `array_map` comparator idiom — emits a REFERENCES edge to `method`.
  Cardinal-safe — only project symbols resolve, so a non-callable 2-element array that happens
  to name a method merely over-roots (masking dead code), never causing a false-dead. On the
  Magento Framework this removed 9 false-positive dead-flags (39 → 30 PHP candidates) while
  still flagging genuinely-unused private methods. Byte-identity (streaming == full) is
  preserved — the new edges flow through the shared extractor path. Owned by a regression test.
  (A `'Class::method'` *string* callable needs no handling: a string static call requires a
  PUBLIC target, which is already rooted as exported.)

## [2.0.0] — 2026-06-26

**Constant-memory streaming indexer.** The major feature of v2: `reindex` can now stream the
graph straight to SQLite instead of building it all in Python first, so peak memory tracks
one file's working set — not the size of the whole repo. Tens-of-thousands-of-file monorepos
(Magento, 24k PHP files) that used to need >12 GB and OOM now index on a laptop. The streamed
index is **byte-identical** to the in-memory one, pinned by a differential oracle across
Python + JS/TS + Go + Ruby + C/C++ + Rust + PHP.

Measured on the Magento `Framework` core (4,304 PHP files → 30,412 nodes, ~15.5M raw edges):
**reindex peak 3,183 MB → 269 MB (~12×), output identical** (3,926,345 edges / 30,412 nodes
verified row-for-row), ~40% slower.

### Added

- **`reindex(store, path, streaming=...)`** — tri-state:
  - `None` (default) — **AUTO**: streams when the store is on-disk AND the tree is large
    (≥ 2,000 indexable source files); small repos keep the slightly faster in-memory path.
  - `True` / `False` — force streaming / in-memory.
  Exposed on the CLI (`--streaming/--no-streaming`) and MCP. Streaming saves memory only with
  an on-disk `Store` (a `:memory:` DB holds rows in RAM regardless), so AUTO never picks it
  for `:memory:`.
- **Streaming differential oracle** (`tests/oracles/test_streaming_differential.py`) — the
  release gate for the streaming path: `streaming == full`, byte-for-byte, on a polyglot
  corpus plus a heavy-fan-out / cross-group stress fixture.
- **`scripts/mutate.py --only <names>`** — restrict the mutation meta-oracle to specific
  functions/classes, so the streaming-critical core is pinned by a fast, targeted kill-signal.

### Changed

- **How extraction streams (internal).** Each file's AST / tree-sitter parse-tree **and**
  source bytes are dropped after pass 1 (only a tiny per-definition record survives into pass
  2); edges are deduplicated per-source on the fly and written to SQLite in committed batches,
  so neither the parse trees nor the millions of edges are ever all resident at once. Output
  is unchanged. `extract_project` / `treesitter.extract` gained internal `cache_asts` /
  `cache_trees` / `edge_sink` parameters; their public `(nodes, edges)` return is unchanged.
- **`Node` / `Edge` use `__slots__`** — lower per-object overhead at scale.

### Fixed

- **`name_based` consistency between the in-memory and store dedups (panel R50).** The
  in-memory `_dedup_edges` now ORs the `name_based` flag onto a `(src, relation, dst_id)`
  group's survivor — matching the store's `_dedup_resolved_edges` (the R23A rule). Without
  this, `reindex(precise=True, streaming=True)` could diverge from `streaming=False` on
  `name_based` (jedi's precise edge and the extractor's name-based edge to the same target
  land in different sink groups, so the store's OR fires while the in-memory path kept the
  precise survivor's flag). Cardinal-safe: a pure-precise group still keeps `name_based=False`,
  so a precise resolution is never wrongly made re-widenable (R22A). The streaming differential
  oracle now compares `name_based` (and `weight`/`provenance`) and includes a `precise=True`
  (jedi) case.

### Notes / trade-offs

- Streaming reindex commits in batches rather than as one transaction, so a crash mid-rebuild
  can leave a partial index; a re-run rebuilds cleanly (it clears first). The default
  in-memory path remains crash-atomic. AUTO only engages streaming for large on-disk repos —
  exactly where the in-memory alternative is an OOM.
- The previous top deferred limitation ("very large monorepos indexed as one in-memory
  graph") is **resolved**; see `LIMITATIONS.md` and `docs/V2_STREAMING_DESIGN.md`.

## [1.0.7] — 2026-06-26

**Precision release from a multi-repo / multi-language false-positive hunt.** stitchgraph
was run against ~47 real-world projects across 9 languages — including code *designed* to
break parsers (IOCCC obfuscated C), and large/messy corpora (Linux kernel core, WordPress,
Magento, PrestaShop, symfony) — to ground-truth `find_stale` against actual liveness. The
hunt surfaced a family of **cardinal-class false-deads** (live code flagged dead) caused by
entry-point/liveness signals stitchgraph did not yet model. Every fix only ever *adds* roots
(precision-safe). **Robustness held: 0 crashes across all corpora** (the 1.0.6 RecursionError/
FIFO/large-file guards survive obfuscated and machine-generated C).

### Added (new entry-point / liveness signals — all cardinal-safe)

- **`setup.cfg [options.entry_points]`** parsed alongside `pyproject.toml` — `console_scripts`,
  `gui_scripts`, and plugin groups (e.g. `flake8.extension`/`flake8.report`); class targets
  root their public methods too.
- **src-layout (`src/`) absolute-import resolution** — a PyPA `src/pkg/…` project's absolute
  imports (`from pkg import …`) now resolve (incl. **PEP 420 namespace packages**, no
  `__init__.py`), so module-load-only-live code isn't flagged dead. Node ids are unchanged;
  the module lookup gains a src-stripped alias.
- **Inherited public methods of an exported class** are rooted (a base-class method like
  `Flask`'s `shell_context_processor`, used on an instance, is public API).
- **Framework callbacks across more languages:** Java/C# reflection **annotations/attributes**
  (`@PostConstruct`, `[OnSerializing]`, …); JS/TS **decorators** (NestJS/Angular/TypeORM —
  `@Controller`/`@Get`/`@Entity`/…); transitive & self-named external-base callback classes
  (`FlaskGroup→AppGroup→click.Group`; `EnvironBuilder(werkzeug…EnvironBuilder)`); Ruby
  `const_missing`/`const_added` and more implicit hooks.
- **C/C++ `EXPORT_SYMBOL(...)`** roots the named function as public kernel/module ABI (the C
  analogue of `__all__`/`module.exports`).
- **JS/TS member-assigned functions** (`app.render = function(){…}`, `X.prototype.m = …`,
  `module.exports.x = …`) are modeled and their bodies walked, so helpers they call aren't
  flagged dead; module-scope ones are rooted (function-nested ones stay reachability-gated).

### Changed

- **Skip dependency/vendored/build dirs** when indexing — one shared set across both
  extractors: `node_modules`, `vendor`, `third_party`, `bower_components`, `target`, `.gradle`,
  plus the existing `.venv`/`build`/`dist`/`.git`/… (conventionally non-first-party).
- Don't extract a **bodyless C/C++ struct/enum/union** as a phantom dead class.

### Notes

- **Documented scalability limit:** `reindex` builds one in-memory graph, so peak RAM scales
  with repo size — a tens-of-thousands-of-file monorepo (Magento, 24k files) exceeds ~12 GB;
  index sub-trees or provision RAM. (A streaming/constant-memory indexer is the next big item.)
- New documented cardinal-safe over-rooting tradeoffs (flat-name export collisions; member-
  assigned methods) — see `LIMITATIONS.md`.

**Hardening rounds (R40–R42).** A full-diversity panel campaign (opus/sonnet/haiku) over the
session's diff, with the three-layer gate (adversarial panel + `tests/oracles/` + mutation
meta-oracle). The panels found and fixed, at root cause, 4 over-rooting/recall defects the
features introduced (R40A script-class over-root; R40B/R41A comment dropping a decorator/
attribute marker across JS/TS **and** Rust; R40C member-assignment-in-dead-function rooting;
R42A namespace-package src-layout false-dead; R46A member-assigned-class methods flagged
dead) — each owned by a regression test, and the src-layout incremental defect class newly
owned by the differential oracle. Released on **two consecutive full-diversity
(opus+sonnet+haiku) clean panels (R47–R48)**; `scripts/readiness.py` confirms RELEASABLE. The
maintainer applies the tag.

## [1.0.6] — 2026-06-25

**Field-fix patch — entry-point coverage (#20/#21/#22) that grew into a robustness +
cross-language cardinal-hardening release.** Under sustained multi-model adversarial-panel
review, the entry-point work surfaced a family of cardinal-class false-deads (live code
flagged dead) across the tree-sitter languages — Python↔tree-sitter rooting asymmetries and
grammar/extension edge cases — plus crash/hang hardening (FIFO/special files, malformed
coverage JSON and `stitchgraph.toml`, deep-AST `RecursionError`) and a documentation
correction. The cardinal fixes only ever *add* roots (precision-safe). Confirmed by full
three-model panels.

**Final hardening rounds (R33–R39).** A deeper full-diversity panel campaign (opus×2 ·
sonnet×2 · haiku×2 per round) plus a three-layer release gate — adversarial panel +
deterministic oracle suite (`tests/oracles/`) + in-house mutation meta-oracle
(`scripts/mutate.py`) — drove the last veins of the cardinal/inflation classes to closure,
each owned by a regression test or oracle so future panels don't re-spend budget:

- More language execution-model rooting: Ruby/PHP module-level scripts, Go package-directory
  `var`/`init` initializers, C++ translation-unit static initializers (reachability-driven),
  and the module/symbol id-collision case (`Service.js` + `class Service`).
- `replace_file` incremental convergence: runtime-role preservation, language-aware name
  resolution (no cross-language bind), and exact cross-file `exported`-role convergence via a
  new `exported_ids` parameter.
- Provenance-demotion completed across the column (`trace_path` joined
  `impact_of`/`get_callers`); `get_matrix`/`summarize_subsystem` id-boundary scoping;
  coverage path-suffix and bool-as-int fixes; envelope non-finite clamp + `_plain` covering
  all result/meta values.

Released on a **two-consecutive-3-layer-clean** gate (rounds R38–R39: panel clean + oracle
suite green + mutation clean), RRS 93.3/100. The maintainer applies the version tag.

### Fixed

- **CARDINAL: a tree-sitter framework-subclass is no longer flagged dead while its methods
  are live.** `_seed_callback_roles` (tree-sitter) marked callback *methods* with the
  `callback` role but never marked the enclosing *class* — the Python extractor's
  `_apply_callback_roles` has a `classes_with_callbacks` second pass that the tree-sitter
  side was missing (a Python↔tree-sitter symmetry gap). So a framework subclass in any
  tree-sitter language (a Rails `ApplicationController`, a React `Component`, …) that wasn't
  otherwise exported or constructed had its **class** surface as dead code while its hook
  methods stayed live — the only release-blocking class of bug (live code flagged dead). The
  class-rooting pass is now mirrored. Tie is to *having* callback methods, so a bare unused
  subclass with no overrides still flags.
- **CARDINAL: C/C++ in-class member functions now root their class.** C/C++ map every
  `function_definition` to FUNCTION (there is no separate method node), so the five
  method-based class-rooting passes (exported / test / callback / main / constructor), which
  key on METHOD, silently skipped every C++ method — a live Qt/framework subclass and its
  methods were flagged dead. In-class member functions are now normalized to METHOD, so all
  five passes work for every language.
- **CARDINAL: a C# `internal` entry-point class is no longer flagged dead.** Idiomatic C#
  (`internal class Program { static void Main }`) has a non-public `Main`, so the class never
  gets the `exported` role, and the tree-sitter extractor had no pass to root the enclosing
  class of a `main`-role method (the Python extractor does). A new `_seed_main_classes` pass
  mirrors the Python rescue.
- **CARDINAL: public interface/trait members are no longer flagged dead.** Members of an
  exported interface/trait are public API but, being implicitly public (no visibility token),
  never got a per-method `exported` role, and the down-propagation pass was gated to JS/TS —
  so a `pub trait` (Rust), `public interface` (Java/C#), or interface/trait (PHP) member,
  including body-bearing `default` methods, was flagged dead. A new
  `_seed_exported_interface_methods` pass down-propagates `exported` from the container.
- **CARDINAL: a C++ class in a `.h` header is no longer flagged dead.** `.h` was mapped to
  the C grammar, which has no class/namespace/template — so a C++ class in a `.h` header
  (the dominant C++ header extension; header-only and split header/source layouts are
  ubiquitous) mis-parsed and surfaced as dead code. `.h` is now resolved to C or C++ by
  content.
- **CARDINAL: a module node colliding with a same-named symbol no longer loses its role.**
  A bash `run.sh` defining `run()`, or a JS test file `tests/Service.js` defining
  `class Service`, produced two nodes with the same id; the store's `INSERT OR REPLACE`
  dropped the MODULE node and with it its module-only role (`script`/`test`, which has no
  redundant assignment), flagging the whole file's code dead. A shadowed module node's roles
  are now merged into the surviving symbol node.
- **CARDINAL: a C++ class/struct in a `.h` header used from a `.cpp` is no longer flagged
  dead.** C and C++ were separate name-resolution buckets, but real projects reference symbols
  freely across `.h`/`.c`/`.cpp` (and a `.h` may be parsed under either grammar), so a header
  type used by the other dialect never resolved. C and C++ now share one resolution bucket
  (precision-safe — it only adds edges). The `.h`→C/C++ content sniff was also broadened
  (access specifiers, `virtual`/`operator`/`nullptr`) so struct-with-methods headers route to
  C++ and their members are extracted.
- **CARDINAL: Rust trait-impl methods are no longer flagged dead.** A method in an
  `impl Trait for X` block can't carry `pub` and is invoked via language sugar
  (`Display::fmt` via `{}`, `Iterator::next` via `for`, operator overloads) with no call node,
  so it got no `exported` role and no inbound edge. A new `_seed_trait_impl_methods` pass roots
  trait-impl methods (a bare inherent `impl X` still flags).
- **A JS/TS `export { X }` no longer roots a same-named symbol in another language**
  (a dead Ruby/Go/… `X` was hidden by an unrelated JS re-export). The reexport pass is now
  language-guarded — a precision (false-negative) fix, not cardinal.
- **CARDINAL: a class with any reachable member is never flagged dead.** General invariant
  (a live method implies a live class — the class must exist for the method to run) added to
  `find_stale`'s candidate filter, as the backstop for the whole "class dead while a member
  is live" family across every language/idiom (callback/main/exported/interface/trait, and
  C# `partial class` parts split across files). A class is flagged only when it AND all its
  members are unreached. (Side effect: stitchgraph's own `ConfigOnlyDetector` is now kept live
  — its `detect` is reachable via the `EntryPointDetector` protocol call — so dogfood is now
  2 advisory, was 3.)
- **CARDINAL: C# top-level statements root their local functions.** A `.cs` file using the
  default .NET 6+ top-level-program form (`Program.cs` with no explicit `Main`) is the
  program's entry point, like bash's top-level body and Python's `__main__`; its local
  functions were flagged dead. Such files are now rooted as a `script`.
- **`find_symbol`/`impact_of`/`trace_path`/`get_callers`/`get_callees` no longer crash on a
  non-UTF-8 symbol name.** A lone surrogate (invalid-UTF-8 argv decoded via `surrogateescape`)
  or embedded NUL bound into SQLite raised `UnicodeEncodeError`/`ValueError`; the store lookups
  now refuse (no match) so the op returns a `Result` instead of a traceback.
- **A resolver can no longer abort `reindex`.** Resolvers are heuristic enrichment, but a
  `DELETE TABLE …` SQL string in analyzed source made sqlglot hand the SQL resolver a `bool`
  `.this`, raising `AttributeError` and crashing the whole reindex. `run_resolvers` now skips
  a resolver that raises (the base graph + other resolvers are unaffected), and the SQL
  resolver guards on `Expression` before walking. A repo with stray SQL no longer fails to
  index.
- **`ingest_trace` no longer OOMs on a corrupt Go coverprofile.** A line with a huge end-line
  expanded `range()` into a multi-GB set; the span is now bounded (>1M lines dropped),
  matching the JSON/LCOV "empty on any problem" hardening.
- **Path-taking ops refuse instead of crashing on a hostile path.** An over-long path,
  embedded NUL, or lone surrogate passed to `reindex`/`ingest_trace`/`risk` (or read by
  `find_config`/`load_coverage`) raised `OSError`/`ValueError`/`UnicodeError` from a
  `stat()`/`resolve()`/bind. `reindex` now degrades to an empty index (like a missing path);
  the others refuse cleanly.
- **A malformed `[review] threshold` no longer silently disables review.** `threshold = "nan"`
  (or out-of-range) made `confidence < nan` always False; it now clamps to the default.
- **`reindex` no longer aborts on a pathologically deep source file.** A huge flat
  expression (`X = a + b + c + …`, realistic in generated SQL/HTML/string-builder code)
  overflows the recursive AST walk with `RecursionError`, which was not in the per-file
  `except (SyntaxError, UnicodeDecodeError, OSError)` and ran outside the `try` — so a single
  bad file aborted the **entire** reindex and left an empty DB. `RecursionError` is now
  caught per file in both extractors and the resolver `parse()` helper, honouring the
  "skip the one file, never abort the whole reindex" contract. The route resolvers
  (express/jsfetch/spring) run their **own** recursive descent over a tree-sitter tree,
  bypassing that helper, so `run_resolvers` now also guards each resolver — a deep `.js`
  expression degrades to "no extra edges" instead of aborting the reindex. (Library hygiene:
  the SCC passes that raise `sys.setrecursionlimit` now restore it; the tree-sitter
  RecursionError skip no longer leaves an orphan module node.)
- **`[project.scripts]` / `[project.entry-points]` console entry points are detected as
  roots** (issue #21). `design.md` §4 lists them as roots and `PythonLibraryDetector`
  already collected a `script` role, but nothing parsed `pyproject.toml` to set it — so a
  CLI's `main` with no internal caller was false-flagged dead (the common case, including
  stitchgraph's own `stitchgraph = "...cli:main"`). The Python extractor now reads
  `[project.scripts]`, `[project.gui-scripts]`, and `[project.entry-points.*]` and tags the
  target with role `script`, matched by object name **and** module path so a same-named
  function elsewhere isn't mis-rooted.
- **A bash script's top-level body is a root** (issue #22 — the bash analogue of #8). A
  script that runs its work as bare top-level statements (no `main()`) had zero entry-point
  seed and zero inbound edges to its functions, so `find_stale` flagged all of them. Each
  bash script's module node is now seeded as a root (bash's `__main__`) and its top-level
  calls are rooted — direct, via `$(...)` command substitution, and via `trap NAME` — so
  those functions are correctly live. A function reached by nothing (including its own top
  level) still flags, exactly as intended.
- **`reindex` no longer hangs on a FIFO / special file.** `open()` on a named pipe with no
  writer blocks forever, and the `except OSError` guards never fire (the open doesn't
  error, it blocks). Every file walk that reads bytes/text now skips non-regular files via
  `path.is_file()`: the Python and tree-sitter extractors, the resolver `parse()` helper,
  and the four route/template resolvers that do their own `rglob` walk
  (`express`, `jsfetch`, `spring`, `html`) — the last of which run on every `reindex`, so a
  FIFO named `*.js`/`*.java`/`*.html` would otherwise hang the primary entry point. The
  fixed-path `pyproject.toml` read in `_console_script_targets` (the #21 path, run on every
  reindex) was also guarded with `exists()` — which is `True` for a FIFO — and is now
  guarded with `is_file()`, so a FIFO named `pyproject.toml` no longer hangs reindex.
  `load_coverage` (reached via `ingest_trace`) was hardened the same way: a FIFO trace path
  now returns empty (its documented "empty on any problem" contract) instead of blocking.
- **`ingest_trace` no longer crashes on a structurally-malformed coverage.py JSON report.**
  `_parse_json` guarded `executed_lines` *values* but assumed the `files` object — and each
  per-file entry — was a dict, so valid JSON of the wrong *shape* (`files` a list, an entry a
  string/null, `executed_lines` a dict) raised an uncaught `AttributeError` through the public
  `ingest_trace` op/CLI. It now isinstance-gates the shape and degrades to empty, matching the
  "empty on any problem" contract and the already-tolerant LCOV/Go parsers.
- **A malformed `stitchgraph.toml` no longer crashes every CLI command.** `_load` chained
  `.get().get()` over the config's sections, so a hand-edited file with a section that is not
  a table (`entry_points = "oops"`), a non-numeric `threshold`, or a non-list `include` raised
  `AttributeError`/`ValueError` — and config is read on every command. Each section/value is
  now shape-guarded and falls back to its default (the same robustness sweep that covered the
  coverage-JSON and FIFO cases).
- **`risk` no longer silently drops unicode-named source files.** `gitrisk._commits` parsed
  `git log --name-only`, but git octal-escapes and double-quotes non-ASCII paths under the
  default `core.quotepath=true` (`"caf\303\251.py"`), so the trailing quote defeated the
  source-extension filter and unicode-named files vanished from churn / co-change / `risk`
  hotspots. It now runs git with `-c core.quotepath=false` (and strips any residual quoting),
  so those files are counted.

### Documentation

- **`find_holes` scope documented** (issue #20). `find_holes` reports references orphaned
  by edits (delete/rename), not first-index calls to undefined/stdlib names — the
  extractors deliberately drop unresolved calls (precision over noise). `LIMITATIONS.md`
  and `AGENTS.md` now say so and point to `scan`, which **does** deliver the
  `is_stub ∧ reachable` "landmine" (design §6.D) as its `live_stub` finding.

## [1.0.5] — 2026-06-24

**Field-fix patch — CLI/UX papercuts (issues #18, #19).** Three small fixes found while
running v1.0.4 against a real repo; no analysis-engine changes. Confirmed by full
three-model panels.

### Fixed

- **`risk` is scoped from the indexed root, like every other read op** (issue #18).
  `risk`'s git-history `--path` defaulted to the process **cwd**, so `risk --db <db>` from
  anywhere but the analysed repo failed with `'.' is not a git repository` — even though
  the DB already records the indexed root (and `risk` reads it for path-mapping two lines
  later). It now defaults `--path` to the indexed root stored in the DB; pass `--path` to
  override. `risk --db <db>` now works from any directory. The `report` command had the same
  bug (it passed `repo="."` to `risk`, silently skipping its risk section from a foreign cwd)
  and gets the same fix — `report --db <db>` now includes risk from anywhere.

### Added

- **`stitchgraph --version`** (issue #19). Prints the installed package version *and* the
  active `tree-sitter-language-pack` line (bundled vs. download model) — the two things a
  bug report needs, given the version-keyed install model (#12). There was previously no
  way to confirm the installed version from the CLI.

### Fixed (cont.)

- **`stitchgraph.__version__` no longer drifts stale.** The literal had been left at
  `"1.0.3"` through 1.0.4/1.0.5; it now derives from the installed distribution metadata
  (`importlib.metadata`), the same source `--version` uses, so the attribute and the CLI
  always agree and there's nothing to bump by hand.

### Changed

- **`docs/design.md` §9 reconciled with the CLI** (issue #19). The operation surface listed
  a `path?` argument on `orient`/`find_stale`/`find_holes`/`scan` that the CLI never
  accepted. Scope comes from the **indexed graph** (`--db`), not a per-call path filter, so
  the `path?` is dropped and a "On scope" note documents the actual model (index the subset
  you want; `risk`'s `--path` is the git root, not a query filter). The §9 table was also
  scrubbed of stale entries: the phantom `structure_smells()` row (never a registered op —
  its output is part of `scan`), the non-existent `relations?` argument on `trace_path`, and
  the unbuilt `type_at` primitive (a documented LSP roadmap item that lives in `STATUS.md`,
  not the shipped-operation surface). §9 now lists only registered operations.

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
  (stdlib/third-party) class. The edge is now labelled `INFERRED` (a guess). In the
  **tree-sitter** extractor this is detected receiver-aware across all languages
  (member/field/selector/scoped access, plus Java `object` / Ruby `receiver` fields); every
  receiver call is demoted, while direct calls (`save()`) and constructors stay
  `EXTRACTED`. In the **Python `ast`** extractor, scope-aware resolution still wins first —
  `self.save()` and a locally-typed `r = Repo(); r.save()` stay `EXTRACTED`; only an
  unknown-receiver fallback (`x.save()`) is demoted, removing the Python↔tree-sitter
  asymmetry. **Weight is unchanged (1.0)** everywhere, so the edge still counts fully for
  reachability / `find_stale` — only the asserted confidence is lowered, never the liveness
  (cardinal-safe).
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
