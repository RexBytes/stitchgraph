# DEVLOG

## Step 0 — Read the spec, plan architecture

Read `SPEC.md`. Requirements:
- Parse a small SPICE-style netlist (R, V, I, C elements + `.op` / `.tran` directives).
- Solve with Modified Nodal Analysis (MNA).
- `.op`: DC operating point with resistors + independent sources.
- `.tran`: transient with Backward-Euler companion model for capacitors (G=C/h in
  parallel with a current source carrying the previous step's implied current),
  caps start at 0V.
- `run.py <netlist>` prints one JSON object to stdout.

Planned module layout (avoiding the name `stitchgraph` for my own package, since
that's the code-intel tool's namespace — used `circuitsim` instead):

```
circuitsim/
  __init__.py
  values.py     - numeric literal parsing (plain + SPICE engineering suffixes)
  elements.py   - element dataclasses: Resistor, VSource, ISource, Capacitor
  netlist.py    - parser: text -> Netlist(elements, analysis)
  mna.py        - node numbering + MNA matrix/RHS assembly (stamps)
  solver.py     - thin numpy.linalg.solve wrapper
  analyses.py   - run_op(), run_tran() orchestration
run.py          - CLI entry point, JSON output
tests/          - pytest unit tests
examples/       - divider.ckt, rc.ckt (the two worked examples)
```

Design decisions made up front:
- MNA unknown vector = [non-ground node voltages..., voltage-source branch
  currents...]. Node `0` is never a variable (fixed at 0V by construction).
- Capacitor sign/stamp convention: current defined flowing from node A to node B
  through the element (like a resistor). Backward-Euler discretization gives an
  equivalent Norton model: conductance `Geq = C/h` between A,B, plus a current
  source `Ieq = Geq * v_prev` injected into A and extracted from B, where
  `v_prev = V_A_prev - V_B_prev` from the previous timestep (0 at t=0).
- `.op` treats capacitors as open circuits (no DC path) — physically correct
  steady-state behavior, and a reasonable superset of what the spec strictly
  requires for `.op` (resistors + sources only).
- Independent current source `I name p m value`: removes `value` A from node p,
  injects `value` A into node m => RHS[p] -= value, RHS[m] += value.
- Independent voltage source stamp: standard extra-unknown MNA stamp (B=+1/-1 at
  p/m columns, C=+1/-1 at p/m rows for the extra equation, RHS of extra row =
  value).

Plan: write `values.py`, `elements.py`, `netlist.py` first (pure parsing, easy to
unit test in isolation), then `mna.py` + `solver.py` (numeric core, test against
hand-solved small circuits), then `analyses.py` + `run.py` last, verified against
the two worked examples in the spec.

Will index with `stitchgraph` once there's a nontrivial amount of code to look at
(not useful yet on an empty directory).

## Step 1 — Build the core package

Wrote, in order:
1. `circuitsim/values.py` — numeric literal parser. Spec says "engineering
   suffixes are optional," which I read as "not required to support" but
   implemented anyway as a safe superset (plain `1e-6` style numbers always
   work; `4.7k`, `1MEG`, `10m` also work). Deliberately handled the classic
   SPICE gotcha: `M` = milli (1e-3), `MEG` = mega (1e6), and checked `MEG`
   before the bare `M` prefix so `"2MEG"` doesn't get misparsed as `2 * 1e-3`
   plus garbage.
2. `circuitsim/elements.py` — plain frozen dataclasses for `Resistor`,
   `VoltageSource`, `CurrentSource`, `Capacitor`. No behavior, just data —
   keeps the MNA stamping logic in one place (`mna.py`).
3. `circuitsim/netlist.py` — line-oriented parser producing a `Netlist`
   (lists of elements + one `OpAnalysis`/`TranAnalysis`). Validates: exactly
   one analysis directive, no duplicate element names, no zero-value
   resistors, right field counts, known element prefixes/directives.
4. `circuitsim/mna.py` — the numeric core. `build_node_map()` assigns each
   non-ground node an index (ground `"0"` is simply never a variable) and
   gives each voltage source an extra branch-current unknown. `assemble()`
   stamps the `A` matrix and `b` vector.

### MNA stamping decisions (worked out on paper before coding)

- **Resistor** (conductance g=1/R) between A,B: standard 4-stamp
  `G[A,A]+=g, G[A,B]-=g, G[B,A]-=g, G[B,B]+=g`.
- **Independent current source** `I p m value`: spec says it "removes value A
  from p, injects value A into m" → `RHS[p] -= value; RHS[m] += value`.
- **Independent voltage source** `V p m value`: standard extra-unknown MNA
  stamp. Defined branch current `k` as flowing from p to m *through* the
  source (internal direction). Stamps: `A[p,k]+=1, A[m,k]-=1, A[k,p]+=1,
  A[k,m]-=1`, and the extra equation row gives `RHS[k]=value` (i.e.
  `V_p - V_m = value`). Sign convention for `k` doesn't matter for node
  voltages since we never report source currents.
- **Capacitor, Backward Euler**: derived from `i(t) = C*dv/dt`, discretized
  as `i^n = (C/h)*(V_A^n - V_B^n) - (C/h)*v_prev` where
  `v_prev = V_A^{n-1} - V_B^{n-1}`. This is a Norton equivalent: conductance
  `Geq=C/h` stamped exactly like a resistor, plus a current source
  `Ieq = Geq*v_prev` injected into A and extracted from B
  (`RHS[A] += Ieq; RHS[B] -= Ieq`). At t=0, v_prev=0 for every capacitor per
  spec, so the first step has no extra current term, just the conductance.
- **`.op` and capacitors**: spec's `.op` scope only mentions resistors +
  sources, but I made capacitors act as open circuits during `.op` (skip them
  entirely) rather than erroring, since that's physically correct DC
  behavior and a reasonable superset. Verified with a unit test
  (`test_capacitor_open_in_dc`) that adding a cap in parallel with a divider
  resistor doesn't change the `.op` answer.

5. `circuitsim/solver.py` — thin wrapper around `numpy.linalg.solve`,
   translating `LinAlgError` (singular matrix — e.g. floating node) into a
   `SingularSystemError` with a hint.
6. `circuitsim/analyses.py` — `run_op()` builds one MNA system and solves
   once; `run_tran()` loops Backward-Euler steps from `t=step` to `t=tstop`
   (`num_steps = round(tstop/tstep)`, guarding float round-off e.g.
   `1e-3/1e-6` landing on `999.9999999999`), carrying forward each
   capacitor's `V_A - V_B` as `v_prev` into the next step's stamp.
7. `run.py` — CLI: parse → run whichever analysis → print exactly one JSON
   line to stdout; all error messages go to stderr; nonzero exit on parse
   errors, missing file, or singular system.

Manually smoke-tested both worked examples immediately after writing `run.py`,
before writing the pytest suite:

```
$ python3 run.py examples/divider.ckt
{"analysis": "op", "nodes": {"in": 10.0, "out": 5.0}}
$ python3 run.py examples/rc.ckt
{"analysis": "tran", "tstop": 0.001, "nodes": {"in": 1.0, "n": 0.6319366957112516}}
```

Both match the spec's expected values (`V(in)=10, V(out)=5.0` exactly; RC
node ≈0.632, and 0.6319... vs. the ideal continuous-time 0.63212 is the
expected small Backward-Euler discretization error at 1000 steps — first
order accurate, error ~O(h)).

