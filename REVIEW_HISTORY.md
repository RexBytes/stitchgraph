# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 0 (kit just adopted) |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ · no-open-defects ✅ |
| Tests | 83 passing, 1 skipped |
| Coverage | ~80–85% |
| Release-Readiness Score | 70 / 100 |
| Convergence | clean streak 0 of 2 required; confidence 0.00 |
| Verdict | NOT RELEASABLE — needs review panels (convergence) |

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Models | Findings | Weighted | Theme |
|---|---|---|---|---|
| _(none yet)_ | | | | kit adopted; baseline RRS 70 |

## What each panel found and how it was fixed

_(append one bullet per panel)_

## Standing themes

- Convergence is non-monotonic and never reaches zero — measure residual risk.
- Late-stage defects are symmetry gaps: a guard present in one language extractor
  or resolver but not its siblings. Audit by a path×behaviour matrix.
- Blind spots: tree-sitter / graphblas / sqlglot / jedi / mcp surfaces are gated
  by optional deps; a panel is blind to them unless the extras are installed.

_Maintenance: append a trajectory row + a bullet per panel; keep the TL;DR in
sync with `release_readiness.json`._
