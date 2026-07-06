# v3.44.0 — 3.7 seconds

*2026-07-06 · the edit loop, finished · field story:
`research/21-persistent-symtab.md` (addendum) · details: `CHANGELOG.md`*

## One number

Editing one file in a watched Home Assistant tree (6,725 files, 16.1M
logical edges) and having the index fully caught up:

| | per-edit cost |
|---|---|
| pre-v3.43 (full streaming rebuild) | ~4 min |
| v3.43.0 | 13.6 s |
| **v3.44.0** | **3.7 s** |

Steady across repeated edit/revert rounds, end state verified each time.

## How

v3.43.0's profile said the remaining time was the store's per-edit passes.
Half true. Scoping the override derivation (the last whole-graph pass — it
now derives only what an edit can actually change) was correct and
ring-green, but barely moved the wall clock. The real bottleneck was hiding
in plain sight: `replace_file` inserted the edited file's widened fan-out
FLAT — tens of thousands of candidate arms for a hot-homonym component —
and compressed it only at transaction end, so every pipeline pass in between
waded through rows that were about to be interned anyway. Deduping the batch
in memory and writing eligible fan-outs as interned groups up front (the
streaming sink's own argument, applied to the incremental path) collapsed
every downstream cost at once.

## The honest witness

stitchgraph's own 2,480-test suite — untouched all day — now runs in 3:13
where it took ~10 minutes this morning. Nobody optimised the tests; the
operations they exercise got cheaper. A workload we didn't design to flatter
the changes, telling the same story as the field numbers.
