# Release Readiness

Two numbers decide whether stitchgraph is shippable:

- **Release-Readiness Score (RRS, 0–100)** — a snapshot of quality *now*.
- **Convergence** — the *trend* (across review panels) that says the snapshot is
  trustworthy, i.e. that few undiscovered defects remain.

A high RRS with low convergence means "looks good but we haven't looked hard
enough yet." Ship only when both are satisfied.

```bash
python scripts/readiness.py            # runs gates + coverage, reads history
python scripts/readiness.py --no-gates # skip pytest/ruff/mypy (use cached)
```

## Why not "perfect code, no review"?

Unachievable for non-trivial software: semantic correctness is undecidable in
general (Rice's theorem); the *spec itself* is incomplete and is discovered
through review; and behaviour depends on empirical facts about dependencies
(which exact node type a tree-sitter grammar emits, which exception a stdlib call
raises). For stitchgraph specifically, much of the "spec" is the **promised
contract of each operation's `Result` envelope** and the **precision-over-recall
stance** — both discovered and pinned through review. The goal is not "zero
cycles" — it is a **measurable, auditable release decision**.

## Hard gates (any failure caps RRS at 40 = NOT releasable)

- all tests pass (`pytest`)
- lint clean (`ruff check src tests`)
- type-check clean (`mypy`)
- zero **known-open** defects (anything not-fixed lives in `LIMITATIONS.md` as a
  *decision*, not an open defect)

## RRS components (weights sum to 100)

| Component | Weight | How it's measured |
|---|---|---|
| Test coverage | 15 | line+branch %, scaled against an 85% target |
| Property / round-trip tests present | 10 | Hypothesis invariants exist (`tests/test_properties.py`) |
| Contract coverage | 20 | fraction of public docstring promises with a pinning test (estimated in config until automated) |
| Convergence confidence | 25 | from the panel history (below) |
| Static rigor | 15 | mypy clean + ruff ruleset |
| Docs | 10 | LIMITATIONS / CONTRIBUTING / README present |
| Security / robustness | 5 | read-only / malformed-input / traversal tests present (`tests/test_safety.py`) |

## Convergence

Weight each panel's **new, confirmed** defects by severity
(`CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2`).

- **Convergence Rate** `CRₙ = Wₙ / W₁` — this panel's weighted yield vs the
  first. Trends toward 0, but **not monotonically** — a deep HIGH can surface late.
- **Clean streak** — consecutive trailing *full-diversity* panels with weighted
  yield below `tau` (default 2 ⇒ "nothing above LOW").
- **Convergence confidence** `= 1 − e^(−streak × diversity)`, where `diversity`
  is the fraction of available models that participated.
- **Convergence score** (feeds RRS) `= 0.5·decline + 0.5·confidence`.

## Release rule

Ship when **all gates green AND RRS ≥ 90 AND clean streak ≥ 2 at full
diversity**. The streak requirement is the real safeguard: two independent,
fully-briefed panels in a row finding nothing above LOW.

> **Diversity definition (adapted 2026-06-23).** "Full diversity" means every
> model in `available_models` participated. sonnet's API became unreliable for
> agent slots (it had already degraded to a third-party core-only review in
> Panels M–Q), so by maintainer decision the panel now runs on the two
> reliably-available models — **opus + haiku** — and `available_models` is set to
> them. This is a deliberate, documented weakening of the diversity signal: two
> models catch fewer blind spots than three. When sonnet recovers (or a third
> model such as Fable is added), restore it in `release_readiness.json` and the
> next two clean panels must clear the higher bar.

## Visibility caveat (a clean panel only counts where it can see)

stitchgraph has heavily **optional, gated surfaces** — tree-sitter (11
languages), python-graphblas, sqlglot, jedi, the MCP server. With an extra
absent, its tests `importorskip` and a panel run that way is **blind** to it. A
release decision must state which surfaces the convergence signal covers; CI
installs `[all,dev]` so the gated paths actually run, and a separate job installs
**no** extras to prove the stdlib-only core.

## Maintaining the history

After each panel + fix, append the panel to `release_readiness.json` with its
participating `models` and `findings` counts by severity (the *new, confirmed,
adjudicated* ones — not re-reports, not documented tradeoffs). Re-run
`scripts/readiness.py`. **Anti-gaming:** every input must be adversarially
sourced — coverage from a meaningful suite, convergence from *independent* models
each given the full philosophy in `CONTRIBUTING.md`.