## Step 2 — stitchgraph checkpoint #1 (orient / find-holes / find-stale)

Indexed the fresh package:

```
$ stitchgraph reindex . --db .sg.db
reindex: ok  files: 8  nodes: 42  holes: 0
```

```
$ stitchgraph orient --db .sg.db
top_hubs: elements.py::{Capacitor,CurrentSource,Resistor,VoltageSource} (fan-in
14-15), netlist.py::{OpAnalysis,TranAnalysis,Netlist}, mna.py::NodeMap...
```
Confirmed the architecture is shaped the way I intended: the element
dataclasses and `NodeMap` are the hubs everything else depends on, which
matches the "data model at the center" design.

```
$ stitchgraph find-holes --db .sg.db
(empty) count=0
```
No dangling references — the parser -> elements -> mna wiring is intact.

```
$ stitchgraph find-stale --db .sg.db
- id=circuitsim/mna.py::NodeMap.node_names_in_order
```
**This caught real dead code.** I'd added `NodeMap.node_names_in_order()`
as a speculative convenience method while designing the node-index mapping,
but never ended up needing it (node names are already recovered via
`node_index` dict comprehension in `analyses.py`). Confirmed with `grep` that
it had zero references anywhere (including tests), then deleted it from
`mna.py`. Concrete example of stitchgraph doing its job — I would not have
noticed this by reading the file since it looked like a reasonable API to
keep "just in case."

