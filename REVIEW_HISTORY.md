# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 6 (Panels A–F) |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ · no-open-defects ✅ |
| Tests | 108 passing, 1 skipped |
| Coverage | ~83% |
| Release-Readiness Score | 77.7 / 100 |
| Convergence | weighted yield 43 → 15 → 18 → 6 → 12.2 → 16 (non-monotonic); clean streak 0 of 2 required |
| Verdict | NOT RELEASABLE — Panels E/F fixed the last untested language paths (C/C++, Ruby); all 11 languages now verified. Needs ≥2 clean panels |

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Models | Findings | Weighted | Theme |
|---|---|---|---|---|
| A | opus · sonnet · haiku | 3 HIGH · 3 MEDIUM · 1 LOW | 43.0 | symmetry gaps — a rule present in one sibling, missing in another |
| B | opus · sonnet · haiku | 1 HIGH · 1 MEDIUM · 1 LOW | 15.0 | the *same* gaps in the other siblings (tree-sitter, html/jsfetch, git risk) |
| C | opus · sonnet · haiku | 1 HIGH · 2 MEDIUM | 18.0 | deeper surfaces — tree-sitter callback roles, signal `.connect()`, SQL CTE phantom |
| D | opus · sonnet · haiku | 1 MEDIUM · 2 LOW | 6.0 | incremental/edge surfaces — `_resolve_worklist` ambiguity, recursion self-edge, malformed-coverage crash |
| E | opus · sonnet · haiku | 1 HIGH · 2 LOW · 1 NIT | 12.2 | untested language path — C/C++ functions silently dropped; exported-method over-seeding, INSERT…SELECT label, jsfetch guard |
| F | opus · sonnet · haiku | 1 HIGH · 1 MEDIUM · 2 LOW | 16.0 | Ruby paren-less calls (all 3 converged); TS `export{}` re-export; trace_path vacuous-ok; SQL multi-statement |

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

- **Panel D (opus · sonnet · haiku)** — first run in isolated worktrees,
  review-only. Yield fell to 6; opus also fuzz-confirmed the GraphBLAS-vs-pure-Python
  reachability agreement over 3000 random graphs (0 mismatches) and cleared coverage
  ingestion, `find_similar`, the report/MCP/CLI adapters, and the envelope contract.
  - **MEDIUM (opus + sonnet, converged)** — `Store._resolve_worklist`, the
    incremental re-resolution path, linked an ambiguous hole to only one candidate
    (`COUNT(*) = 1` guard) — the lone resolution site not over-approximating. Now it
    links to *all* candidates as AMBIGUOUS, mirroring the extractors. The rarer
    cross-update homonym case (a hole uniquely resolved, then a same-named def added
    by a *later* single-file update) is documented in `LIMITATIONS.md`; `replace_file`
    is experimental and `reindex` (the wired path) is authoritative.
  - **LOW (haiku)** — the tree-sitter `_ref` filtered self-references for *all*
    relations, dropping the self-CALLS edge of a recursive function that the Python
    extractor keeps. The self-filter now applies only to INHERITS/IMPORTS.
  - **LOW (sonnet)** — `runtime._parse_json` admitted non-integer `executed_lines`
    from a malformed coverage file, crashing the later range test (LCOV/Go already
    int-cast). JSON now coerces and drops non-integers too.

- **Panel E (opus · sonnet · haiku)** — the panel reached an *untested* surface and
  the headline find justifies the whole exercise:
  - **HIGH (sonnet)** — C and C++ produced **zero** function/method nodes: `_name_of`
    read the function's `type` field (the *return type*) before the `declarator`, so
    every `function_definition` resolved to `None` and was silently dropped — the
    entire C/C++ call graph was empty, though `docs/LANGUAGES.md` claimed ✅ and no
    C/C++ test existed to catch it. `_name_of` now reads the declarator first (the
    `type` field is kept only for Rust `impl` targets); a C/C++ regression test guards
    it. `Widget* create()` also now resolves to `create`, not `Widget`.
  - **LOW (opus)** — `_seed_exported_class_methods` over-marked Java/C# *private*
    methods of a public class as `exported` (its docstring claimed those languages
    were unaffected). Now gated to the JS/TS family, where visibility is inherited
    from the class; Java/C#/Go/Rust/PHP keep their correct per-method roles.
  - **LOW (sonnet)** — `INSERT INTO archive SELECT … FROM users` labelled the SELECT
    source `users` as WRITES (top-level statement type applied to every table). The
    DML target is now distinguished from read sources.
  - **NIT (haiku)** — `jsfetch.py` lacked html.py's `"METHOD /path"` route-name guard
    (zero practical impact; added for defensive symmetry).

> Process note: a Panel E review worktree ran its own `pip install -e .`, repointing
> the editable install at a stale worktree copy — caught when a verified fix appeared
> not to take. Worktrees are now pruned and the install repointed to the main tree;
> all fixes re-verified there. Review worktrees must not install the package.

- **Panel F (opus · sonnet · haiku)** — review-only on the main tree (worktree
  isolation was retired after the Panel E contamination). All three models
  **independently converged** on the same defect — the strongest confirmation in the
  series — closing the last untested language path:
  - **HIGH (opus + sonnet + haiku, converged)** — Ruby's idiomatic paren-less,
    receiver-less call (`validate`) parses as a bare `identifier`, not a `call`
    node, so those CALLS edges were dropped and a method reached only that way looked
    dead (precision violation). Added a `bare_calls` LangSpec flag and `_is_bare_call`,
    which treats a bare identifier as a call unless it is structurally a def/param/
    assignment-target/receiver; resolution goes through `_ref`, which links only to
    project-defined methods (the safe over-approximating direction).
  - **LOW (opus)** — `trace_path` returned `refuse(..., result=[])` for "no path",
    and `refuse` sets `ok = result is not None`, so a genuine no-path came back
    `ok=True` with an empty result — which had masked the Ruby bug (the polyglot test
    asserted only `.ok`). "No path" is now a clean refusal (`ok=False`).

  - **MEDIUM (sonnet)** — a TypeScript named re-export `export { Widget }` did not
    seed the `exported` role (only inline `export class` did), so the re-exported
    class and its methods were false-flagged dead. A re-export post-pass now marks
    matching nodes exported (mirroring Python's `__all__`).
  - **LOW (sonnet)** — a multi-statement SQL string (`DELETE …; SELECT …`) parsed via
    `parse_one` as a single `Block`, so the DML target was mislabelled READS. Now
    parsed with `parse()` and classified per statement.

  With C/C++ (Panel E) and Ruby (Panel F) fixed, **all 11 tree-sitter languages are
  now verified to extract defs + a call graph** (opus exercised each on real input).

## Standing themes

- Convergence is non-monotonic and never reaches zero — measure residual risk.
- Late-stage defects are symmetry gaps: a guard present in one language extractor
  or resolver but not its siblings. Audit by a path×behaviour matrix.
- Blind spots: tree-sitter / graphblas / sqlglot / jedi / mcp surfaces are gated
  by optional deps; a panel is blind to them unless the extras are installed.

_Maintenance: append a trajectory row + a bullet per panel; keep the TL;DR in
sync with `release_readiness.json`._
