# 22 — Variable-granularity data flow: the scoped slice

*2026-07-06 · dependency-free batch ② · what "beyond mutable globals"
honestly means for one release, and what stays deferred.*

## Ground truth (survey)

Today's data-flow surface is one thin, deliberately cardinal-safe slice:
`_global_state` (extract/python.py) emits `var::<rel>::<name>` VARIABLE nodes
and READS/WRITES edges ONLY for names some function declares `global`;
`find_data_loops` runs an SCC over CALLS + writer→var→reader; `scan` surfaces
the loops at ORANGE. READS/WRITES sit outside `LIVENESS_RELATIONS`, so
nothing here can ever under-root live code (the cardinal gate holds by
construction). The PDG/VFG body-matrix layer computes exactly the
intra-function def-use this arc needs, but it is on-demand, per-function,
opaque at call boundaries — a reference algorithm, not a reusable substrate.

Design contract (design.md §6.E/§6.F, IDEAS.md gates): non-global data loops
+ argument provenance, variable granularity opt-in for scale, prototype
before promoting, advisory always.

## The three deliverables of this slice (Python; advisory; same machinery)

**1. Mutation-aware module-state tracking — closing a real gap in the
existing feature.** `global X` is only needed to REBIND a name; the dominant
shared-state idiom mutates in place and today is invisible:

```python
CACHE = {}                    # module level
def remember(k, v):
    CACHE[k] = v              # no `global` needed -> currently untracked
def recall(k):
    return CACHE.get(k)
```

New triggers, for names ASSIGNED a container-ish literal at module level
(dict/list/set/call-of-them): a subscript STORE (`X[k]=v`), an augmented
assignment (`X += ...` with `global`, already covered; `X[k] += v` without),
`del X[k]`, or a call of a KNOWN mutating method (`append/extend/add/update/
setdefault/pop/insert/remove/clear/discard/appendleft`) with the module name
as receiver → WRITES; plain loads keep READS. Same `var::` ids, same
consumers — `find_data_loops` and `scan` light up for free.

**2. Instance/class-attribute data loops.** The classic non-global feedback
shape: methods of one class reading and writing the same `self.attr`.
Attribute-granularity nodes `var::<rel>::<Class>.<attr>` with WRITES from
methods that store/mutate `self.attr` and READS from methods that load it —
class-scoped by id (never cross-class; `self` only, no alias chasing).
`find_data_loops` then reports cycles threaded through instance state
(method A writes `self.queue`, method B drains it and calls A...).

**3. Unused parameters (§6.E's first promise).** Computed per function at
scan time from the AST (a parameter with no Load in the body; `self`/`cls`,
`_`-prefixed, `*args/**kwargs`, and abstract/stub/overriding methods
excluded), surfaced as a LOW/GREEN advisory scan issue. Deliberately NOT
persisted as param nodes — design.md keeps variable granularity opt-in
because per-param nodes ×12 languages inflates the graph; a scan-time check
delivers the user value with zero storage.

## Deferred, explicitly (the honest 🔴 remainder)

Interprocedural argument provenance/taint (a value's journey across call
boundaries) needs a whole-program def-use representation with stable
cross-function variable ids — the v3.0-class "promote the body matrix"
project (IDEAS.md §5c). Not this slice. The LIMITATIONS entry stays.

## Gates

- Advisory only: no new relation enters `LIVENESS_RELATIONS`; find_stale
  unaffected by construction (the existing suite pins this).
- Python-first: tree-sitter languages keep their current behaviour; the
  matrix documents it.
- Convergence: incremental replace_file must produce identical var nodes
  and READS/WRITES to a fresh reindex (the existing convergence oracles
  extend to the new emitters).
- Precision bias: mutating-method list is a closed allowlist; unknown
  methods emit nothing (a missed loop is acceptable; a phantom loop that
  cries wolf is not).
