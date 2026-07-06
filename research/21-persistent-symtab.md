# 21 — Persistent symbol table: single-file extraction at any scale

*2026-07-06 · dependency-free batch ① · the design for extending the v3.38
incremental watch past the AUTO-streaming threshold.*

## The gap

`reindex_incremental` extracts the WHOLE project in memory — that is what
guarantees resolution semantics identical to a full reindex (the v3.38
convergence contract) — and writes only the changed owners via
`replace_file`. On trees big enough for AUTO-streaming (~2k files), that
in-memory whole-project extract is exactly what streaming exists to avoid, so
the watch loop falls back to a FULL reindex per change: 4 minutes at Home
Assistant scale post-compression. An edit loop wants seconds.

## What single-file extraction actually needs (survey result)

Pass 2 of the Python extractor resolves one file's references against
cross-file state assembled between passes. The complete inventory, by where
the data lives today:

**Already in the store (derivable by SQL):**
- `by_name` (name → node ids): `nodes.name` + `idx_nodes_name`
- `class_by_name`: `nodes WHERE kind='class' AND name=?`
- `ids`: the `nodes` primary key
- `module_by_qual` / `module_ids`: `nodes WHERE kind='module'` (+ the
  source-prefix alias, reconstructable)
- the INHERITS tee (subclass maps for override/role propagation): `edges
  WHERE relation='inherits'` — the store's `_propagate_overrides` twin
  already rebuilds exactly this
