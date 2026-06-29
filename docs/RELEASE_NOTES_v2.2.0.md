# stitchgraph v2.2.0 — the cardinal sweep is complete

This is a milestone release. The **per-language cardinal sweep is complete across all ten supported
languages**, and the post-sweep precision/recall follow-up backlog (**#70–#89**) is closed. It
consolidates the entire 2.1.1–2.1.31 hardening line into one minor release.

**No API or schema change. Indexes rebuild cleanly.** `find_stale` is strictly more precise than
2.1.0 — fewer false-positive dead-code reports, nothing new to migrate.

## The invariant this release is built around

> **Live code is never confidently flagged dead.**

Reporting a *live* symbol as dead ("a cardinal") is the one failure that destroys trust in a dead-code
tool — once it happens, you stop believing any of its findings. Every change in this line either
removes a way that could happen or improves dead-code *recall* without ever risking it. Over-rooting
(occasionally keeping genuinely-dead code alive) is the deliberate, safe direction.

Each fix shipped behind a hard gate — `ruff` + `mypy` + the full test suite + a **differential
streaming oracle** (the streamed graph must be byte-identical to the in-memory one) + a **mutation
meta-oracle** — and **two consecutive clean full-diversity multi-model adversarial review rounds**
(independent Opus, Sonnet, and Haiku reviewers, each trying to find a live symbol flagged dead).

## What's in it

### Per-language cardinal sweep (2.1.1 – 2.1.26)

One gated cardinal fix per language, so a live symbol reached only by a language-specific or
framework idiom is no longer reported dead:

- **Python** — framework/Protocol/ABC classes, enum hooks, pytest/conftest discovery, parameter
  defaults & annotations, nested/local defs.
- **JS / TS** — re-exports and CommonJS/`export default`, object-literal method shorthand, class
  expressions, well-known-Symbol methods, `get`/`set` accessors, `toJSON`/`toString`/`valueOf`.
- **Go** — `init()` runtime entry, method value/expression selectors.
- **Rust** — `#[no_mangle]`/`#[export_name]`, test attributes, `#[ctor]`/`#[dtor]`, const-item
  initializer calls, trait `impl`/inherits.
- **C / C++** — `EXPORT_SYMBOL`, export-attribute declarations, `#define` macro-body call sites,
  global function-pointer / vtable tables, ISR/interrupt attributes, range-for customization points.
- **C#** — attribute classes, explicit interface implementations, native/FFI entry points.
- **Java** — native (JNI) methods, same-name overload role unions, anonymous-inner-class overrides.
- **PHP** — bare-string callables, transitive framework inheritance.
- **Ruby** — operator methods, implicit conversion/coercion & Enumerable/Comparable protocols,
  `&:symbol` / `enum_for` symbol dispatch.
- **Bash** — top-level body roots, `trap` / `complete -F` / `time` / `export -f` callbacks.

### Post-sweep follow-up backlog #70–#89 (2.1.27 – 2.1.31)

- **#74** — JS/TS function referenced via object-literal **shorthand** in an exported object
  (`export const handlers = { onClick }`), including the canonical `… as const` / `satisfies` forms.
- **#76 / #78** — TS `#private` method called via `this.#m()`; string / computed / numeric-keyed
  class methods.
- **#70 / #86** — Python subscripted `Protocol[T]` / `ABC, Generic[T]` recognition, and bodyless
  abstract / Protocol interface methods.
- **#89** — C/C++ struct/union/enum used only as a *type* (`struct Config g;`).
- **#73** — Bash `declare -fx` / `declare -f -x` / `typeset -fx` function exports and
  `time { fn; }` targets.

The remaining backlog items were resolved without a code change — confirmed by the review panels as
deliberate **cardinal-safe boundaries** (where tightening would risk the invariant) — or are
coverage-only.

## Compatibility

- No public API change; no index-schema change.
- Existing indexes rebuild cleanly; the streaming and in-memory indexers remain byte-identical.
- The only observable difference is **fewer false-positive dead-code reports** from `find_stale`.

## Known limitations (unchanged)

Genuinely dynamic dispatch the static graph can't see remains out of scope and documented in
`LIMITATIONS.md` (e.g. Ruby `send`/`public_send`, Salt-style string-keyed loaders). One newly
filed pre-existing recall gap is deferred: Bash `PROMPT_COMMAND=fn` / `var=fn; $var` indirect
invocation.

## Quality gate

571 tests + 27 differential oracles + `ruff` + `mypy`, all green; mutation meta-oracle on each new
helper; readiness **RELEASABLE** (two consecutive clean full-diversity panels per fix). Dogfood on
stitchgraph's own source: `find_stale` advisory-only (no false-dead), 0 holes, streaming ==
in-memory parity.

The GitHub Actions CI matrix — `test (py3.11)`, `test (py3.12)`, `lint + type-check`, and
`core-only (no extras)` — is green. (The `core-only` job runs the suite with no optional extras
installed to prove the stdlib-only core; 14 tree-sitter-dependent tests were guarded with
`pytest.importorskip` so they skip cleanly there.)
