# DEVLOG

## Goal

Build a small SPICE-style circuit simulator (R/V/I/C/L, `.op`/`.tran`/`.dc`)
using Modified Nodal Analysis (MNA), structured so a different developer can
add a new element type or a new analysis without touching existing files.

## Architecture decisions

**Module split** (see `circsim/__init__.py` for the map): `units` (numeric
parsing) -> `elements/` (element model) -> `circuit` (parsed netlist) ->
`netlist` (text -> Circuit) -> `mna` (stamping + solve) -> `analyses/`
(directive behavior) -> `results` (formatting) -> `cli`/`run.py` (entry
point). Each layer only depends on the ones before it in that list, so
there's one clean import direction and no cycles.

**Plugin registries for the two extension points.** Both `elements/base.py`
(`ELEMENT_REGISTRY` + `@register_element`) and `analyses/base.py`
(`ANALYSIS_REGISTRY` + `@register_analysis`) use the same pattern: a
module-level dict keyed by netlist designator/keyword, populated by a class
decorator. `netlist.py` dispatches purely by looking things up in these
dicts -- it never has an `if prefix == "R"` chain, so adding a new element
or analysis means writing one new file and adding one import line to the
package's `__init__.py`. `tests/test_extensibility.py` proves this by
registering a throwaway element type and a throwaway analysis from a test
and running a netlist that uses them, with zero edits to existing modules.

**Companion-model reactive elements, not extra unknowns.** The spec's
`.tran` companion models for C and L are pure conductance + Norton current
source at the two existing nodes -- no extra MNA unknown needed for either
in transient mode. This keeps the system size constant across `.op`/`.dc`
and simplifies `.tran` bookkeeping (no separate "inductor current" row to
carry across steps in the matrix itself). At DC, though, an inductor *is*
modeled as an extra unknown, because "short circuit" only has a clean MNA
stamp as a 0-volt voltage source (exactly reusing `VoltageSource`'s branch
mechanism via `stamp_voltage_branch`). A capacitor at DC needs no stamp at
all (open circuit == do nothing).

