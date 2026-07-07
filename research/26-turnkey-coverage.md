# 26 — Turnkey coverage across the board

*2026-07-07 · v3.49.0 · the LLM review's "setup friction" finding, generalized
and closed for the languages that matter.*

## Not a Rust problem

The review (docs/LLM_REVIEW.md) hit the ~150-line wiring gap on Rust because
that is what the reviewer drove. The audit says it is everything but Python:
of the five languages `scaffold_coverage` covers, four ship `_TEMPLATE_RUN` —
a stub that prints a TODO and exits 1. The behavioural toolkit (the moat) is
turnkey for exactly one language.

## The design insight: the index already knows the spans

The hard part of every converter is mapping covered LINES to enclosing
FUNCTIONS with ids that match stitchgraph's node ids. The Python kit solves
it by re-deriving spans with `ast` inside the sandbox. That approach doesn't
generalize — but it doesn't need to: **stitchgraph generates the kit, and
stitchgraph's index already holds every function's `(id, start, end_line)`
span.** The kit now ships `spans.json` (per-file span tables, node-id exact),
so every language's converter is the same trivial, parser-free lookup:

    covered line -> innermost enclosing span -> node id

One stdlib-only python3 converter (`to_canonical.py`) handles all three new
formats — llvm-cov export JSON (Rust), Go coverprofiles, istanbul JSON
(JS/TS) — plus the span mapping and the canonical emit. No stitchgraph, no
language parser, no network needed inside the sandbox. (Python keeps its
proven ast converter: it predates the index shipping spans and its ids are
pinned by tests.)

## Per-language capture

| language | strategy | granularity |
|---|---|---|
| **Rust** | build instrumented once (`cargo llvm-cov test --no-report`), enumerate tests via `-- --list`, per test: wipe profraws → run `-- --exact <name>` → `report --json`; converter marks each executed function's entry line | per test |
| **Go** | `go test -list` per package, per test: `go test -run '^<name>$' -coverprofile -coverpkg=./...`; converter parses profile blocks (module-path prefix stripped via go.mod) | per test |
| **JS/TS** | detect jest/vitest from package.json; enumerate test files; per file: run with istanbul JSON coverage; converter reads statementMap/s | per test FILE (documented honestly) |
| **Java** | stays a template — JaCoCo per-test attribution needs build-system integration a generated script can't do responsibly | — |

Honesty notes baked into the kits: JS granularity is per test file (istanbul
per-test contexts are runner-specific; per-file is what generalizes); the
Rust loop reruns the suite binary per test (correct first, fast later); any
enumeration/capture failure skips that test with a line on stderr rather than
aborting the whole capture.

## Results (2026-07-07)

- **Rust / fd (the review's own language, a real crate)**: kit generated from
  the index, `bash run_coverage.sh`, zero edits → one instrumented build,
  then 267 tests captured at ~0.5 s each → `coverage_modes.json` with 316
  functions → `find_modes` answers: intrinsic dimensionality 7, 16 modes,
  minimal test set 154 of 267, mode 0 = the CLI-options cluster
  (`Opts.max_depth`/`max_results`… over `src/cli.rs`). The exact task that
  cost the reviewer ~150 hand-written lines is now zero.
- **Go fixture**: attribution exact — `TestMul → {Add, Mul}` because `Mul`
  calls `Add`; module-path prefix stripped via go.mod.
- **JS, both runners**: jest and vitest fixtures each produce exact
  per-test-file attribution through the same istanbul JSON path.
- The llvm-cov loop detail that made Rust fast: `--no-report` keeps the
  instrumented build; `clean --profraw-only` between tests wipes only the
  profile data (and `--no-report` cannot be combined with `--no-clean` —
  found by probe, encoded in the script).

## Gate

- Converter unit tests: fixture llvm-JSON / coverprofile / istanbul inputs +
  a spans table → exact canonical artifacts (including innermost-span
  attribution and prefix stripping).
- Kit-generation tests: spans.json content matches the index; turnkey
  manifest lists the four languages.
- End-to-end field validation: the generated Rust kit run against **fd**
  (the corpus already on disk) and the artifact fed to `find_modes`; Go and
  JS validated on self-contained fixture projects with real tool runs.
