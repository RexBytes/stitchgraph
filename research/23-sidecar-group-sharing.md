# 23 — Sidecar CSR group-sharing: the v2 layout

*2026-07-06 · dependency-free batch ④ · the last consumer of the flat-edge
expansion learns to read candidate sets shared.*

## The residual v3.41.0 left behind

Edge compression (research/20) made SQLite store each interned candidate set
once — 16.1M logical edges in a 317 MB index at Home Assistant scale. But the
adjacency sidecar still read `edges_all`, the expanding view: its CSR arrays
re-materialised every widening arm per site. The numbers on the HA field
index:

| | logical rows in sidecar | on-disk | build |
|---|---|---|---|
| v1 (expanded) | 16,115,797 | 162 MB | ~74 s, per-row Python loop |
| v2 (shared) | 174,703 flat + 160,757 group + 200,885 member | see results | rows read ∝ shared form |

The redundancy is the same 96.2% the store compression removed: 160,757 group
rows reference only 3,103 distinct candidate sets.

## Layout v2

Flat edges keep the four v1 arrays per direction (`fwd_*`/`rev_*`), now over
the `edges` table only. Groups add three families, all mmapped like the rest:

- **per-src group rows** — `grp_indptr[N+1]`, `grp_set/grp_rel/grp_conf[G]`:
  the forward hop ("this src widens through these sets").
- **the sets themselves** — `set_indptr[S+1]`, `set_members[M]`: each interned
  set's member node indices, stored once.
- **the transposes** — `setuse_*` (the same G rows re-sorted by set: "which
  srcs use this set", the reverse hop's second half) and `mem_*` (per-node
  containing sets: the reverse hop's first half).

`manifest.version = 2`; a v1 directory fails the version check and rebuilds —
the sidecar is disposable by contract, so no migration path is needed.

## How each sweep family reads it

- **BFS (`reachable`/`reverse_reachable`)**: after the flat gather, a two-level
  group hop. Forward: frontier → masked group rows → **unique** sets → members.
  The dedup before expansion is the point — a 64-arm homonym set referenced by
  hundreds of frontier sites is gathered once per round, not per site. Reverse
  runs the transposes: node → containing sets → masked `setuse` rows → srcs.
- **`_label_sweep`** (bit-parallel `reachable_many`/`reach_hits`): the same
  two-level hop, label-carrying — OR the frontier labels into each touched
  *set* (`bitwise_or.at` over S slots), then broadcast each set's accumulated
  label to its members with one `repeat`. Per-lane fixed-point semantics are
  unchanged: a node re-enters the frontier only on new bits.
- **degrees (`fan_in`/`fan_out`)**: vectorised, never expanded. Fan-out: each
  masked group row weighs `set_size[its set]`; one `reduceat` per src. Fan-in:
  masked row count per set (`reduceat` over `setuse`), then per node summed
  over its containing sets (`reduceat` over `mem`).
- **`self_loops`**: "src inside its own candidate set", probed against sorted
  packed `(node, set)` membership keys — no expansion.
- **`scc`/`articulation` (`_filtered_csr`)**: the per-edge walkers genuinely
  visit every logical edge, so this path expands kept groups **transiently** —
  the same order of memory the v1 arrays held on disk, paid only when those
  operations run. Within a row the order is flat edges then group expansions
  in stored order, preserving the `edges_all` UNION-ALL order the scan
  differential pins.

The v3.40.0 row overlay is untouched: `apply_delta` reads `edges_all` (already
expanded, bounded ≤ 2048 rows), and the delta triggers were group-aware since
v3.41.0 (`_adjg_ins/_adjg_del` capture every member dst). Overlay rows REPLACE
a node's whole base row, so `_split_frontier` keeps overlaid nodes out of both
the flat and the group base gathers.

## Parity discipline (unchanged from v1)

Edges to nodes with no row are skipped (panel R29A) — for groups this filters
*members* once globally, which is identical to per-site filtering because the
skip depends only on the dst id. Unknown-relation rows are skipped. Rebuild
aborts if the store's generation moves mid-read.

## Gate

- The structural-identity oracle (`test_adjcache_structurally_identical`) now
  compares per-node **logical** neighbour triples — flat segment plus expanded
  group arrays, the same code on both caches — and asserts the compressed
  side's group arrays are non-empty so it can never pass vacuously.
- Every accelerated-vs-reference equivalence test in `test_adjcache.py` runs
  with compression on by default, so the group hops sit on the pinned path.
- HA field probe: full-dict `fan_in`/`fan_out` equality against SQL GROUP BY
  ground truth (both confidence modes), forward/reverse BFS against a
  pure-Python BFS over one `edges_all` scan, bit-parallel lanes against
  sequential sweeps.

## Results (HA field index, 58,998 nodes / 16.1M logical edges)

| | v1 (expanded) | v2 (shared) |
|---|---|---|
| on-disk | 162 MB | **12 MB** |
| build | ~74 s | **2.5 s** |
| rows written | 16,115,797 × 2 directions | 174,674 flat + 160,757 group + 200,885 member |

The build win is exactly the row-count win: the per-row Python translation
loop now visits the shared form (~536k rows) instead of the logical form
(~32M row-direction visits).

Equivalence, all EQUAL on the live field index:

- `fan_in`/`fan_out` full-dict identity vs SQL GROUP BY ground truth
  (R29A-filtered), both confidence modes — each answered in 0.02 s.
- Forward BFS (6 seeds, both confidence modes) and reverse BFS (6 targets)
  vs a pure-Python BFS over one `edges_all` scan (the 45.9 s / multi-GB
  reference build the sidecar exists to avoid).
- `reachable_many`: 12 bit-parallel lanes == sequential sweeps.

Traversal scale exercise (CALLS+IMPORTS): SCC over all 58,998 nodes in
2.0 s (44,688 components, 153 multi-node), `self_loops` 4,691 in 0.0 s,
articulation 5,600 cut vertices in 22.9 s at 1.39 GB peak RSS — the
documented transient expansion, paid only by the per-edge walkers.
