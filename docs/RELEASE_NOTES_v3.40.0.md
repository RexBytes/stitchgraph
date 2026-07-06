# v3.40.0 — the edit-loop release

*2026-07-06 · the two performance recommendations from the post-v3.39 review:
parallel extraction and the incremental sidecar · details: `CHANGELOG.md`*

## Incremental sidecar refresh

The v3.38 incremental `watch` made store writes proportional to the edit; the
sidecar still paid a full rebuild on the next query (~2 minutes at HA scale).
Now `replace_file` captures every edge row it touches — via temporary SQL
triggers, so the worklist/re-widening/override side effects are covered by
construction — and the sidecar loader patches a per-node row overlay from that
delta instead of rebuilding. The BFS family (dead-code sweeps, impact,
audit_graph batches, fan-in/out) reads through the overlay; SCC/articulation
conservatively fall back to the reference path; anything the delta chain can't
prove (gaps, oversize, node deletions) forces the full rebuild. The overlay
serves **stale-safe or not at all**, and byte-equality with a fresh rebuild is
pinned by tests. Net: the edit→query loop is now incremental end to end.

## Parallel extraction — and the measurement that matters more

A fork-based pool runs both extractor passes on all cores, byte-identical to
the serial reference (differential oracle). It auto-enables only where it
wins — in-memory extraction (Django 5.2: **67 s → 50 s**) — because the
honest end-to-end benchmark showed the streaming reindex is bounded by edge
materialisation + SQLite insertion, which no parse pool touches (181 s serial
vs 190 s parallel). That negative result is recorded in `docs/PERFORMANCE.md`:
**index time at framework-Python edge density scales with edge volume, not
parsing** — making homonym-group edge compression the one remaining
step-change, now the standing next arc in `docs/STATUS.md`.