## Step 3 — Unit tests

Wrote pytest tests bottom-up, mirroring the module structure:
- `tests/test_values.py` — plain numbers, all engineering suffixes, the
  `MEG`-vs-`M` priority case, unknown trailing units ignored, error cases.
- `tests/test_netlist.py` — both worked examples parse correctly; comments/
  blank lines ignored; `.end` truncates parsing; and a battery of error
  cases (missing/duplicate `.op`/`.tran`, duplicate element names, zero
  resistance, wrong field count, unknown prefix/directive).
- `tests/test_mna.py` — hand-verified circuits below the parser layer
  (build `Netlist` objects directly, call `assemble()`/`solve_linear()`):
  voltage divider, a current source into a resistor (checks I*R by hand), a
  test that specifically exercises the `I p m` direction convention
  (current removed from p / injected into m gives the expected *signs* on
  both node voltages), the "capacitor is open in `.op`" behavior, and a
  first-Backward-Euler-step check against the closed form
  `v1 = h/(RC+h)*V` for `v_prev=0`.
- `tests/test_analyses.py` — the two worked examples at the `run_op`/
  `run_tran` API level, plus an analytic cross-check: for several `tstop`
  values, backward-Euler `.tran` output for the RC circuit is compared
  against the closed-form `V*(1-exp(-t/RC))` within a tolerance, and a
  monotonicity test that finer `tstep` (`tau/1000` vs `tau/10`) *reduces*
  the discretization error (regression guard against a sign error or
  reversed direction in the Backward-Euler update, which would not
  necessarily blow up the answer but would break convergence).
- `tests/test_examples.py` — end-to-end: shells out to
  `python run.py examples/{divider,rc}.ckt` exactly as the grader will,
  parses the single JSON line from stdout, checks it against the spec's
  expected values, and also checks CLI error paths (missing file, missing
  arg) exit nonzero with no stdout JSON and a message on stderr.

Result: `python3 -m pytest -q` → **53 passed**.

## Step 4 — stitchgraph checkpoint #2 (after tests existed)

```
$ stitchgraph reindex . --db .sg.db
files: 14  nodes: 84  holes: 0
```

```
$ stitchgraph scan --db .sg.db
[ORANGE] god_object: run_tran (fan-in 5, fan-out 5)
[ORANGE] god_object: run_op (fan-in 5, fan-out 5)
[ORANGE] god_object: parse_netlist (fan-in 22, fan-out 10)
```
Reviewed each: these are the three natural orchestration points in a small,
single-purpose CLI tool (every test and `run.py` calls into `run_op`/
`run_tran`; every parsing test calls `parse_netlist`). High fan-in here is
expected and desirable for a project this size, not a real coupling problem
— splitting them further would just add indirection. Treated as a reviewed
false positive rather than acted on; noted here rather than silently
ignored.

```
$ stitchgraph find-stale --db .sg.db
(empty) count=0
```
Confirms the dead method removed in Step 2 was the only stale code, and no
new dead code was introduced while writing the test suite.

```
$ stitchgraph impact-of assemble --db .sg.db
blast_radius: 22 nodes, tests_to_run: 13 tests (all of test_mna.py and
test_analyses.py, plus run.py::main)
```
Used this as a final sanity check that `mna.assemble` — the true numerical
core — is exercised by a broad swath of the test suite before considering
the project done; it is (13 of 53 tests touch it directly, plus the
end-to-end examples through `run_op`/`run_tran`/`main`).

## Step 5 — Final verification

- `python3 -m pytest -q` → 53 passed.
- `python3 run.py examples/divider.ckt` → `{"analysis": "op", "nodes":
  {"in": 10.0, "out": 5.0}}` — matches spec exactly.
- `python3 run.py examples/rc.ckt` → `{"analysis": "tran", "tstop": 0.001,
  "nodes": {"in": 1.0, "n": 0.6319...}}` — matches spec's `≈0.632`.

## Reflection on stitchgraph

Most useful moments: `find-stale` catching the unused
`NodeMap.node_names_in_order` method right after the core module was
written (a real, if small, finding), and `orient`/`find-holes` giving quick
confidence that the parser → elements → MNA wiring had no dangling
references before writing tests against it. `impact-of` was a nice final
gut-check that the test suite actually covers the numerical core rather
than just the happy-path CLI. `scan`'s god_object flags were the one place
it produced a signal I consciously overrode — appropriate for this project's
size, but worth recording rather than silently dismissing.
