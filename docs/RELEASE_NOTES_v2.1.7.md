# stitchgraph v2.1.7 — recall: third-party Rust test harnesses + ByteBuddy/Moshi annotations

The first of the **non-cardinal** items from the `LIMITATIONS.md` audit. These are recall gaps —
they under-report live code as dead (a method that *is* reached, by a test runner or a framework,
flagged as a candidate). Closing them is cardinal-safe: rooting only ever *adds* roots.

## What changed

**Third-party Rust test attributes.** The test detector matched `#[test]` and any `*::test`
(`#[tokio::test]`, `#[googletest::test]`). It now also recognizes the common third-party harnesses
whose attribute path does **not** end in `test`:

- `#[rstest]` (rstest), `#[test_case(...)]` (test-case), `#[gtest]` (googletest-rust),
  `#[quickcheck]` (quickcheck).

Matched on the last path segment, so `#[rstest::rstest]` is covered too. The free-form-named test
function these decorate — and the helpers it reaches — are now rooted `test` instead of surfacing as
stale candidates.

**ByteBuddy and Moshi annotations.** The curated Java callback-annotation set now includes:

- ByteBuddy `@Advice.OnMethodEnter` / `@Advice.OnMethodExit` (bytecode instrumentation — e.g.
  mockito's mock advice), and
- Moshi `@ToJson` / `@FromJson` adapter methods (invoked by reflection).

These were the documented external-framework-annotation gap surfaced in the Java/C# hunt
(mockito/okhttp). The annotated methods — and their callees — are now rooted `callback`.

## Still not covered (documented)

Other unrecognised framework annotations, more obscure Rust test macros, and JS/TS metadata-only
decorators remain outside the curated sets (pin them via `stitchgraph.toml [entry_points]`). The
sets cover the dominant frameworks, not every library — expanding them indefinitely would risk
over-matching.

## Compatibility

No API or schema change; indexes rebuild cleanly.

## Quality gate

Full suite (incl. Rust test-attr, ByteBuddy/Moshi, and helper regression tests) + ruff + mypy clean;
differential oracles green; mutation meta-oracle over `_is_rust_test_attr` (all mutants killed);
two-round full-diversity multi-model adversarial review.
