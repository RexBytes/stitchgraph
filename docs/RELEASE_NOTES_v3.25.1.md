# stitchgraph v3.25.1 — release notes

**The dogfood patch.** v3.25.0 was pointed at its own source with the full battery — the static
graph ops plus the first POD run on the fixed math (`research/14-dogfood-v3.25.md`). The run
validated the v3.25.0 fixes live (2,356 coverage rows → 939 logical tests; intrinsic
dimensionality **27, exact**, where the pre-F3 code capped at 16; `find_gaps`' one untested-dead
function is exactly `find_stale`'s one candidate) — and caught one real defect in the newest op.

## Fixed

- **`runtime_risk` silently returned zero hotspots on src-layout repos.** Coverage function ids
  are relative to the *indexed* root (`stitchgraph/core/…`) while git churn paths are relative to
  the *repo* root (`src/stitchgraph/core/…`); the churn × behavioural-centrality join used raw
  strings, matched nothing, and returned `ok` with an empty list. It now translates through the
  same `_git_path_mapper` as the static `risk` op (+ regression test). On stitchgraph's own tree
  this turns 0 hotspots into 15, led by `treesitter.py` (churn 73 × behavioural centrality 12,664).

## Housekeeping

- `.gitignore` covers coverage.py parallel-mode data files (`.coverage.*`).
- `research/14-dogfood-v3.25.md` records the dogfood run: the three `scan` oranges verified
  deliberate (mutual recursions + the embedder bootstrap latch), `find_subsystems` isolating the
  nine body-matrix frontends as their own 329-node cluster (the D2 finding as a structural fact),
  and the full POD read (64-test minimal cover, the config↔envelope coupling found blind,
  the always-on store/envelope/config core).
