# v3.41.0 — the compression release

*2026-07-06 · homonym-group edge compression, the arc v3.40.0's honest
benchmark pointed at · design + measurements: `research/20-homonym-compression.md`
· details: `CHANGELOG.md`*

## The problem

To never flag live code dead, a bare `obj.work()` call links to **every**
`work` in the index — one stored row per candidate per call site. On Django
5.2 that over-approximation is **96.2% of all resolved edge rows** (644K of
670K), and v3.40.0 proved index time scales with exactly that row volume.
But the candidate *sets* repeat: 644K arms collapse to 1,801 distinct sets
serving 24,762 call sites.

## The change

The store now interns each distinct candidate set once (content-addressed by
its sorted member ids) and stores one row per widened call site. A UNION-ALL
view (`edges_all`) serves every consumer the identical row multiset whether a
fan-out is stored flat or compressed — extractors, resolvers, and the whole
operation surface are untouched. Compression is **opportunistic**: anything
that doesn't fit the pattern stays flat, so correctness never depends on
coverage. Incremental updates use an *expand-affected* discipline — groups an
edit provably touches are flattened, the proven flat-row resolve pipeline runs
unchanged, survivors re-compress — so every convergence guarantee carries over
by construction.

## Measured (Django 5.2, same machine, same tree)

| metric | v3.40.0 | v3.41.0 | factor |
|---|---|---|---|
| full index time | 41.6 s | **24.0 s** | 1.7× |
| index size on disk | 278 MB | **25 MB** | 11× |
| stored edge rows | 669,944 | 63,867 | 10.5× |
| rows served via `edges_all` | 669,944 | 669,944 | = |
| scan / orient / find_stale | 119.6 s / 0.7 s / 0.2 s | 123.2 s / 0.4 s / 0.2 s | = (identical results, 783 issues) |

At Home Assistant scale (26.8M-edge class, 21 GB, ~62 min) the same structure
predicts a low-single-digit-GB index and a substantially shorter build — the
insertion share of ~25M redundant rows simply disappears.

## Safety

The release gate is a differential campaign: compression on-vs-off must be
byte-identical through the row multiset, the full operation battery, both
reindex paths, the incremental edit loop, and the adjacency sidecar's
structure. `[index] edge_compression = false` (or
`STITCHGRAPH_NO_EDGE_COMPRESSION=1`) is the escape hatch and control arm.
One compatibility note: compressed indexes need ≥ 3.41.0 readers; older
readers should reindex (or use the flat gate).
