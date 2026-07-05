# Performance: what to expect, and how to estimate it

Measured anchors and a practical estimation method for humans and LLMs deciding
whether to wait, background, or split a run. All numbers from a 4-core dev-class
box unless noted; disk speed is usually the real variable at scale.

## The one number that drives everything: EDGES, not files

Cost scales with resolved edges, and **edge density varies ~50× across
codebases** (stitchgraph's own repo: ~140 edges/file; Home Assistant's
homonym-heavy tree: ~2,400 edges/file). File count alone under-predicts badly on
framework-style Python. If you know nothing else, assume 200–500 edges/file for
typical code and treat homonym-heavy trees (many same-named methods across many
classes) as 5–10× denser.

## Indexing (streaming reindex)

Measured anchors:

| corpus | files | edges | db size | wall time | peak RSS |
|---|---|---|---|---|---|
| stitchgraph itself | 48 | ~7k | 5 MB | ~2 s | ~30 MB |
| memory-gate corpus | 450 | 1.2M | ~0.5 GB | ~1 min | <130 MB (capped) |
| homonym corpus | 1,212 | 8.6M | — | — | 50 MB |
| Home Assistant 2024.3.3 | 6,728 | 16.0M | 10 GB | **34 min** | 158 MB |
| megacorpus (HA + sympy + django + 21 pkgs) | 9,080 | **26.8M** | 17 GB | **61.6 min** | 228 MB |

Estimator check on the megacorpus (the first field test of this doc): edges/500k
predicts 54 min vs 61.6 measured (−12%); 0.65 KB/edge predicts 17.4 GB vs 17
measured. Good enough to plan around.

Estimation method, in order of increasing accuracy:

1. **Before starting**: `edges ≈ files × density` (see above), then
   `minutes ≈ edges / 500k`. HA check: 16M / 500k ≈ 32 min ≈ the measured 34.
2. **One minute into a run** (the good one): the streaming write rate is roughly
   constant, and the db grows at ≈ **0.6–0.7 KB per edge**. Watch the `.db` file:
   `ETA ≈ (predicted_final_size − current_size) / observed_MB_per_min`, where
   `predicted_final_size ≈ edges × 0.65 KB`. Then add the endgame.
3. **The endgame is not free and not visible**: after the last edge streams,
   override widening + global dedup + ANALYZE run inside SQLite — **add 20–35%**
   of the write-phase time on a warm cache, and up to **2×** that when the page
   cache is cold or the disk is slow (the phase rewrites large parts of the edge
   table; the `.db-wal` file being active is the tell that it's alive).
4. Memory never signals progress: streaming holds a **flat 50–160 MB RSS**
   regardless of corpus size. A flat RSS with an active WAL is a healthy run,
   not a hung one.

## Analysis sweeps (after v3.30's adjacency sidecar)

The FIRST sweep after a (re)index pays the one-time lazy sidecar build:
**~5 s per million edges** warm (74 s on 16M), up to ~2.5× cold. Every sweep
after that opens the sidecar in milliseconds. Warm anchors on the 16M-edge graph:

| op | time | notes |
|---|---|---|
| `find_stale` | 2.1 s | reachability is effectively free now (1.8 s even at 26.8M edges / 106k nodes) |
| `impact_of` | ~30 s | name resolution + rendering dominate |
| `find_chokepoints` | 59 s | articulation DFS in Python over ints; **memory is its cost** — 4.1 GB peak at 26.8M edges (the symmetrised int lists; recorded follow-up) |
| `orient` (fallback) | <1 s | sidecar bitcount |
| `scan` | ~5 min | per-candidate SQL shares dominate; scale ≈ linear in flagged candidates |
| `find_component` / `find_similar` | ~3 min/query | O(nodes) token scan — the recorded gap; a prebuilt embedder index is the fix |

Rule of thumb: reachability-shaped questions are interactive at any measured
scale; `scan` is a coffee break at 16M edges; token-similarity search is the one
op that is genuinely slow on huge graphs.

## POD / coverage ops

Set-math ops (`select_tests`, `find_gaps`, `co_change`, …) are sub-second up to
thousands of tests. SVD ops (`find_modes`, `feature_map`) depend on the matrix's
smaller dimension: seconds below ~2k tests/functions dense; install `[spectral]`
for the sparse path above that. `audit_graph` ≈ one reachability sweep per test —
with the sidecar warm, budget ~1 s per 10 tests on a 16M-edge graph.

## When an estimate misses badly, suspect (in order)

1. **Near-duplicate trees in one indexed root** — N copies of similar code make
   every homonym resolve ambiguously across all N copies; edge count grows
   quadratically-ish and nothing downstream measures anything real. Observed: a
   corpus padded with near-identical synthetic packages hit 21 GB (2× the
   realistic figure) and was still writing. Index real, distinct codebases.
2. **Cold page cache / slow disk** — the endgame and first sidecar build are
   disk-bound; everything else here assumed warm.
3. **Homonym density** — check `edges/files` after the fact; >1,000 means the
   graph, and everything downstream, is 3–5× the typical-code estimate.
4. **A pre-v3.29 index without planner stats** — reindex once; the ANALYZE
   safety net and pinned query shapes only fully protect fresh indexes.
