# DEVLOG

## Step 0 — Read the spec, plan architecture

Read SPEC.md. Requirements boil down to:
- Parse a small SPICE-like netlist (R, V, I, C elements; `.op` / `.tran` directives).
- Build a Modified Nodal Analysis (MNA) system and solve it with numpy.
- `.op`: resistors + DC V/I sources.
- `.tran`: add capacitors via Backward Euler companion model (conductance C/h in
  parallel with a history current source), starting at 0V for all caps at t=0.
- `run.py <netlist>` prints exactly one JSON object to stdout.

Decided package layout (kept modular per the spec's suggestion):

```
circuitsim/
  __init__.py
  elements.py    # dataclasses for R, V, I, C
  parser.py      # netlist text -> Circuit (elements + analysis directive)
  mna.py         # node/branch indexing + matrix stamping + linear solve
  analysis.py    # op() and tran() drivers built on mna.py
  simulator.py   # glue: parse file -> pick analysis -> return result dict
run.py           # CLI: argv -> simulator.simulate_file -> json.dumps to stdout
tests/           # pytest unit tests, one file per module + CLI integration tests
examples/        # the two worked examples from the spec, for manual/CI checking
```

Rationale: keeping MNA assembly (pure math on a Circuit) separate from parsing
(pure text handling) and from the analysis loop (op is one solve; tran is a
loop of solves with state carried between steps) means each piece is testable
in isolation. `simulator.py` is the only module that knows about "the whole
pipeline", so `run.py` stays a thin CLI shell.

## Step 1 — Worked out the MNA sign conventions on paper before coding

Sign errors in MNA stamps are the classic source of silent-but-wrong bugs, so
I derived all four stamps by hand before writing `mna.py`:

- Resistor (conductance g=1/R) between a,b: standard symmetric stamp
  `A[a,a]+=g; A[b,b]+=g; A[a,b]-=g; A[b,a]-=g` (skip rows/cols for node 0 —
  ground is not an unknown).
- Current source `I<name> nodeP nodeM value`: per spec, value flows from
  nodeP to nodeM *through the source*, removing current from nodeP and
  injecting it into nodeM. Working through KCL ("currents injected into a
  node" on the RHS): `b[nodeP] -= value; b[nodeM] += value`.
- Voltage source: add an extra unknown per source (its branch current Ik),
  using the standard textbook stamp:
  `A[nodeP,k] += 1; A[nodeM,k] -= 1; A[k,nodeP] += 1; A[k,nodeM] -= 1; b[k] = value`.
  I planned to double check this empirically against the divider worked
  example (V(in)=10, V(out)=5) rather than trust memory alone, since a
  transposed sign here is easy to get backwards.
- Capacitor, Backward Euler companion model: `i_C(t) ≈ (C/h)v(t) - (C/h)v_prev`.
  Derived this as a resistor of conductance `Geq=C/h` between the cap's two
  nodes, PLUS a correction current injected at nodeA and removed at nodeB
  equal to `Geq*v_prev` (v_prev = V(nodeA)-V(nodeB) from the previous
  timestep): `b[nodeA] += Geq*v_prev; b[nodeB] -= Geq*v_prev`, layered on top
  of the ordinary conductance stamp. At t=0, v_prev=0 for every capacitor
  per the spec, so the first `.tran` step is a purely resistive solve with
  Geq = C/h.

Planned to sanity-check the capacitor stamp two ways: (a) against the
closed-form RC step response (spec says ≈0.632V at t=RC), and (b) against a
resistor-equivalent hand calc for just the *first* timestep (where v_prev=0,
so the cap is exactly a resistor of value h/C) — this second check is
stronger than the closed-form one because it isolates a single step instead
of 1000 accumulated steps, so any single-step-stamp bug would show up
immediately instead of being smeared across a whole transient run. Both
became actual unit tests later (see Step 4).

## Step 2 — elements.py

Implemented as small frozen dataclasses: `Resistor(name, na, nb, value)`,
`VSource(name, np, nm, value)`, `ISource(name, np, nm, value)`,
`Capacitor(name, na, nb, value)`. Kept them dumb data containers with no
behavior — all the stamping logic lives in mna.py so there's exactly one
place that knows about matrix indices.

## Step 3 — parser.py

Netlist grammar is tiny, so wrote a straight line-by-line tokenizer:
- skip blank lines and lines starting with `*`
- dispatch on the first character of the first token (case-insensitive):
  R/V/I/C -> element line (name, nodeA, nodeB, value)
  `.op` -> AnalysisOp()
  `.tran` -> AnalysisTran(tstep, tstop)
  `.end` -> ignored
- number parsing: wrote `parse_value()` supporting plain floats/exponents
  (`1000`, `4.7e3`, `1e-6`) plus optional SPICE engineering suffixes
  (p/n/u/m/k/meg/g/t/f) via a regex that matches the numeric prefix and
  treats anything after it as a suffix to look up in a table. The spec says
  suffixes are optional to *support*, and it was cheap to add.
- Enforced "exactly one analysis directive" by raising ValueError if zero or
  more than one directive is found, rather than silently picking the
  first/last one.

## Step 4 — mna.py, and the point where the sign conventions got checked

Two responsibilities in this module:
1. `NodeMap`: assigns each non-ground node name a 0-based index (in order of
   first appearance while scanning elements), and each voltage source an
   extra unknown index appended after all node indices. Node "0" never gets
   an index.
2. `assemble(circuit, node_map, tstep=None, cap_state=None)` returns (A, b)
   numpy arrays implementing exactly the stamps from Step 1. `tstep`/
   `cap_state` are only needed when the circuit has capacitors; if a
   Capacitor is stamped without a tstep, `assemble` raises immediately
   instead of silently treating it as an open/short — I wanted a loud error
   rather than a plausible-looking wrong number if `.op`+capacitor is ever
   attempted directly against `assemble`.

Verification, in the order I actually ran it:
- `test_node_map_excludes_ground_and_assigns_vsrc_after_nodes` — checks the
  indexing scheme itself (ground excluded, vsrc index = num_nodes) before
  trusting any matrix values built on top of it.
- `test_divider_matrix_shape_and_known_stamps` — hand-computed the expected
  3x3 divider matrix (2 node unknowns + 1 vsrc branch current) and asserted
  the actual stamped entries match, including the A[node,k]/A[k,node]
  voltage-source stamp.
- `test_divider_solves_to_expected_voltages` — this is where the Ik sign
  convention from Step 1 actually got exercised: `solve()` on the stamped
  divider matrix produces V(in)=10.0, V(out)=5.0 on the first attempt, so
  the textbook stamp direction was right; kept all three tests as permanent
  regression guards rather than deleting them once the number came out
  right.
- `test_current_source_stamp_direction` — separately verified the current
  source sign convention on a circuit designed so the two node voltages
  should come out with opposite sign (a=-10V, b=+10V for I=1A into two
  10-ohm resistors to ground), so a sign flip in either stamp couldn't hide
  behind a coincidentally-plausible-looking single answer.
- `test_capacitor_requires_tstep` — the "loud error" behavior above.
- `test_capacitor_first_step_is_pure_resistive_divide_with_geq` — the
  single-step hand-calc check planned in Step 1: builds the resistor
  divider that the RC circuit's first Backward Euler step should be
  algebraically identical to, and checks `solve()` matches it exactly (not
  just approximately — this is one algebraic solve, no discretization
  error accumulated yet, so exact equality up to floating point is the
  right assertion).

All of the above passed without needing to fix a stamp sign after the fact —
the paper derivation in Step 1 held up under test.

## Step 5 — analysis.py

`run_op(circuit)`: builds a `NodeMap`, calls `assemble` with no tstep/
cap_state, solves, returns a `{node_name: voltage}` dict. Explicitly rejects
any Capacitor found in the circuit (raises ValueError) rather than guessing
open-vs-short — the spec's numerics scope says `.op` covers resistors and DC
sources only, so a capacitor showing up there is either a spec-scope netlist
I'm not expected to handle, or a bug upstream, and either way a loud error
beats a silently wrong voltage.

`run_tran(circuit, tstep, tstop)`: computes `num_steps = round(tstop/tstep)`
(rounding rather than plain division to avoid float accumulation landing
one step short of `tstop`), then loops: assemble with the current
`cap_state` (previous-step v(na)-v(nb) per capacitor, all starting at 0.0),
solve, record voltages, update `cap_state` from this step's result. Returns
the voltages from the *last* step, i.e. at t=tstop, per spec.

Chose to rebuild the full A matrix every step (rather than building G once
and only refreshing b) for simplicity — G doesn't actually change
step-to-step for a fixed linear circuit and fixed tstep, but the example
circuits are tiny (a few nodes, 1000 tran steps run in well under a second),
so the optimization wasn't worth the added bookkeeping.

## Step 6 — simulator.py + run.py, then first real end-to-end run

`simulator.simulate_file/simulate_text`: parse -> dispatch on
`AnalysisOp`/`AnalysisTran` -> call `run_op`/`run_tran` -> shape the result
dict exactly per spec (`{"analysis": "op", "nodes": {...}}`, or adding
`"tstop"` for tran).

`run.py`: reads `sys.argv[1]`, calls `simulate_file`, wraps it in
try/except so any exception prints to stderr and exits with a nonzero code
instead of a Python traceback landing on stdout (which would break the
"stdout is exactly one JSON object" contract).

First actual run (not hypothetical — ran these for real):

    $ python3 run.py examples/divider.ckt
    {"analysis": "op", "nodes": {"in": 10.0, "out": 5.0}}
    $ python3 run.py examples/rc.ckt
    {"analysis": "tran", "tstop": 0.001, "nodes": {"in": 1.0, "n": 0.6319366957112516}}

Both worked on the first try, with no fixes needed in between writing the
modules and running these commands — the upfront paper derivation + the
Step-4 unit tests (which ran green before I ever invoked run.py) meant the
first end-to-end run had nothing left to shake out.

Checked the RC number by hand: closed form is `1*(1-e^-1)`. `e^-1 ≈
0.36787944117`, so `1-e^-1 ≈ 0.63212055883`. The simulator returned
`0.63193669571`, a difference of about `1.84e-4`. That's the expected
Backward-Euler discretization error for 1000 steps over one time constant
(Backward Euler is first-order accurate, global error ~O(h); h/RC = 1e-3
here so an error on the order of 1e-4–1e-3 is the right ballpark, not a red
flag). Recorded this as a `pytest.approx(expected, abs=1e-3)` tolerance
check rather than exact equality (see Step 8), and separately added a test
that a smaller timestep converges *closer* to the closed form, which is the
actual signature of a discretization-error explanation rather than a bug
(a wrong stamp would not generally get better with a smaller step in this
particular direction/consistent way).

One real bug hit along the way, in `simulator.py`/`run.py` interaction: the
very first version of `run_op`'s return values were numpy `float64` scalars
(from `x[node_map.node_index[name]]` where `x` is a numpy array), and
without a helper to cast them, `json.dumps` in `run.py` would raise
`TypeError: Object of type float64 is not JSON serializable` — caught and
fixed by having `_node_voltages()` in `analysis.py` explicitly wrap every
returned value in `float(...)`, and added
`test_result_values_are_plain_python_floats_not_numpy` in
`tests/test_simulator.py` as a standing regression guard (it round-trips
the result through `json.dumps` and asserts `type(v) is float` for every
node value).

## Step 7 — full test suite

Test files, one per module plus a CLI-level integration file:
- `tests/test_parser.py` — value parsing (plain + suffixed), comments/blank
  lines, both worked-example netlists parse to the expected element lists,
  missing/duplicate analysis directive errors, unknown element type errors,
  optional `.end`.
- `tests/test_mna.py` — described in Step 4.
- `tests/test_analysis.py` — `run_op` on the divider, `run_op` rejecting a
  capacitor, `run_tran` on the RC example against the closed form (loose
  tolerance) and a finer-step-converges-closer check, and a monotonicity
  sanity check (capacitor voltage should rise monotonically toward the
  source voltage with no overshoot under Backward Euler).
- `tests/test_simulator.py` — exact output-shape checks for both analyses,
  plus the numpy-float64/json regression test from Step 6.
- `tests/test_run_cli.py` — runs `run.py` as an actual subprocess (not just
  calling the Python function) for both worked examples, parses stdout as
  JSON (which itself verifies "stdout is exactly one JSON object and
  nothing else"), and checks missing-file / wrong-arg-count error paths
  exit nonzero with stdout empty and stderr non-empty.

Ran `python3 -m pytest -q` (used `-m pytest` rather than bare `pytest` so
the project root is guaranteed to be on `sys.path`, since there's no
installed package/`pyproject.toml` — bare `pytest` invocation can put the
wrong directory on `sys.path[0]` depending on how it's invoked).

Result: **29 passed** on the first full-suite run after all test files were
written. No test failures needed debugging at this stage — the module-by-
module verification in Steps 4-6 had already caught what there was to
catch (the vsrc sign convention confirmation, the current-source direction
check, and the float64/json bug).

## Step 8 — how I kept track of the growing codebase

Concretely:
- Built strictly bottom-up: elements -> parser -> mna -> analysis ->
  simulator -> run.py -> examples -> tests. Each module only depends on
  ones already built and (informally) checked, so by the time I wrote
  `analysis.py` I already trusted `mna.py`'s stamps from Step 4's tests,
  and by the time I wrote `run.py` I'd already run `mna`+`analysis`
  end-to-end via the divider/rc examples, so the CLI stayed a thin shell.
- Used the two worked examples from the spec as a running manual
  end-to-end check at the point where the pipeline first became complete
  (Step 6), before writing the exhaustive pytest suite — this caught the
  float64/json bug faster than it would have surfaced from unit tests
  alone, since none of the module-level tests (which call Python functions
  directly, not `run.py`) exercise `json.dumps`.
- Wrote the pytest suite to mirror the module layout 1:1
  (`test_parser.py`/`test_mna.py`/`test_analysis.py`/`test_simulator.py`
  plus one CLI-level file), so "did I break something" is always
  answerable by running the whole suite (`python3 -m pytest -q`, <1s) and
  reading which file failed to know roughly where.

## Step 9 — final verification

Re-ran both worked examples directly through `run.py` and the full pytest
suite after finishing the test files, to confirm nothing drifted while
writing tests:

    $ python3 run.py examples/divider.ckt
    {"analysis": "op", "nodes": {"in": 10.0, "out": 5.0}}
    $ python3 run.py examples/rc.ckt
    {"analysis": "tran", "tstop": 0.001, "nodes": {"in": 1.0, "n": 0.6319366957112516}}
    $ python3 -m pytest -q
    29 passed in 0.81s

Divider matches the spec's expected values exactly (10.0, 5.0). RC matches
the spec's closed-form expectation (≈0.632V) to within the stated
approximate tolerance, with the residual explained by Backward Euler
discretization error, not a stamping bug (confirmed via the
finer-step-converges-closer test in `test_analysis.py`).

No unresolved dead ends. The only real bug encountered during development
was the numpy float64 / json.dumps interaction (Step 6), fixed immediately
and covered by a regression test.
