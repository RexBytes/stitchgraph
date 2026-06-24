# stitchgraph v1.0.1 — field fixes

A patch release fixing three issues raised against 1.0.0 in real use on a Rust
crate. No new operations, no surface changes, no behaviour change for anything
1.0.0 already handled. **The cardinal invariant — live code is never flagged
dead — is preserved**, and verified in both directions by review.

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

`pytest` 152 passed (three regression tests added, one per issue, each pinning
both directions) · ruff clean · mypy clean. Two confirmation panels (opus +
haiku): **W** found the attribute over-match in the #8 fix (corrected); **X**
returned clean on both models. Full trajectory in `REVIEW_HISTORY.md`.
