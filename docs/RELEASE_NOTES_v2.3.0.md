# stitchgraph v2.3.0 — shared Tarjan SCC core

A small, internal de-duplication release. **No API, schema, or behaviour change**: indexes,
`scan` cycle detection, and `find_data_loops` produce byte-identical results. Indexes do not need
rebuilding.

## Changed

### Shared `tarjan_scc` helper

The strongly-connected-components algorithm was duplicated verbatim in two places:

- `core/reach.py` → `strongly_connected_components` (call / import cycles, behind `scan`)
- `core/dataloop.py` → `_tarjan` (data-feedback loops)

Both copies carried an identical `strongconnect` implementation and recursion-limit handling. They
are now a single `core/_scc.py:tarjan_scc(adj, seeds, node_count)`. Each call site keeps its own
adjacency construction, seed set (`reach`: all node ids; `dataloop`: adjacency keys), and
post-filter — so behaviour is preserved exactly, including the temporary recursion-limit raise that
is always restored in a `finally` (never leaked to the host).

This was the one piece of genuine redundancy surfaced by the *matrix-as-oracle* research line
(`research/`, not part of the package): the body-level structural-clone detector found the
byte-identical duplication the call-graph detector was blind to, and an independent
program-dependence-graph pass re-found the same pair.

## Added

- **`tests/test_scc.py`** — 15 direct unit tests pinning the shared primitive: component identity
  (empty / single / self-loop / chain / 3-cycle / two independent cycles / **cross-edge into an
  already-finished SCC**), destination-only and out-of-adjacency seeds, reverse-topological
  ordering, a 5 000-node chain (no `RecursionError`), `defaultdict` non-mutation, and
  recursion-limit restoration on both normal return and an exception mid-walk. Stdlib-only, so it
  runs in the `core-only` CI job.

## Quality gate

- ruff + mypy clean; full suite **590 passing**; differential oracle suite (27) green.
- Mutation meta-oracle on `tarjan_scc`: **6/6 mutants killed by `tests/test_scc.py` alone**.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean. The panel
  confirmed component / ordering / recursion-limit equivalence to the old inline copies — including
  a **650-graph random differential with zero divergences** between old and new — and verified that
  `find_stale` liveness is **structurally independent** of SCC results (SCC feeds only advisory
  cycle / data-loop findings, never stale rooting), so the cardinal rule is insulated from this
  change. Round 2 surfaced a unit-test coverage gap (the `on_stack` cross-edge guard), which was
  closed before release; the two confirming rounds were clean.

## Notes

The companion research (`research/SYNTHESIS.md`) and the larger deferred direction — an
intra-procedural (body / CFG / DFG / PDG) matrix, a candidate **v3.0.0** feature — are documented in
`docs/IDEAS.md §5`. This release ships only the safe, validated de-duplication.
