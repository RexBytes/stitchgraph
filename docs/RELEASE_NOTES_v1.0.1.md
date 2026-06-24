# stitchgraph v1.0.1 — field fixes

A patch release fixing three issues raised against 1.0.0 in real use on a Rust
crate (#7/#8/#9) — and, once #8 showed the same test-detection gap existed in
*every* language, a **polyglot generalization of test detection**. No new
operations and no surface changes; no behaviour change for anything 1.0.0 already
handled correctly. **The cardinal invariant — live code is never flagged dead —
is preserved**, and verified in both directions across eight review panels (W–DD).

## What was wrong, and what changed

### #8 — Rust inline unit tests flooded `find_stale` (precision)

Idiomatic Rust puts unit tests in `#[cfg(test)] mod tests { … }` with free-form
names (`closeness_works`, `add_works`). stitchgraph's test-root detection was
name-convention based (`test*` / `Benchmark*` / `Example*`), which **never fires**
for those names — so every `#[test]` function, and every helper it reached, was
reported as a stale candidate. On a real crate that was hundreds of false
candidates.

**Fix:** the `#[test]` / `#[tokio::test]` attribute (any `*::test`) and the
`#[cfg(test)]` module gate now seed the `test` role (a root), so the tests — and
the helpers they reach — stay live. Matching is on the attribute **path**, not a
raw `"test"` substring: `#[cfg(feature="testing")]` and `#[doc="…test…"]` do
**not** mark production code as a test (an over-match that would *hide* genuinely
dead code — caught by review panel W and corrected). A test helper reached by no
test, and unused production code, still flag — exactly as a dead helper in any
test file would.

> **Limitation:** tests driven only by a third-party runner macro whose path
> isn't `test`/`*::test` (`#[rstest]`, `#[test_case]`) aren't recognized as roots.
> The macro set is open-ended; recognizing all of them would re-introduce the
> over-match. Pin them in `stitchgraph.toml [entry_points]` or `ingest_trace` a
> `cargo test` run. See `LIMITATIONS.md`.

### #8 generalized — the same gap existed in every language (precision)

The Rust flood had a universal root cause: a file's test context never seeded the
`test` role, so *only* the `test*`/`Test*` **name** convention did. Every language
whose idiomatic tests aren't name-convention had the same bug — live tests, and
the helpers they reach, flagged dead. Confirmed empirically and fixed across the
board (panels Y–DD):

- **Annotation / attribute tests** (the direct analog of Rust `#[test]`): Java
  `@Test`/`@ParameterizedTest`/`@BeforeEach`/… (JUnit/TestNG), C# `[Fact]`/
  `[Theory]`/`[Test]`/`[TestMethod]`/… (xUnit/NUnit/MSTest), PHP `#[Test]`
  (PHPUnit) now seed the `test` role, matched on the annotation **name** against
  an allowlist (so `@Override`/`@Deprecated`/`[Obsolete]` don't match).
- **Call-based suites** with no named test function — JS/TS Jest/Mocha/Vitest,
  Ruby RSpec — root the `test()`/`it()`/`describe()` call sites at module scope in
  a test file (descending into the anonymous callbacks, but not into named defs or
  imports), so the helpers they call stay live. A helper called by nothing still
  flags.
- **Test classes** are seeded transitively: a class with a test member, a class
  that *contains* a nested test class, and a class that *inherits* its tests from a
  custom base (the JUnit abstract-base + thin-subclass idiom; pytest
  `class TestWidget:` / a `unittest.TestCase`) — all via a single combined
  fixed-point pass over nesting + inheritance.
- **`is_test_file` is now one directory-aware heuristic shared by both extractors.**
  A prior drift (Python checked only the filename; tree-sitter also checked
  directories) was itself a cardinal gap: a shared test base in `tests/conftest.py`
  went unrecognized, flagging an inheriting subclass dead. The shared set is the
  strongly-conventional `test`/`tests`/`spec`/`__tests__` dirs (ambiguous
  `testing`/`specs` are deliberately excluded as plausible *production* dirs).

Genuinely-dead test helpers, unused production code, bare metaclasses, and
`#[cfg(not(test))]` production code all still flag — the over-marking direction was
held safe throughout (it can hide some dead code, but never reports live code dead).

### #7 — a broken grammar load became a silent empty graph

If a tree-sitter grammar couldn't load — offline or proxied environments, or
version drift — extraction silently produced an empty graph and exited
successfully, so a non-Python repo looked like "ran fine, found almost nothing".

**Fix:** `treesitter.extract` records grammar-load failures and emits a
`RuntimeWarning` naming the affected languages and the count of skipped files;
`extract_project` warns instead of a blanket `except: pass`. Python extraction is
unaffected, and a normal run with grammars present emits no warning. The
dependencies that bit are now bounded — `tree-sitter>=0.22,<1`,
`tree-sitter-language-pack>=0.1,<2` — so a future breaking major can't silently
break a fresh install.

### #9 — `impact_of` on an ambiguous name gave no candidates and no way to scope

A bare common name (e.g. `get`, matching many `Type.get` methods) refused with no
hint and no way to pick one.

**Fix:** the refusal now lists the matching symbols in `alternatives`, and the
resolver accepts a fully qualified `Type.method` or a full `path::qual` id to
scope to exactly one — without unioning unrelated blast radii. The upgraded
resolver gives `get_callers` / `get_callees` / `trace_path` the same scoping;
names that legitimately contain dots (`index.html`) still resolve directly.

## Verification

`pytest` 165 passed (regression tests added per issue and per panel finding, each
pinning both directions) · ruff clean · mypy clean. Eight confirmation panels
(opus + haiku), each adversarially probing both directions:

- **W** found an attribute over-match in the #8 fix (substring vs path); **X** clean.
- **Y** found the `testing/`/`specs/` dir over-reach (hides dead code); fixed.
- **Z** found a cardinal: a pytest `class TestWidget:` (all `test_*`) flagged dead.
- **AA** found two cardinal siblings: inherited-only and nested test classes.
- **BB** found the two seed axes weren't a *combined* fixed point (nested+inherited),
  plus a cross-language base-name over-match.
- **CC** found the Python/tree-sitter `is_test_file` asymmetry (cardinal) and the
  `#[cfg(not(test))]` over-match.
- **DD** clean on both models, with an explicit verdict: the test-liveness class is
  **closed** for 1.0.1; the one residual (a test base in a *non-test* directory
  subclassed by an own-method-less test) is a documented, precision-safe limitation.

Full trajectory in `REVIEW_HISTORY.md`.
