# 17 — 100k-node scale validation (the megacorpus run)

**Date:** 2026-07-05 · **Tool:** v3.34.0 (single branch `claude/adjacency-sidecar`,
carrying v3.28.0–v3.34.0) · **Goal:** the STATUS.md roadmap item "~100k-node scale
validation" — prove the whole stack (constant-memory streaming index, ANALYZE
planner stats, lazy adjacency sidecar, sidecar-scale sweeps) past the largest
graph it had ever seen (Home Assistant, 59k nodes / 16M edges).

## The corpus — and the failed first attempt worth remembering

**Attempt 1 (killed):** HA + the synthetic memory-gate/calibration corpora
(9,006 files). Killed at 1h49m with 21 GB written and still going — double the
realistic projection. Diagnosis: the synthetic packages are near-identical copies
of one homonym-dense tree; sharing one indexed root, every homonym name resolves
AMBIGUOUS across *all* copies, so edge count grows quadratically-ish with the
copy count. Nothing downstream of such a graph measures anything real.
**Recorded as estimator hazard #1 in `docs/PERFORMANCE.md`: index real, distinct
codebases — never pad a corpus with near-duplicates.**

**Attempt 2 (the run):** real code only — the full HA 2024.3.3 tree + sympy 1.12
+ django 5.0.4 + the 21 research-eval packages. 9,080 files.

## Index (streaming, one shot)

| | measured |
|---|---|
| files / nodes / resolved edges | 9,080 / **106,027** / **26,782,196** |
| index size | 17 GB (634 B/edge) |
| wall time | **61.6 min** |
| peak RSS | **228 MB, flat** |
| holes | 191 |

Constant-memory holds at 1.8× the HA edge count and 1.8× its node count; the RSS
rise from HA's 158 MB is the larger symbol table (node ids/names), not edges —
still O(symbols), not O(edges).

**Estimator field test** (`docs/PERFORMANCE.md`, written *before* this run):
`edges/500k` predicted 54 min (measured 61.6, −12%); `0.65 KB/edge` predicted
17.4 GB (measured 17). The method survives contact with reality.

## Analysis battery (per-op subprocess, cold page cache)

| op | time | peak RSS | output |
|---|---|---|---|
| `orient` (cold — includes one-time sidecar build) | 81.5 s | 838 MB | hubs |
| `orient` (warm) | 7.5 s | 832 MB | hubs |
| `find_stale` | **1.8 s** | 792 MB | 4,646 candidates |
| `find_chokepoints` | 78.6 s | **4.1 GB** | 30 chokepoints |
| `scan` | 397 s | 3.1 GB | 2,077 issues |

Reachability-shaped questions are interactive at 26.8M edges. The sidecar for
this graph is ~260 MB on disk beside the 17 GB index.

## Follow-ups recorded (STATUS.md roadmap)

1. **`find_chokepoints` memory at scale** (S): 4.1 GB peak is the articulation
   pass's symmetrised neighbour lists materialised via `.tolist()` — keeping them
   as numpy arrays trades a little traversal speed for ~10× less memory.
2. **`transitive_fan_in`'s 4,000-node closure cap** (M): at 106k nodes `orient`
   falls back to confident fan-in (correct, and fast via the sidecar bitcount);
   uncapping the closure needs a frontier-bounded or blocked formulation.

## Verdict

The v3.28→v3.34 stack — streaming index, planner stats, sidecar, capped scan
output — is validated at 106k nodes / 26.8M edges on real code, with one memory
soft spot and one known cap, both recorded. The roadmap row is closed.
