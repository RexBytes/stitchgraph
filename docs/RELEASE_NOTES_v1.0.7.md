# stitchgraph v1.0.7 — multi-repo / multi-language precision hardening

A precision release driven by a **multi-repo, multi-language false-positive hunt**:
stitchgraph was run against **~47 real-world projects across 9 languages** — including code
*designed to break parsers* (IOCCC obfuscated C) and large/messy corpora (Linux kernel core,
WordPress, Magento, PrestaShop, Symfony, flake8, Flask, NestJS, TypeORM) — to ground-truth
`find_stale` against actual liveness. The hunt surfaced a family of **cardinal-class
false-deads** (live code flagged dead) from entry-point and liveness signals stitchgraph did
not yet model. Every fix only ever **adds roots** (precision-safe), and each is owned by a
regression test.

**Robustness held: 0 crashes across every corpus** — the 1.0.6 RecursionError / FIFO /
large-file / non-UTF-8 guards survive obfuscated and machine-generated C.

## Highlights

- **Far fewer false-deads on real codebases** — entry points, framework callbacks, and
  packaging conventions across Python, JS/TS, C/C++, Java, C#, Ruby, PHP, Rust, Go are now
  recognized as live roots.
- **src-layout just works** — a PyPA `src/` project's absolute imports resolve (including
  PEP 420 namespace packages with no `__init__.py`).
- **Modern framework code is understood** — NestJS/Angular/TypeORM decorators, Java/C#
  reflection annotations, and the CommonJS prototype/`exports.X = …` idiom.
- **Less noise** — dependency/vendored/build directories (`node_modules`, `vendor`,
  `third_party`, `target`, …) are skipped by default.

## What changed

### Added — new entry-point / liveness signals (all cardinal-safe)

- **`setup.cfg [options.entry_points]`** is parsed alongside `pyproject.toml` —
  `console_scripts`, `gui_scripts`, and plugin groups (e.g. `flake8.extension` /
  `flake8.report`). Class targets also root their public methods.
- **src-layout absolute-import resolution** — modules under a top-level `src/` are imported as
  `pkg.mod` (not `src.pkg.mod`); absolute imports now resolve so module-load-only-live code
  (registries, dispatch tables instantiated at import) isn't flagged dead. Works for **PEP 420
  namespace packages** too (no `__init__.py` required). Node ids are unchanged — the module
  lookup gains a src-stripped alias.
- **Inherited public methods of an exported class** are rooted — a base-class method such as
  Flask's `shell_context_processor`, called on an instance, is public API.
- **Framework callbacks across more languages:**
  - Java/C# reflection **annotations/attributes** — `@PostConstruct`, `@EventListener`, JPA
    `@PrePersist`, `[OnSerializing]`, `[ModuleInitializer]`, BenchmarkDotNet `[Benchmark]`, …
  - JS/TS **decorators** — NestJS/Angular/TypeORM: `@Controller`/`@Get`/`@Injectable`/
    `@Component`/`@Entity`/… root the decorated class or handler method.
  - **Transitive and self-named external-base callback classes** —
    `FlaskGroup → AppGroup → click.Group`; `EnvironBuilder(werkzeug.test.EnvironBuilder)`.
  - **Ruby** `const_missing` / `const_added` and more implicit interpreter hooks.
- **C/C++ `EXPORT_SYMBOL(...)`** (and `_GPL` / `_NS` variants) roots the named function as
  public kernel/module ABI — the C analogue of `__all__` / `module.exports`.
- **JS/TS member-assigned functions and classes** — `app.render = function(){…}`,
  `Foo.prototype.m = …`, `module.exports.x = …`, `exports.Parser = class {…}` are modeled and
  their bodies walked, so helpers they call aren't flagged dead. Module-scope ones are rooted
  (a member-assigned **class** takes the `exported` role so its public methods are rescued);
  function-nested ones stay reachability-gated.

### Changed

- **Dependency/vendored/build directories are skipped** when indexing — one shared set across
  both extractors: `node_modules`, `vendor`, `third_party`, `third-party`, `bower_components`,
  `target`, `.gradle`, plus the existing `.venv` / `build` / `dist` / `__pycache__` / `.git` /
  `.tox` / `.svn` / `.hg` / `.mypy_cache` / `.pytest_cache` / `.ruff_cache`. Conservative —
  only names reserved by convention for non-first-party content.
- A **bodyless C/C++ struct/enum/union** (a type reference like `struct timeval tv`, a forward
  declaration) is no longer extracted as a phantom dead class.

### Notes & limitations

- **Scalability:** `reindex` builds a single in-memory graph, so peak RAM scales with total
  nodes + edges — a *tens-of-thousands-of-file* monorepo (Magento, 24k files) exceeds ~12 GB.
  Index self-contained sub-trees separately, or provision RAM (~0.7–0.9 GB per 1,000 dense PHP
  files). A **streaming / constant-memory indexer** is the next planned enhancement.
- New documented, cardinal-safe over-rooting tradeoffs (flat-name export-name collisions;
  member-assigned methods) — see `LIMITATIONS.md`.

## How it was verified

Hardened by the standard three-layer release gate (full-diversity adversarial panel +
deterministic oracle suite + in-house mutation meta-oracle) over a 9-round fix-panel campaign
(R40–R48). The panels caught and fixed **6 over-rooting / recall defects the new features
themselves introduced** — R40A (script-class over-root), R40B + R41A (a comment between a
decorator/attribute and its def dropping the marker, across JS/TS **and** Rust), R40C
(member-assignment inside a dead function rooted), R42A (namespace-package src-layout), and
R46A (member-assigned-class methods flagged dead) — none ever shipped, each cardinal-safe and
owned by a regression test. The src-layout incremental defect class is now owned by the
differential oracle (a new `src/`-layout incremental==full fixture).

Released on **two consecutive full-diversity (opus + sonnet + haiku) clean panels (R47–R48)**;
`scripts/readiness.py` reports **RELEASABLE**.

- Tests: **374 passing** (full extras), incl. **23 oracle tests**
- Mutation meta-oracle: **17/17** killed (envelope)
- `ruff` ✅ · `mypy` ✅
- Dogfood (self-index): `find_stale` **1** advisory (no false-dead) · `find_holes` **0**
- Robustness: **~47 real repos across 9 languages, 0 crashes**

## Upgrade notes

Drop-in. No API or schema changes. On a `src/`-layout repo or one with vendored directories,
expect **fewer `find_stale` candidates** (live code previously mis-flagged is now correctly
rooted; dependency dirs are no longer indexed). Re-run `reindex` to pick up the improved
resolution.

_The maintainer applies the `v1.0.7` tag._
