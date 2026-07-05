# v3.30.0 — the adjacency-sidecar release

## Where this came from

A design discussion about 0/1 matrices: since the relation projections are boolean,
could bit-packed storage beat the database? The answer split in two. As the *primary*
representation — no: the graph is 0.46% dense (16M edges over a 59k² bit space), so a
dense bitmap wastes 99.5% of its bits, and the rows carry provenance/weight/location
that a bit can't hold; SQLite stays the source of truth. But as the *query acceleration*
layer — emphatically yes, and a prototype on the Home Assistant field graph
(16M edges, 10 GB index) made the case overwhelming: every reachability sweep was
rebuilding a `dict[str, list[str]]` of Python strings from SQLite — ~130 s and ~2 GB
per op — and throwing it away.

## What shipped

**`<db>.adjcache/`** — a derived, mmapped CSR sidecar (`core/adjcache.py`):

- forward + reverse CSR (`int64` indptr, `int32` indices) over all resolved edges,
  `uint8` relation codes, and a **packed per-edge provenance bitmask** — 2 MB for 16M
  edges, probed in the BFS inner loop with shift-and-mask. The EXTRACTED-only closure
  (scan's certainty pass) reads one bit per edge where the old path constructed an
  `Edge` object per row.
- serves `reachable_from`, `reverse_reachable_from`, `fan_in`, `fan_out`; dispatch is
  sidecar → GraphBLAS → pure Python, all three pinned identical by equivalence tests.
- **built lazily by the first sweep** after a (re)index — deliberately NOT inside
  `reindex`, whose streaming path carries a hard 130 MB `RLIMIT_AS` CI gate that numpy
  plus build arrays would violate.

## The contracts

- **SQLite is authoritative; the sidecar is disposable.** Deleting the directory is
  always safe — the next sweep rebuilds (or falls back).
- **A sweep can never read stale adjacency.** A `generation` counter in `meta` — bumped
  by both `reindex` paths, `replace_file`, and the invalid-root wipe — is recorded in
  the sidecar manifest and checked with the node count on every open; mismatch refuses
  the cache. Pinned by a test that severs a dependency via `replace_file` and asserts
  the closure shrinks.
- **Absence degrades to exactly the old behaviour**: no numpy (guarded import — core
  stays stdlib-only), `:memory:` stores, read-only index directories (failed builds
  memoised per generation: one attempt per index state, not one per sweep), or
  `[index] adjacency_cache = false`.

## Measured (16M-edge field graph, 59k nodes, 10 GB index)

| | v3.29.0 | v3.30.0 warm | factor |
|---|---|---|---|
| `find_stale` | 119 s / 1.97 GB | **2.1 s / 516 MB** | ~57× |
| `find_stale`, cold first sweep | — | 171 s (one-time lazy build) | — |
| `scan` | 625 s / 2.09 GB | **371 s / 1.92 GB** | 1.7× |
| adjacency for one sweep | ~130 s rebuild, per op | 0.03 s mmap open | ~4,000× |
| sidecar on disk | — | 161 MB | (10 GB db) |

Field results are byte-identical across the switch: 1,703 stale candidates, 11,619
scan issues — the same numbers as the v3.29.0 field record (`research/16-ha-field-analysis.md`).

`scan` improves only 1.7× because its SCC pass still builds the Python adjacency;
routing Tarjan (and `articulation_points`) over the CSR ints is the recorded follow-up,
alongside provenance-filtered GraphBLAS matrices.

## Tests

- Equivalence differential: all four accelerated ops plus the `confident_only` closure,
  sidecar vs pure-Python reference, on a provenance-mixed corpus (the confident closure
  is asserted a *strict subset*, so the filter is provably exercised).
- Staleness: `replace_file` bump → old sidecar refused → fallback correct → rebuild
  reflects the edit. Tamper (manifest generation) → refused.
- Lazy-build placement: reindex itself must NOT create the sidecar (the constant-memory
  gate), the first sweep must.
- Degradation: numpy absent, `:memory:`, config off, build-failure memoisation.
- Full suite green, including the streaming byte-identity oracles and both memory gates.