- `external_base_classes`: recomputable from INHERITS rows with no
  first-party target (treesitter's `_framework_classes` pattern)
- `packages` / `source_prefix`: derivable from `nodes.file` paths; cheap to
  persist as meta at reindex time instead of re-deriving

**Missing — the four raw pass-1 name-sets that never become graph objects:**

| set | pass-2 / post-edge consumer | why it must persist |
|---|---|---|
| `module_consts` | `_import_edge` phantom-hole suppression | file A's import of B's tuple-unpack constant needs B's set |
| `fixture_names` | fixture-aware test rooting (param binding) | a conftest fixture roots tests in OTHER files |
| `exported_names` | `exported` role assignment + inherited-method seeding | an `__init__`'s `__all__` tags nodes in OTHER files by name |
| `main_calls` | `main` role assignment + entrypoint-class seeding | same cross-file name matching |

The role OUTCOMES persist in `nodes.roles`, but recomputing roles after an
edit needs the raw per-file sets (the union changes when one file's
contribution changes).

## Design

**New store state — one table, four kinds:**

```sql
CREATE TABLE IF NOT EXISTS symtab (
    file TEXT NOT NULL,      -- owning source file (replace_file's delete key)
    kind TEXT NOT NULL,      -- 'const' | 'fixture' | 'export' | 'main'
    name TEXT NOT NULL,
    PRIMARY KEY (file, kind, name)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_symtab_kind ON symtab(kind, name);
```

Populated by both reindex paths (pass 1 already computes every entry) and
maintained by `replace_file` (delete `file=?`, insert the fresh extract's
contribution). Plus two meta keys: `packages`, `source_prefix`.

**A store-backed `_Project` shim.** For a changed Python file F:
1. Pass 1 on F alone (`_collect_defs`) → F's nodes + its four name-set
   contributions.
2. Assemble a `_Project` whose symbol tables are LAZY store views: `by_name`
   et al. answer from SQL over `nodes` — minus F's OLD rows, plus F's fresh
   pass-1 nodes — and the four name-sets are `symtab` unions with F's row
   replaced. A dict-like wrapper keeps `_ref_edges`/`_resolve_member`
   untouched (they only `.get()` names, a handful per reference; each is one
   indexed lookup, memoised per run).
3. Pass 2 on F alone (`_collect_edges`) → F's edges, resolved with the exact
   semantics of the whole-project run by construction (same code, same
   tables, same filters).
4. `replace_file(F, ...)` — whose worklist / re-widening / override /
   dangling machinery already handles every cross-file EDGE consequence and
   is pinned by the v3.38 convergence oracles.
5. Role recomputation: rebuild the four unions from `symtab` and re-apply
   `_apply_entrypoint_roles`-equivalent tagging in the store (the
   `exported_ids` contract `replace_file` already honours, extended to
   `main`/`fixture`-derived tags), so a changed `__all__` re-tags other
   files' nodes exactly as a full reindex would.

**Scope: Python first, honestly gated.** Tree-sitter files keep the current
whole-project incremental path (their `by_lang` buckets follow the same
pattern later; `same_lang` is already a store function). `reindex_incremental`
gains the single-file mode only when every changed file is Python and the
tree is past the in-memory comfort threshold; everything else keeps today's
behaviour. Deletions keep the documented full-reindex fallback.

**The oracle (the release gate).** For a matrix of edits (add/remove
function, add/remove homonym, change `__all__`, add fixture, add subclass of
another file's class, add import of a module constant) on a multi-file
corpus: single-file-mode end state == `reindex_incremental` end state ==
fresh full reindex, through rows (`edges_all`), holes, roles, and the
operation battery. Field measurement: watch-loop edit latency on the HA
index (target: seconds, from 4 minutes).

## Field results (Home Assistant 2024.3.3, 6,725 files, 16.1M logical edges)

The edit loop this arc exists for — touch one component file, re-query:

| path | latency |
|---|---|
| full streaming reindex (what watch paid before, per edit) | ~4 min |
| `reindex_singlefile` (stages A–C, after the field fixes below) | **13.6 s** |

Stable across repeated edit/revert rounds; end state verified (probe node
present after edit, gone after revert). ~17× — and the field probes found
and fixed three real scale defects on the way:

1. **The resolver gate was keyword soup**: the first draft declined every
   file (`.get(` is every Python file). Now each check re-states its
   resolver's own firing shape (route verbs in decorator position with a
   string path; the events arg shapes; the sql resolver's `_SQL_RE` reused).
2. **The expand-affected universe was old∪new, not the delta**: an HA
   component file defines the graph's hottest homonyms, so a one-function
   edit tried to flatten 3,651 groups (11.5M rows) and filled the disk. A
   group's set can only change when a NAME's defining-id set changes or a
   contained id is removed/re-kinded — the precise predicate keeps everything
   else compressed. (This also fixes plain `reindex_incremental` at scale.)
3. **Whole-graph passes per edit**: `_dedup_resolved_edges` (26 s of
   correlated sweep; keys are src-local, so it now scopes to the
   transaction's touched srcs, JOIN-driven past the stat-less planner trap)
   and `_rewiden_resolved` (per-group queries confirming foregone
   conclusions; now scoped to affected names).

**Remaining known cost**: `_propagate_overrides` still derives the full
override map per edit (~half the residual 13.6 s). Its scoping (bases whose
subtree or member set changed + new bound targets) is the next optimisation,
deliberately left for its own careful pass — the semantics are the
subtlest of the three and the current latency is already edit-loop usable.

## Addendum (post-v3.43.0): the reserved pass, and the real bottleneck

Two further changes took the HA edit loop from 13.6 s to **3.7 s (65× from
the original 4 min)**, steady across repeated rounds:

1. **Scoped override derivation** — the pass is additive and
   NOT-EXISTS-guarded over an override-complete state, so an edit can only
   create missing rows through three doors: newly bound targets (touched
   srcs' edges), new members (added ids overriding ancestor members), and new
   INHERITS links (an existing subtree grafted under new ancestors —
   candidate targets enumerated by primary-key range over the base chain's
   members). Correct, ring-green — but it barely moved the wall clock,
   because the profile was misread:
2. **The actual bottleneck was the fresh batch itself**: `replace_file`
   inserted the edited file's widened fan-out FLAT (tens of thousands of
   arms for a hot-homonym HA component) and compressed only at transaction
   end — so add_edge, the worklist, dedup, and the override join all waded
   through rows that were about to be interned anyway. Deduping the batch in
   memory (the file-local twin) and partitioning it into groups up front —
   the streaming sink's own per-source-complete argument — collapsed every
   downstream pass at once. Bonus: the project's convergence-test ring got
   ~35% faster for the same reason.

| HA edit loop (components/light/__init__.py) | latency |
|---|---|
| full streaming reindex (pre-v3.43) | ~4 min |
| v3.43.0 as released | 13.6 s |
| + scoped overrides + ingest-compressed batch | **3.7 s** |

## Known seams to carry honestly

- Cross-file effects that today's `reindex_incremental` itself does not
  propagate (its contract is "changed owners only") stay as they are: the
  oracle is equality with the CURRENT incremental path first, full-reindex
  convergence second — any pre-existing gap found gets documented, not
  silently widened.
- `_getattr_dispatch_edges` scans by_name KEYS by prefix/suffix — a lazy
  view can't answer that from a `.get()`; it needs one `SELECT name FROM
  nodes WHERE name LIKE ?` per dispatch site (indexed prefix form) or a
  bounded key materialisation. Sized in implementation.
