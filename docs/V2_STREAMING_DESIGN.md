# v2: constant(-ish)-memory streaming indexer — design

> **Correction (2026-07-03, fixed in v3.28.0):** until v3.28.0 the *Python* extractor did
> not actually stream — `extract/__init__.py` drained its edge list to the sink only after
> `python.extract_project` fully materialised it, so `edge_sink` bought zero memory
> reduction on the Python path. Every large-scale validation below (Magento) happened to
> route through tree-sitter, the one extractor with the sink wired in; a field report of
> Home Assistant (~10k `.py` files, heavy homonym fan-out) OOMing at ~7 GB despite
> `streaming=True` exposed the boundary. v3.28.0 makes the Python extractor drain per
> pass-2 file (INHERITS teed for the post-passes; override widening delegated to the
> store twin), measured at **8.6M edges / 50 MB peak**, and adds a hard-RLIMIT CI gate so
> constant-memory is a *tested invariant*, not a claim — the pre-fix code demonstrably
> dies at the gate's cap at the exact `_ref_edges` append the diagnosis named.

Goal: index a tens-of-thousands-of-file monorepo (Magento: 24k files) without holding the
whole graph in RAM. Today `reindex` builds **all ASTs + all nodes + all edges** in memory,
then runs **resolvers + role-seeds + dedup over those full lists**, so peak RAM scales with
repo size (Magento > 12 GB). See `LIMITATIONS.md`.

The non-negotiable invariant: a streaming index must be **byte-identical** to the in-memory
one. The gate is `tests/oracles/test_streaming_differential.py` (`streaming == full`); every
phase below ships only when that stays green on the dogfood + the hunt corpora.

## Shipped

- **Phase 1 — AST-resident peak removed.** `extract_project(..., cache_asts=False)` /
  `reindex(..., streaming=True)` drop each file's AST after pass 1 and re-parse in pass 2
  (~2× parse CPU, no all-ASTs-resident step). Identical output.
- **Phase 2a — `__slots__` on Node/Edge.** Edges outnumber nodes ~6:1; slots removes the
  per-instance `__dict__`, ~halving object overhead (~150–200 MB at Magento scale).
- **Phase 4 — tree-sitter re-parse streaming (Magento/PHP).** `treesitter.extract(..,
  cache_trees=False)` (driven by the same `reindex(streaming=True)` flag) drops each file's
  parse tree **and** source bytes after pass 1. While the tree is still alive it precomputes
  the only things pass 2 + the seeds read back from a body — call/ref tuples, node type, C/C++
  out-of-line scope, Rust trait-impl flag — into a tiny `_DefInfo` record (replacing the body
  ref in `defs`). This removes the double-pin (`src_by` + tree refs) that was Magento's actual
  hog. Identical output, gated by the polyglot streaming oracle (now incl. PHP, Rust trait
  impl, C++ out-of-line, TS interface).
