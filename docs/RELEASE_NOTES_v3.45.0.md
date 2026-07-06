# v3.45.0 — the dependency-free batch, closed

*2026-07-06 · variable-granularity data flow + sidecar group-sharing ·
design stories: `research/22-variable-dataflow.md`,
`research/23-sidecar-group-sharing.md` · details: `CHANGELOG.md`*

## What this release closes

Two roadmap arcs, and with them every roadmap item that needs no new
dependency. What remains after this release is exactly one item: the
language-server resolution backend.

## 1. Data flow beyond mutable globals (research/22)

`find_data_loops` could only see feedback state that a function declared
`global` — but `global X` is only needed to *rebind* a name. The dominant
shared-state idiom mutates in place, and was invisible:

```python
CACHE = {}                      # module level
def remember(k, v): CACHE[k] = v      # no `global` -> was untracked
def recall(k):      return CACHE.get(k)
```

Three additions, all advisory (READS/WRITES stay outside liveness — nothing
here can ever change what counts as dead):

- **Mutation-aware module state.** Module-level containers written through
  subscript stores, `del`, or a *closed allowlist* of mutating methods
  (`append`, `update`, `setdefault`, …) now emit the same `var::` node and
  READS/WRITES slice as declared globals. Precision-biased by design: an
  unrecognised method emits nothing (a missed loop beats a phantom loop),
  read-only config tables emit nothing, and a local `ITEMS = [1]` shadow is
  never a module write.
- **Instance-attribute data loops.** Methods of one class reading and
  writing the same `self.attr` — the classic worker-queue feedback shape —
  get class-scoped `var::<file>::<Class>.<attr>` nodes, gated on
  written∩read so write-only seeding never floods the graph.
- **Unused parameters.** A parameter never loaded in its function's body is
  a GREEN scan advisory, computed from source at scan time and deliberately
  not persisted (per-param nodes ×12 languages would inflate the graph).
  Abstract/stub/overload signatures, callbacks, `_`-prefixed names, and
  first-party overrides are excluded — the signature is the interface's,
  not the function's to slim.

Incremental convergence is pinned: `replace_file` produces the identical
var-node universe to a fresh reindex, including removing a zombie var when
the last mutation is edited away.

## 2. Sidecar group-sharing (research/23)

v3.41.0 taught SQLite to store each widened candidate set once; the
adjacency sidecar kept paying the expansion. The v2 layout stores the
interned sets in the mmap too:

| HA field index (16.1M logical edges) | v1 (expanded) | v2 (shared) |
|---|---|---|
| sidecar on disk | 162 MB | **12 MB** |
| build time | ~74 s | **2.5 s** |

Sweeps stay exact — the BFS family deduplicates candidate sets per frontier
round before touching members; degrees are computed set-first with no
expansion at all. Field-verified on Home Assistant: `fan_in`/`fan_out`
full-dict equality against SQL ground truth in both confidence modes,
forward/reverse closures equal to a reference BFS, bit-parallel lanes equal
to sequential sweeps. Old sidecar directories rebuild themselves on first
use (the sidecar has always been disposable).

## Compatibility

No schema migration, no API change, no new dependency. Indexes from v3.41+
open unchanged; the sidecar rebuilds once (2.5 s at 16M-edge scale).
