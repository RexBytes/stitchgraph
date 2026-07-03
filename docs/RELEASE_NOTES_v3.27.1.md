# stitchgraph v3.27.1 — release notes

**The second dogfood patch.** v3.27.0 was run on itself with the full battery
(`research/15-dogfood-v3.27.md`) — a controlled before/after of the D2 dedup, since
`research/14` measured the same codebase pre-refactor.

## Fixed

- **`structure_common.parse_tree` was dead on arrival.** The D2 stage-2 patch added the shared
  walk-guard helper but the transformation script never wired the nine `_walk` functions to call
  it, and `ruff --fix` then removed the unused imports — hiding the slip from every
  output-equivalence gate, because dead code has no outputs. `find_stale` caught it statically;
  `find_gaps` corroborated at runtime (`untested_dead` = exactly this + the one known advisory).
  It now guards all nine walk entries as intended, and the seven remaining `_walk`-local `text`
  helpers delegate to the shared `node_text` (closing the residual clone hubs `orient` still
  ranked at fan_in 97).

## What the run verified (the interesting part)

- **Behaviour-preservation, measured at runtime:** intrinsic dimensionality **27 → 27** across a
  ~400-line refactor — the strongest runtime statement of "behaviour-preserving" available,
  complementing the byte-identical output oracles.
- **`coverage_drift`'s first real cross-release use** narrated the dedup from coverage alone:
  lost = the nine deleted per-language iterator/closure copies; gained = `structure_common.*`.
  A behavioural changelog derived without reading the diff.
- The refactor is visible in the static graph exactly where intended: nodes 899 → 866, the
  frontend subsystem cluster 329 → 320, `scan` findings 192 → 167 (evaporated name-ambiguity
  artifacts), the same three verified-deliberate oranges.

## Gate

Byte-identical fingerprint/VFG/PDG baseline over the 9-language corpus; the full 1,618-test
oracle battery; ruff + mypy. Post-patch `find_stale` is back to the single known advisory.
