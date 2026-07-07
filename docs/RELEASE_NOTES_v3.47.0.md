# v3.47.0 — the dogfood release

*2026-07-07 · scan and orient, calibrated by pointing v3.46.0 at its own
repository · story: `research/25-dogfood-v3.46.md` · details: `CHANGELOG.md`*

## The premise

The morning after v3.46.0 shipped, we indexed stitchgraph with it. It found
real things (a dead function, three dead parameters — removed in v3.46.0's
follow-up commit) and produced ~390 findings of structured noise. This
release fixes the noise at its patterns, verified by re-running the same
battery.

## Before / after (same repository, same battery)

| | v3.46.0 | v3.47.0 |
|---|---|---|
| scan findings | 435 | **45** |
| god_object | 340 (252 in src/, ORANGE) | **0** (honest: max real coupling ~50 vs scaled floors 62/29) |
| unused_params | 52 | **5**, all genuine |
| phantom holes | 2 | **0** |
| #1 orient hub | `Store.close` (test mass) | `Provenance` (all-src mass) |

## What changed

1. **God-object floors scale with the graph.** With ≥ 200 coupled code
   nodes, a god object must sit strictly above the population's 95th
   percentile of coupling in both directions; small graphs keep the
   historical absolute floors byte-identically. Signal, not census.
2. **Orient hubs exclude test mass.** Test-owned nodes neither appear in
   the hub list nor count as dependency mass in the transitive metrics —
   they still route reachability, they just aren't dependers. A suite that
   closes 1,117 stores no longer crowns `Store.close` the #1 thing to read.
   (Explicitly-chosen raw `fan_in`/`pagerank` keep degree semantics.)
3. **Unused-parameter advisories understand interfaces.** Three
   suppressions: decorator-registered functions (the framework consumes the
   signature), family-interface slots (a param a same-name same-arity
   sibling loads), and value-referenced functions (an incoming REFERENCES
   edge — a dispatcher owns the shape). The advisory that survived all
   three was *right*: 28 family-wide dead parameters in the per-language
   builder files, now underscore-renamed.
4. **`try/except` and `if/else` module constants extract properly** — the
   `_HAVE_X = True/False` idiom no longer leaves phantom import holes.

## Compatibility

No schema change, no API change, no new dependency. Small graphs see
byte-identical scan behaviour; mid-size and larger graphs see dramatically
fewer, dramatically better-ranked findings.
