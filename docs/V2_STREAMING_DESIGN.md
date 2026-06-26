# v2: constant(-ish)-memory streaming indexer — design

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

- **Resolvers.** `run_resolvers` (routes/SQL/ORM/cross-language) currently iterates the full
  in-memory `(nodes, edges)` and *adds* edges. It must move to operate over the store (or a
  streamed view) — the largest single sub-task. For a pure-Python repo it adds little, but the
  design isn't constant-memory until resolvers stream too.
- **tree-sitter extractor.** It holds every file's source bytes (`src_by`) *and* the parse
  trees (the `defs` list holds body-node refs) across both passes — Magento is PHP, so this is
  Magento's actual hog. Streaming it needs a re-parse-in-pass-2 restructure analogous to
  Python phase 1, but harder (tree-node refs can't survive a re-parse, so pass 2 must re-walk).
- **Seed parity.** Each seed reformulated on lean records must produce identical roles —
  driven entirely by the streaming differential oracle.

### Phased rollout (each gated by the oracle)

- **Phase 2b** — stream Python nodes+edges to the store; lean seeds over (lean nodes +
  INHERITS); reuse store `_propagate_overrides`/dedup. (Python path constant-ish.)
- **Phase 3** — resolvers over the store.
- **Phase 4** — tree-sitter re-parse streaming (Magento/PHP).
- **Phase 5** — validate on Magento end-to-end; make streaming the default (or auto above a
  file-count threshold); evolve the public API (`extract_project`'s `(nodes, edges)` return is
  incompatible with streaming → the semver-major trigger). → **v2.0.0**.
