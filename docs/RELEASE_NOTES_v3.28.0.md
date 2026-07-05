# stitchgraph v3.28.0 — release notes

**The constant-memory release.** A field report (2026-07-03) falsified our core scale claim:
Home Assistant — a pure-Python monorepo with heavy homonym fan-out — OOM'd at ~7 GB with
`streaming=True`, while a PHP repo twice its size had validated at 269 MB. Two defects, same
root cause, both fixed, and constant-memory is now a *CI-tested invariant* instead of a claim.

## Fixed

- **The Python extractor never streamed.** `extract/__init__.py` drained the extractor's edge
  list to the sink only *after* `python.extract_project` fully materialised it — `edge_sink`
  bought zero memory reduction on the Python path. Every large-scale validation (Magento) had
  happened to route through tree-sitter, the one extractor with the sink actually wired in.
  The extractor now drains to the sink after each pass-2 file: `INHERITS` edges are teed into
  a small list for the role-seed post-passes (the only relation they read), and override
  widening is delegated to the store twin. A/B on an identical 610-file corpus: pre-fix
  **412 MB and linear** (~190 B/edge), post-fix **43 MB and flat**; a 1,212-file corpus
  producing **8.64M edges peaks at 50 MB**.
- **The endgame override widening was O(edges) in Python.** `Store._propagate_overrides` —
  the DB twin that replaces the extractor-side widening in sink mode — `fetchall()`'d every
  resolved CALLS/REFERENCES row plus a seen-set of their key tuples. On Home Assistant's
  16.15M-edge graph that re-OOM'd the *endgame* after the whole index had streamed at a flat
  ~113 MB. It now touches only symbol-scale data in Python (class ids, the INHERITS closure,
  distinct edge *targets*) and runs the edge scan + widened inserts inside SQLite, snapshot
  semantics and first-triggering-edge templates preserved exactly (the streaming and
  incremental differential oracles pin byte-identity). On the same HA graph: **completes in
  ~160 s at 113 MB peak**, adding 81,345 override edges.
- **The final holes tally materialised an `Edge` per hole.** `_reindex_streaming` now uses
  `Store.unresolved_count()` (COUNT twin, same predicate) instead of
  `len(store.unresolved_edges())`.
- The endgame covering index `(src, relation, dst_id, weight)` is created *before* override
  widening (its NOT-EXISTS probes use it), not just before the global dedup.

## Added

- **A hard memory-regression gate**: `test_streaming_python_edges_bounded_memory` indexes a
  450-file homonym-fan-out corpus (~1.2M edges) **with class hierarchies and overrides** in a
  subprocess under a 130 MB `RLIMIT_AS` hard cap. Falsified in both directions: the pre-fix
  extractor dies at the exact `_ref_edges` append the field diagnosis named, and the pre-fix
  store widening dies at its `fetchall` — the fixed path passes with ~3× headroom. The
  inheritance in the corpus is load-bearing: the first cut of the gate had none, the widening
  early-returned, and the gate passed while real HA still OOM'd in the endgame ("test the
  test", twice).
- `Store.unresolved_count()`.

## Validation

- **Home Assistant 2024.3.3** (6,728 files, 58,998 nodes, **16.0M edges**): the repo that
  OOM'd at ~7 GB now completes a clean end-to-end streaming reindex under a **4 GB
  address-space ulimit** in **34 min at 158 MB peak RSS** — extraction streams at a flat
  ~115 MB, and the endgame (covering index + override widening + global dedup, run
  separately on the 16.15M-edge graph the failed run left behind) takes ~5 min at 113 MB
  peak where the old code died with MemoryError.
- Streaming differential oracle (streaming == full, byte-identical), including a new
  HA-shaped fixture (homonym methods × inheritance × override fan-out): green.
- Full suite green.

## Docs

- `README.md` (Scale), `docs/V2_STREAMING_DESIGN.md`, and `LIMITATIONS.md` corrected: the
  measured numbers replace the aspirational claim, with the correction history kept visible.
