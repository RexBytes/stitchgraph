# v3.39.0 — the recall-tail release

*2026-07-05 · every item is an evidence-ranked entry from the field
validations: research/18 (Home Assistant, recall 0.991 with an enumerable
0.9% tail) and research/19 (Django false-positive taxonomy) · details:
`CHANGELOG.md`*

The two field runs ended with a short, measured list of dynamic-dispatch
patterns static analysis missed. This release ships a resolver or heuristic
for each — plus the instrumentation to re-measure recall cheaply after every
future resolver change.

## New resolution coverage

- **Protocol dunders** — `with` blocks, `for` loops, comprehensions and
  subscripts run methods the source never names (`__exit__`, `__iter__`,
  `__setitem__`, …). Exact binding when the receiver is resolvable, the
  standard name-based fallback when it isn't; builtin receivers bind to
  nothing (zero noise). The work also exposed a latent collector bug: a
  `with` written as a direct body statement — the most common shape — was
  never collected at all.
- **getattr dispatch** — `getattr(self, f"_step_{x}")`, `"as_%s" % vendor`,
  concat and `.format` single-hole shapes reference every member matching the
  literal anchor. Anchorless patterns (`f"{x}"`) are rejected rather than
  fanning the graph.
- **Pytest fixtures** — fixtures are injected by parameter name; test and
  fixture parameters now bind to the `@pytest.fixture` defs they name
  (conftest chains included). Closes the zero-recall fixture-blind tests.
- **Django/Jinja templates** — `{{ obj.prop }}` member reads reference
  matching properties/methods, and TEMPLATE nodes are entry-point roots
  (frameworks render templates by name — an external entry surface like a
  route). Kills the admin-property false-dead bucket found on Django itself.
- **Tuple-unpack module constants** — `HORIZONTAL, VERTICAL = 1, 2` no longer
  leaks phantom import holes.

## Performance

- **Bit-parallel reachability** — `reachable_many` answers 64 independent
  closure queries per sweep by packing seeds into uint64 bit-lanes over the
  CSR sidecar. `audit_graph`'s per-test loop (31.6 min for 2,056 tests on the
  HA index) batches through it; lane-equality with sequential BFS is pinned
  by differential tests. Recall audits become cheap enough to run after every
  resolver change — which is exactly what keeps a tail-list honest.
- **`find_coupling` memory fix** — the no-static-edge filter materialised a
  frozenset per resolved edge (~10–12 GB at 27–30M edges, the recorded
  known-cost-op hazard); it now probes only the few hundred candidate pairs
  with indexed lookups.

## Measurement discipline

Every addition here is cardinal-safe (only ever adds reachability) and
name-based additions carry INFERRED/AMBIGUOUS provenance, so confidence
ceilings and `needs_review` behave exactly as for every other heuristic
binding. The HA recall re-measurement against the 0.991 baseline lands in the
research/18 addendum.
