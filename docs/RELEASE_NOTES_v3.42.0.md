# v3.42.0 — hubs at any scale

*2026-07-06 · sampled transitive fan-in: orient's best ranking past the exact
closure's node cap · details: `CHANGELOG.md`; field numbers:
`research/20-homonym-compression.md`*

## The gap

`orient`'s default hub metric — transitive fan-in, "how many distinct nodes
depend on this, directly or not" — was computed as an exact boolean closure,
which densifies catastrophically on big graphs. Above 4,000 nodes it refused,
and orient silently degraded to *direct* fan-in: still useful, but a
one-hop count is not "read these first". Every framework-scale codebase got
the weaker ranking.

## The fix

A node's transitive fan-in is just its distinct-ancestor count — and that is
estimable without any closure: sweep forward from a sample of S sources,
count per node how many sampled sources reach it, scale by n/S. The v3.39.0
bit-parallel BFS machinery answers 64 sources per fixed-point sweep over the
mmapped sidecar, so the default 1,024 samples cost 16 sweeps — seconds at
scales the closure cannot touch at all. When the sample budget covers the
whole graph the result is exact (no estimation), which also hands small
no-GraphBLAS installs the true transitive ranking for the first time.

Honesty is kept: a sampled result is named `transitive_fan_in_sampled` in the
result envelope, the sample is deterministic (repeat runs rank identically),
and the ~1/√hits relative error is tightest exactly where ranking matters —
the top hubs.

## Field (Home Assistant 2024.3.3, 59k nodes, 16.1M logical edges)

`orient` now returns `HomeAssistant`, `ConfigEntries`, `AuthManager`, and
`HomeAssistantHTTP` as the top hubs — the classes a HA developer would
actually tell you to read first — where this scale previously fell back to
direct fan-in. Ran on the v3.41.0-compressed 317 MB index, on a machine whose
free disk could not have held the flat representation at all.