**Where inductor current lives.** A capacitor's "previous voltage" for the
Backward-Euler companion source can be read straight out of the previous
solved vector (it's a node voltage). An inductor's "previous current" is
*not* a node voltage, so it can't be recovered from `x_prev` alone. I added
an `Element.post_solve(mna, x, ctx)` hook (no-op by default) that every
analysis calls once per solved step; `Inductor.post_solve` computes and
stores its updated current in `ctx.state[self.name]`, a plain dict handed
around by the transient loop. This turned out to be a second, quieter
extension point: any future element with per-step history (e.g. a diode
doing Newton iteration) has a documented place to hook in.

**`.dc` reuses `.op`.** `analyses/op.py` exposes `solve_dc(circuit,
overrides=None)` as the actual DC solver; `OpAnalysis.run` calls it with no
overrides, `DcAnalysis.run` calls it once per sweep point with
`{source_name: value}`. Overriding is generic (`Element.effective_value`
checks `ctx.overrides` before falling back to the parsed netlist value) so
it works for `I` sources too, not just `V`, even though the spec's example
only sweeps a voltage source.

**Sign convention sanity checks.** The current-source and companion-model
sign conventions were derived from KCL by hand (see docstrings in
`mna.py`/`capacitor.py`/`inductor.py`) and then cross-checked two ways:
against the spec's worked RC example, and against the closed-form step
response of a series R-L circuit (`tests/test_tran.py::
test_rl_step_response_matches_analytic_solution`) driven by a voltage
source at t=0 -- catching a sign error there was the main risk in this
design, and both checks passed on the first implementation.

## What I verified

- All three SPEC.md worked examples pass via `python run.py <netlist>`:
  `examples/divider.ckt` (`.op`) -> V(out)=5.0,
  `examples/rc.ckt` (`.tran`) -> V(n)≈0.632 at tstop,
  `examples/sweep.ckt` (`.dc`) -> V(out) in {0, 2.5, 5.0}.
- `pytest` (57 tests) covers: unit parsing, netlist parsing/error cases,
  `.op` (including current source direction, C-open, L-short at DC),
  `.dc` (including negative step, non-integer step count, bad source name),
  `.tran` (RC charging matches spec + long-run convergence, RL step
  response matches analytic solution), the full CLI via subprocess for all
  three worked examples plus an error-path check, and the two extension
  points (new element type, new analysis).
- All non-destructive/parametrized tests pass in isolation and as part of
  the full suite (checked for registry-mutation leakage from the
  extensibility tests specifically, since they register/unregister into
  shared module-level dicts).

## Known simplifications / things a future developer should know

- Zero-ohm resistors are rejected (no branch-current formulation for them);
  a floating/singular circuit raises `SingularMatrixError` with a
  human-readable hint rather than numpy's raw `LinAlgError`.
- `.tran` uses a fixed timestep (no adaptive step control) per spec.
- Node voltage output order in the JSON `nodes` dict is alphabetical by
  node name (`Circuit.node_names` is sorted); the spec doesn't require a
  particular order, and dict/JSON key order isn't otherwise meaningful here.

## Change: adding the `E` element (VCVS) -- picked up cold, didn't write the original code

Task (from `CHANGE.md`): add a linear voltage-controlled voltage source,
`E<name> nP nM ncP ncM gain`, enforcing `V(nP)-V(nM) = gain*(V(ncP)-V(ncM))`
independent of load, in `.op`/`.tran`/`.dc`, without regressing anything.

### Orienting myself

I did not write this codebase, so before touching anything I read `SPEC.md`
(what it's supposed to do), then this file's history above (an earlier
developer's own account of *why* things are shaped the way they are -- the
registry pattern, the module dependency order, where reactive-element state
lives). That save a lot of exploration: it told me up front that there are
exactly two extension-point registries (`ELEMENT_REGISTRY`,
`ANALYSIS_REGISTRY`) and where the "how to add an element" instructions
live (`circsim/elements/base.py`'s module docstring), so I went straight
there instead of grepping blind.

From `elements/base.py`'s docstring I got a numbered recipe: subclass
`Element`, set `prefix`/`num_params`, implement `stamp()`, implement
`extra_vars()` if it needs a branch-current unknown, register with
`@register_element`, import it from `elements/__init__.py`. I then read the
existing `VoltageSource` (`circsim/elements/vsource.py`) as the closest
analog -- a VCVS is "a voltage source whose constraint's RHS is a multiple
of two other node voltages instead of a constant" -- and
`mna.py::MNASystem.stamp_voltage_branch` to see exactly what matrix cells
an ideal-voltage-source branch touches, since the task said to "work out
the stamp from the constraint equation."

### The one wrinkle the recipe didn't cover: node count

Every existing element is two-terminal. `Element.__init__` takes `nodes`
generically as a sequence, and `Circuit.__init__` builds `node_names` by
iterating `element.nodes` (no hardcoded length assumption) -- so the
*element* and *circuit* layers were already node-count-agnostic. But
`netlist.py::_parse_element_line` was not: it unconditionally did
`n1, n2 = tokens[1], tokens[2]` and computed `expected_len = 1 + 2 +
element_cls.num_params`. A VCVS needs 4 node fields (nP nM ncP ncM) plus 1
gain value, so parsing would have silently mis-sliced the line (e.g. taking
`ncP` as a "value" and choking on `parse_value`, or worse, truncating
silently) if I'd left this alone. This was the main "hidden call site" the
task warned me to look for -- it isn't in the elements folder at all.

Fix: added `Element.num_nodes: ClassVar[int] = 2` (defaulting to today's
universal case, so every existing element needs zero changes), and
generalized `_parse_element_line` to slice `tokens[1 : 1+num_nodes]` for
nodes and `tokens[1+num_nodes:]` for params, using `element_cls.num_nodes`
instead of the hardcoded `2`. `VCVS` (new file `circsim/elements/vcvs.py`)
sets `num_nodes = 4`. I re-derived the malformed-line error message to
list `num_nodes` placeholders (`n1 n2 ... `) instead of hardcoding `nA nB`,
and checked `tests/test_netlist.py::test_rejects_malformed_element_line`
only pattern-matches on `"expects"`, not the literal old wording, so this
was safe to reword.

### Working out the stamp

Constraint: `V(nP) - V(nM) - gain*(V(ncP) - V(ncM)) = 0`. Like an
independent V source, this needs one extra branch-current unknown (call it
`I_E`) because it can't be expressed as a node conductance -- it's copied
straight from `VoltageSource.extra_vars`/`branch_key` pattern. The KCL
coupling at the output nodes nP/nM (the branch current entering/leaving
those nodes) is *identical* to `stamp_voltage_branch`'s -- from the rest of
the circuit's perspective a VCVS output pair behaves exactly like an ideal
V source, it just doesn't know its own value until solve time. The only
thing that changes is the constraint row: instead of `A[branch,i] += 1;
A[branch,j] -= 1; z[branch] = value`, the control-node terms move onto the
left-hand side as matrix coefficients: `A[branch, ncP] -= gain; A[branch,
ncM] += gain`, with `z[branch]` left at 0 (no constant term).

I added `MNASystem.stamp_vcvs_branch(n_plus, n_minus, nc_plus, nc_minus,
branch_index, gain)` in `mna.py` as a sibling to `stamp_voltage_branch`
rather than trying to shoehorn the VCVS through the existing method with a
fake "value" -- the existing method's signature has no room for two more
node names, and I didn't want to change its signature and risk breaking
`Inductor`'s DC-short use of it. `VCVS.stamp()` just looks up its
`extra_var_index()` and calls the new primitive, mirroring
`VoltageSource.stamp()` line for line.

### Why no analysis-specific code was needed

`.op`/`.dc`/`.tran` all go through the same `assemble_and_solve()` core
(`mna.py`), and a VCVS's `stamp()`/`extra_vars()` don't branch on
`ctx.mode` at all (unlike `Capacitor`/`Inductor`, it isn't reactive -- it
has no state to carry between timesteps, so no `post_solve()` override
either). That means "works in .op" automatically implied "works in .tran
and .dc" once the stamp was right, which I still verified concretely (see
below) rather than taking on faith, since `.dc`'s override mechanism
(`effective_value`) technically *could* have interacted with `gain` in a
sweep -- it doesn't in my tests, but the mechanism is there for free
(sweeping an `E` element's gain via `.dc E1 ...` would work, though that's
not part of the netlists I tested).

### Verifying call-site coverage

Grepped for every place that pattern-matches on node count or iterates
`self.nodes` by fixed unpacking (`nA, nB = self.nodes` style) across
`circsim/`: found it only inside each element's own `stamp()`/`post_solve()`
(each already correctly local to *that* element's arity) and in
`netlist.py` (the one fixed above). `circuit.py` and `results.py` both
treat `element.nodes` / node voltages generically (set union, dict
lookup by name) and needed no changes. Confirmed by grep that
`circsim/elements/__init__.py` is the single place new element modules
must be imported for their `@register_element` decorator to run, and added
`vcvs` to that import line.

### Verification

- Ran the full existing suite before touching anything (`python -m pytest
  -q`) to get a clean baseline: 57 passed.
- After the change: same 57 pre-existing tests still pass unmodified, plus
  11 new tests in `tests/test_vcvs.py` covering: parsing into `VCVS` with
  4 nodes; the exact CHANGE.md self-check example (gain=3, V(in)=2 ->
  V(out)=6, independent of a 1k load); load-independence across three very
  different resistor values; negative and fractional gain; a VCVS driving
  a resistive divider (exercises real current through the extra branch
  unknown, not just a single load resistor); a non-grounded control pair
  (checks the constraint uses the *difference* of the two control nodes,
  not just one of them -- this would have caught a bug where I forgot the
  `nc_minus` term); a direct check of the branch-current unknown's value
  against the load current (and a sign sanity-check against plain
  `VoltageSource` in the same convention, see below); `.dc` sweep; and
  `.tran` settling to the forced output voltage through an RC load.
- Also ran `python run.py` by hand on the CHANGE.md self-check netlist and
  on ad hoc `.op`/`.tran`/`.dc` netlists using `E`, and re-ran the three
  `SPEC.md` worked examples (`examples/divider.ckt`, `rc.ckt`,
  `sweep.ckt`) through `run.py` after the change to confirm the
  node-count generalization in `netlist.py` didn't disturb ordinary
  2-terminal parsing.

### Dead end / thing that looked like a bug but wasn't

My first branch-current test asserted the VCVS's branch-current unknown
equals `+I_load` (current flowing out into a 250-ohm load at 5V ->
expected +0.02A). It came back `-0.02`. Before "fixing" the stamp, I
wrote the same check against the pre-existing, already-tested
`VoltageSource` (`V1 in 0 1` / `R1 in 0 250`, no VCVS involved at all) and
got `-0.004` (i.e. `-1/250`) there too -- so the negative sign is the
simulator's existing, working convention for this branch-current unknown
(an artifact of the KCL-row sign in `stamp_voltage_branch`, present before
my change), not something my VCVS stamp got wrong. I fixed my *test's*
expected sign rather than the code, and left a comment in the test
pointing at that cross-check so a future reader doesn't repeat the same
five minutes of confusion.
