# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 3 (Panels A, B, C) |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ · no-open-defects ✅ |
| Tests | 98 passing, 1 skipped |
| Coverage | ~83% |
| Release-Readiness Score | 77.0 / 100 |
| Convergence | weighted yield 43 → 15 → 18 (non-monotonic); clean streak 0 of 2 required |
| Verdict | NOT RELEASABLE — still surfacing real sibling gaps; needs ≥2 clean full-diversity panels to converge |

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Models | Findings | Weighted | Theme |
|---|---|---|---|---|
| A | opus · sonnet · haiku | 3 HIGH · 3 MEDIUM · 1 LOW | 43.0 | symmetry gaps — a rule present in one sibling, missing in another |
| B | opus · sonnet · haiku | 1 HIGH · 1 MEDIUM · 1 LOW | 15.0 | the *same* gaps in the other siblings (tree-sitter, html/jsfetch, git risk) |
| C | opus · sonnet · haiku | 1 HIGH · 2 MEDIUM | 18.0 | deeper surfaces — tree-sitter callback roles, signal `.connect()`, SQL CTE phantom |

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

- **Panel B (opus · sonnet · haiku)** — yield fell 43 → 15 as Panel A's fixes
  held; every finding was the *same* symmetry gap in a sibling Panel A hadn't
  touched. All reproduced and fixed with regression tests:
  - **HIGH (opus + sonnet, converged)** — the tree-sitter twin of F4: public
    methods of an `export class` (JS/TS) weren't seeded `exported`, so they were
    listed as dead-code candidates (a precision violation on live API surface).
    Added `_seed_exported_class_methods`, mirroring the Python extractor; the two
    models independently landing on the same defect is strong confirmation.
  - **MEDIUM (haiku)** — the HTML-form and JS-fetch resolvers indexed routes by
    path alone, so when `GET /x` and `POST /x` both existed a form/fetch linked to
    only one — `trace_path` could miss the real target. Now they link to *all*
    routes sharing a path (AMBIGUOUS when several), like `routes.py`/`express.py`.
    (Re-assessed from haiku's HIGH: route nodes are themselves seeds, so no handler
    is flagged dead — the impact is trace completeness, not a precision violation.)
  - **LOW (sonnet)** — `gitrisk` hard-filtered git history to `.py`, so `risk()`
    was silently empty (and misleadingly refused) on polyglot repos. The churn /
    co-change scraper now accepts every indexed source extension.

- **Panel C (opus · sonnet · haiku)** — yield ticked up to 18 (non-monotonic, as
  the methodology expects) as the panel reached surfaces the first two hadn't.
  All reproduced and fixed with regression tests:
  - **HIGH (haiku)** — the tree-sitter extractor lacked the Python extractor's
    `_apply_callback_roles`, so methods of a framework-base subclass (e.g.
    `class MyButton extends React.Component`) were flagged dead though the
    framework invokes them. Added `_seed_callback_roles`. (The agent proposed the
    fix directly in the tree; on review its `_PLAIN_BASES` wrongly listed framework
    bases like `HTMLElement`/`EventTarget` as "plain" — the unsafe direction — so
    that set was trimmed to built-in value constructors only.)
  - **MEDIUM (opus)** — the event resolver documented and listed `.connect` but
    only matched the 2-arg string form, so single-arg signal registration
    (`signal.connect(handler)`, blinker/Django/Qt) never linked and handlers were
    flagged dead. Added receiver-keyed events so `signal.connect(h)` and a bare
    `signal.send(...)` meet on the same event node — a broken docstring promise,
    now kept.
  - **MEDIUM (sonnet)** — a `WITH x AS (...)` CTE parses as a Table when
    referenced, so the SQL resolver minted a phantom `db::x` node. CTE aliases are
    now collected and skipped.

> Process note: Panel C agents shared the working tree and one edited source
> directly. Subsequent panels run in **isolated worktrees, strictly review-only**
> — they report findings; the maintainer adjudicates and applies.

## Standing themes

- Convergence is non-monotonic and never reaches zero — measure residual risk.
- Late-stage defects are symmetry gaps: a guard present in one language extractor
  or resolver but not its siblings. Audit by a path×behaviour matrix.
- Blind spots: tree-sitter / graphblas / sqlglot / jedi / mcp surfaces are gated
  by optional deps; a panel is blind to them unless the extras are installed.

_Maintenance: append a trajectory row + a bullet per panel; keep the TL;DR in
sync with `release_readiness.json`._