- **Phase 2b/3 — stream edges to SQLite (the v2.0.0 core).** Profiling a Magento module
  (`lib/`, 4304 files) showed the real hog: the tree-sitter extractor produced **15.5M edges**
  for 30k nodes (~500:1, name-based ambiguous fan-out across PHP's many homonyms) — ~4 GB of
  Python edge objects, the bulk of `reindex`'s peak. Three facts make streaming them out
  safe: (1) within extraction `edges` is *write-only* — override propagation is Python-only
  (small Python edge set) and dedup is deferred; (2) resolvers read only the node list +
  source, never the edges; (3) every dedup key is scoped to the edge's `src`. So `reindex`
  keeps the (far smaller) node list resident, and an append-only `_StoreEdgeSink` consumes
  edges as they're produced. Because the pass-2 loop emits a definition's edges consecutively,
  the sink dedups each source group on the fly (reusing `_dedup_edges`) and writes only the
  ~3.9M survivors in committed `executemany` batches — never the 15.5M raw rows (which would
  also blow the DB to ~9 GB). A final `_dedup_resolved_edges` in the store is the authoritative
  global pass (cross-group same-src + resolver edges). **Result: `reindex` peak 3183 MB → 269
  MB (~12×, GB→MB), byte-identical** (3,926,345 edges / 30,412 nodes verified row-for-row vs
  the in-memory path), ~40% slower. Resolvers stay in memory over the node list — that's
  already constant w.r.t. the edge explosion, so Phase 3 needs no separate store rewrite.

## The remaining refactor (the v2.0.0 core)

Peak is now dominated by the node+edge objects + the resolver/seed working set. The key
realisation that makes streaming tractable:

> The role-seeds only need **lean node records** (`id, name, kind, roles`) and the **INHERITS
> edges** — never the bulk `CALLS`/`REFERENCES` edges. And the store already has a proven
> `_propagate_overrides` (the incremental oracle shows `replace_file == full`).

So the bulk edges (the hog) can stream straight to SQLite and never materialise in Python.

### Architecture

1. **Pass 1 (defs):** stream files; per file collect defs → write Node rows to the store →
   keep only the **lean symbol index** (`by_name`, `class_by_name`, `module_by_qual`,
   `exported_names`, `packages`, `source_prefix`, `module_consts`, `main_calls`,
   `external_base_classes`) + a **lean node table** (`id→(name, kind, roles)`). Free the
   Node objects and AST.
2. **Pass 2 (edges):** stream files (re-parse), resolve each file's edges against the lean
   index, **write Edge rows to the store**, and tee only `INHERITS` edges into a small
   in-memory list. Free the bulk edge objects.
3. **Role-seeds (lean, in Python):** run the existing seed logic over the lean node table +
   the INHERITS list — name/script/entrypoint/callback/test/inherited/dunder. Dunder seeding
   writes its new `REFERENCES` edges to the store. Then flush role changes back with
   `UPDATE nodes SET roles`.
4. **Override widening + dedup (in the store):** reuse `Store._propagate_overrides` /
   `_dedup_resolved_edges` (already proven equivalent to full).
5. **Reachability** (`find_stale`, `impact_of`) already runs over the store.

Peak Python memory ≈ lean node table + INHERITS edges + one file's working set — scales with
**symbol count**, not full-object/edge count (~17 MB of lean nodes for Magento vs ~11 GB
today). Not strictly O(1); true flat memory would push the lean node table to SQLite too,
done only if O(symbols) is still too big.

### The hard part / open problems

- ~~**Resolvers.**~~ *Resolved.* `run_resolvers` reads only the node list + source (never the
  edge list — verified), so it stays in memory over the resident nodes; its few extra edges
  stream through the same sink. The node list is already small relative to the edge explosion,
  so this is constant-memory without a store rewrite.
- ~~**tree-sitter extractor.**~~ *Done (Phase 4).* Resolved by precomputing each def's pass-2
  inputs while its tree is alive, rather than re-parsing in pass 2: a re-parse can't recover
  the same body node refs, so instead the call/ref/scope tuples (which are all pass 2 needs)
  are captured into `_DefInfo` and the tree + source are freed per file.
- **Seed parity.** Each seed reformulated on lean records must produce identical roles —
  driven entirely by the streaming differential oracle.

### Phased rollout (each gated by the oracle)

- ~~**Phase 2b** — stream nodes+edges to the store.~~ **Shipped** (see above): edges stream
  through `_StoreEdgeSink` with on-the-fly per-source dedup; nodes stay resident.
- ~~**Phase 3** — resolvers over the store.~~ **Shipped** (resolvers need only nodes+source).
- ~~**Phase 4** — tree-sitter re-parse streaming (Magento/PHP).~~ **Shipped** (see above).
- **Phase 5** — validate on Magento end-to-end; make streaming the default (or auto above a
  file-count threshold). → **v2.0.0**.
