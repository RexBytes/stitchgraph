# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 1 (Panel A) |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ · no-open-defects ✅ |
| Tests | 92 passing, 1 skipped |
| Coverage | 83.1% |
| Release-Readiness Score | 69.7 / 100 |
| Convergence | clean streak 0 of 2 required; confidence 0.00 |
| Verdict | NOT RELEASABLE — Panel A found real defects (all fixed); needs clean panels to converge |

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Models | Findings | Weighted | Theme |
|---|---|---|---|---|
| A | opus · sonnet · haiku | 3 HIGH · 3 MEDIUM · 1 LOW | 43.0 | symmetry gaps — a rule present in one sibling, missing in another |

## What each panel found and how it was fixed

- **Panel A (opus · sonnet · haiku)** — every finding was a *symmetry gap*: a
  guard/rule applied in one extractor or resolver but not its siblings. All seven
  were reproduced from real input and fixed with a regression test
  (`tests/test_regressions.py`):
  - **HIGH (opus)** — `EMITS`/`HANDLES` were missing from `LIVENESS_RELATIONS`, so
    an event handler registered + fired from a live entry point was flagged stale
    (a precision-invariant violation). Added both relations; liveness now crosses
    the pub/sub boundary as it already did for `ROUTES_TO`/`RENDERS`.
  - **HIGH (haiku)** — the Django-route, event, and Express resolvers each gated
    edge creation on `if len(cands) == 1:` with no `else`, silently dropping a
    handler edge whenever a name was shared — risking a live handler called dead.
    Now they link to *all* candidates as `AMBIGUOUS` (mirroring the AST extractor).
  - **HIGH (sonnet F1)** — the CLI rebuilt every parameter as `str`, so `--limit 5`
    arrived as `"5"` (a `TypeError` on the int comparison) and bool flags inverted.
    The wrapper now preserves each param's real type.
  - **MEDIUM (sonnet F2)** — `scan` assigned a live stub `RED` regardless of how it
    was reached, bypassing the provenance ceiling. RED now requires an
    EXTRACTED-only reachable path; an inferred (heuristic) path caps at ORANGE.
  - **MEDIUM (sonnet F3)** — a Rust `impl<T> Container<T>` block resolved to the
    type parameter `T`, mis-attributing every method. `_trailing_id` now names the
    base type, skipping `type_arguments`/`type_parameters`.
  - **MEDIUM (sonnet F4)** — public methods of an exported class weren't seeded, so
    they could be false-flagged dead though external callers reach them. Public
    (non-underscore) methods of an exported class now inherit the `exported` role.
  - **LOW (sonnet F5)** — `[review] threshold` was a documented `stitchgraph.toml`
    knob that nothing consumed. `config.load_config` now applies it to the envelope
    (one-directional: config → envelope, keeping envelope stdlib-only).

## Standing themes

- Convergence is non-monotonic and never reaches zero — measure residual risk.
- Late-stage defects are symmetry gaps: a guard present in one language extractor
  or resolver but not its siblings. Audit by a path×behaviour matrix.
- Blind spots: tree-sitter / graphblas / sqlglot / jedi / mcp surfaces are gated
  by optional deps; a panel is blind to them unless the extras are installed.

_Maintenance: append a trajectory row + a bullet per panel; keep the TL;DR in
sync with `release_readiness.json`._
