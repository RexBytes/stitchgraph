# v3.43.0 — the edit loop at any scale

*2026-07-06 · dependency-free batch ①: the persistent symbol table ·
design + field story: `research/21-persistent-symtab.md` · details:
`CHANGELOG.md`*

## The gap

`watch`'s incremental path extracts the WHOLE project in memory to guarantee
resolution semantics identical to a full reindex — so on trees past the
streaming threshold (~2k files) it fell back to a full rebuild per edit:
~4 minutes at Home Assistant scale, even after v3.41's compression. An edit
loop wants seconds.

## The fix

Everything single-file extraction needs from other files already lives in the
index — except four raw name-sets that never become graph objects (module
constants, pytest fixtures, the export surface, `__main__` calls). Those now
persist per file in a `symtab` table, and a changed file is re-extracted by
the extractor's own two passes running against store-backed views of the
whole-project symbol tables. The proven `replace_file` machinery lands the
result; the complete post-edit exported surface is recomputed store-side, so
a changed `__all__` re-tags other files' nodes exactly as a full reindex
would. Honest gating throughout: edits that could involve the cross-language
resolvers (each check mirrors that resolver's exact firing shape), deletions,
old indexes, or unparseable files decline the fast path and take today's
routes.

## Measured (Home Assistant 2024.3.3, 6,725 files, 16.1M logical edges)

| per-edit cost | before | after |
|---|---|---|
| watch on a streaming-scale tree | ~4 min (full rebuild) | **13.6 s** |

Convergence is pinned by an 11-shape oracle matrix: the single-file end state
equals the whole-project incremental AND a fresh full reindex — rows, holes,
roles, and symbol-table state alike.

## The field probes paid for themselves again

Three scale defects no test corpus could surface, found live and fixed —
including one that makes v3.41's `replace_file` safer for everyone: the
expand-affected universe is now the edit's *delta*, not every name the file
defines (a one-function edit to a hot-homonym file previously tried to
flatten 11.5M rows), and the per-edit dedup/re-widening passes are scoped to
what the transaction touched instead of sweeping the whole graph. The one
remaining known cost (`_propagate_overrides` re-derivation, ~half the
residual latency) is documented in research/21 with its optimisation
deliberately reserved for its own careful pass.
