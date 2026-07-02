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

---

# Change: add the `E` element (VCVS) -- picking up someone else's codebase

I (a different session, not the original author) was handed this repo cold,
plus `CHANGE.md` asking for a linear voltage-controlled voltage source:
`E<name> nP nM ncP ncM gain`, enforcing `V(nP)-V(nM) = gain*(V(ncP)-V(ncM))`,
working in `.op`/`.tran`/`.dc`, with no regressions. This section documents
orientation, the actual changes, verification, and -- per the task -- an
honest account of every `stitchgraph` call and whether it earned its keep.

## Orienting in unfamiliar code

I did not assume the DEVLOG above (base-build notes) was trustworthy
documentation of the *current* code -- treated it as a hint, then verified
by reading. Sequence:

1. Read `CHANGE.md` and `SPEC.md` first, to know the target behavior and
   vocabulary (MNA, branch-current unknown, companion model) before opening
   any source file.
2. `stitchgraph reindex . --db .sg.db` -- indexed the project (29 files, 147
   nodes, 0 holes at baseline). This is a prerequisite for every other
   command, not itself informative, but it did give a fast sanity signal:
   0 holes meant the base build had no dangling references to begin with,
   so any holes I saw *after* my edit would be mine.
3. `stitchgraph orient --db .sg.db` -- returned top hubs by transitive
   fan-in: `Element.__init__`/`__repr__` (72), `AnalysisContext.__init__`
   (55), `MNASystem.node_id` (55), `Analysis` (53), `parse_value` (53),
   `MNASystem.extra_var_index`/`inject_current` (49). This was genuinely
   useful as a map: it told me, before reading a line of `circsim/`, that
   the element base class and the MNA extra-var/inject-current machinery
   are the two centers of gravity in this codebase -- exactly where I'd
   expect a new element type's stamping logic to plug in, and confirmed by
   everything I found afterward by just reading the files.
4. Read `circsim/elements/base.py`, `vsource.py`, `mna.py`, `netlist.py`,
   `circuit.py` directly (the hub list told me where to start; actually
   understanding the branch-unknown numbering scheme required reading the
   code, not just the graph). Key facts extracted this way: `MNASystem`
   lays out unknowns as `[node voltages][branch currents]`
   (`num_nodes` then one slot per `add_extra_var` key); `VoltageSource`
   requests one extra var (`"{name}:branch"`) and calls
   `stamp_voltage_branch(n_plus, n_minus, branch_idx, value)`, which does
   two things in one primitive: (a) couples the branch current into the two
   output nodes' KCL rows (`A[i,branch] += 1`, `A[j,branch] -= 1`), and (b)
   writes the branch's own constraint row (`A[branch,i] += 1`,
   `A[branch,j] -= 1`, `z[branch] = value`). That constraint-row shape is
   exactly what a VCVS needs to generalize: same output-pin coupling, but
   the RHS becomes `gain*(V(ncP)-V(ncM))` instead of a constant.
5. `stitchgraph find-similar "stamp a voltage source into the MNA matrix as
   a branch-current unknown" --db .sg.db` -- top hits were
   `VoltageSource`, `MNASystem.stamp_voltage_branch`, `Inductor.stamp`,
   `VoltageSource.stamp`, in that order. This exactly matched what I'd
   already found by reading, so it didn't change my plan -- but it's a
   useful confirmation move: if it had surfaced something I'd missed (e.g.
   a second half-finished VCVS-like stamp already in the tree), I'd have
   caught it here for free before writing duplicate code.
6. `stitchgraph impact-of "_parse_element_line" --db .sg.db` and
   `stitchgraph impact-of "Element.__init__" --db .sg.db` -- before
   touching the parser (which hard-codes "2 nodes" in a way `Element`
   itself doesn't) and the `Element` base class, I checked blast radius.
   Both came back with the *entire* test suite (41 and 72 nodes
   respectively, all of `test_op.py`/`test_dc.py`/`test_tran.py`/
   `test_netlist.py`/`test_extensibility.py`/`test_cli_examples.py`) as
   "tests to run." That's not a surprising result for a base class and the
   sole parser entry point in a 29-file project, so it didn't narrow
   anything down -- but it did remove any doubt about *whether* I needed to
   run the full suite after this change (obviously yes) rather than
   spot-checking a subset, which is the kind of thing it's easy to get
   lazy about when a change "should" be additive-only.

## What actually needed changing (and what didn't)

Reading confirmed the codebase's own extension-point contract
(`elements/base.py`'s docstring, `analyses/base.py`'s docstring) does *not*
mention variable node counts anywhere -- every existing element (R, V, I,
C, L) is implicitly two-terminal, and `netlist.py::_parse_element_line`
hard-codes `expected_len = 1 + 2 + num_params` and `n1, n2 = tokens[1],
tokens[2]`. A VCVS needs 4 nodes (2 output + 2 control) plus 1 gain value,
so this hard-coding was the one real structural gap, not just "add a new
file":

1. **`circsim/elements/base.py`**: added `num_nodes: ClassVar[int] = 2` to
   `Element` (default preserves every existing element's behavior
   unchanged) and updated the "Adding a new element type" docstring to
   mention it.
2. **`circsim/netlist.py`**: generalized `_parse_element_line` to slice
   `tokens[1 : 1+num_nodes]` for node names and everything after that for
   params, using `element_cls.num_nodes` instead of the literal `2`; error
   message now lists `n1 n2 ... nK` placeholders instead of hard-coding
   `nA nB`. `circuit.py` needed **no change at all** -- it already builds
   `node_names` generically from `element.nodes` (a tuple of arbitrary
   length), which I confirmed by reading it rather than assuming.
3. **`circsim/mna.py`**: added `stamp_vcvs_branch(n_plus, n_minus,
   ctrl_plus, ctrl_minus, branch_index, gain)`, structurally a copy of
   `stamp_voltage_branch`'s output-pin coupling (unchanged: this part of an
   ideal VCVS's output behaves exactly like an ideal V source) with the
   constraint row replaced: instead of `z[branch] = value`, it stamps
   `A[branch, ctrl_plus] -= gain` and `A[branch, ctrl_minus] += gain`,
   leaving `z[branch] = 0` (a homogeneous constraint). Derived directly
   from the constraint equation in CHANGE.md:
   `V(nP) - V(nM) - gain*(V(ncP)-V(ncM)) = 0`.
4. **`circsim/elements/vcvs.py`** (new file): `VCVS` class, `prefix="E"`,
   `num_nodes=4`, `num_params=1`, one extra var (`"{name}:branch"`), and a
   `stamp()` that reads `self.nodes` as `(n_plus, n_minus, ctrl_plus,
   ctrl_minus)` and calls the new MNA primitive. Uses
   `effective_value(ctx)` for the gain (inherited for free -- this also
   means a `.dc` sweep can target `E1`'s gain by name, which I hadn't been
   asked for but verified works, since the override mechanism is already
   generic across element types).
5. **`circsim/elements/__init__.py`**: added `vcvs` to the import list so
   `@register_element` actually runs.
6. Nothing else needed touching: `analyses/op.py`, `dc.py`, `tran.py`, and
   `mna.assemble_and_solve` are all element-type-agnostic (they only call
   `extra_vars()`/`stamp()`/`post_solve()` through the base-class
   interface), so "work in all three analyses" fell out for free once the
   stamp was correct -- verified, not just assumed (see below).

## Verification

- Ran the self-check netlist from `CHANGE.md` (`V1 in 0 2` / `E1 out 0 in 0
  3` / `R1 out 0 1000` / `.op`) via `python run.py`: got
  `V(out)=6.0000...`, `V(in)=2.0` -- matches exactly. Saved as
  `examples/vcvs.ckt`.
- Manually ran the same circuit under `.dc` (sweeping `V1`) and `.tran`
  (VCVS output driving an RC load) before writing any pytest, to catch
  mode-specific breakage early: both produced the expected
  `V(out) = 3*V(in)` regardless of load or analysis mode. Also checked that
  sweeping the VCVS's own gain via `.dc E1 ...` works (uses the existing
  generic override mechanism, not special-cased).
- Checked the malformed-line error path by hand
  (`E1 out 0 in 0` missing the gain, and `E1 out 0 in` missing a node) --
  both raise `NetlistError` with a `num_nodes`-aware message
  (`'E<name> n1 n2 n3 n4' plus 1 value(s)`), not a `tokens[2]` `IndexError`
  from the old hard-coded slicing.
- `python -m pytest -q` before any change: **57 passed** (baseline).
  After the change, full suite (including 13 new VCVS tests in
  `tests/test_vcvs.py` plus one new CLI example test): **71 passed**, 0
  regressions.
- New tests in `tests/test_vcvs.py` cover: registry/parsing (4 nodes + 1
  param parsed correctly, rejects wrong arg counts), the exact CHANGE.md
  self-check, output voltage independent of load resistance (10Ω/1kΩ/1MΩ),
  negative gain (inversion), a control pair that is *not* referenced to
  ground (exercises the `V(ncP)-V(ncM)` difference rather than a single
  absolute voltage), a direct check of the branch-current unknown against
  the load current computed via Ohm's law (proves the extra unknown is
  actually solved, not left at zero), a `.dc` sweep of the controlling
  source, a `.dc` sweep of the VCVS's own gain, and two `.tran` checks (one
  at steady state confirming `V(out)=gain*V(in))` matches the `.op` answer,
  one at a single very-early timestep confirming the VCVS's ideal output
  is *not* subject to the RC load's lag the way the load node itself is).
  Also added `examples/vcvs.ckt` and a matching CLI-subprocess test in
  `tests/test_cli_examples.py`, mirroring the existing worked-example
  pattern.
- Post-edit `stitchgraph reindex . --db .sg.db` (31 files, 169 nodes) then
  `stitchgraph find-holes --db .sg.db` -> 0 holes, and `stitchgraph
  find-stale --db .sg.db` -> 0 stale nodes. Both came back clean on the
  first try, meaning the new module was fully wired in (import in
  `elements/__init__.py` present, `VCVS` reachable, no leftover dead
  references) -- a fast, cheap confirmation that didn't require me to
  manually trace every import.

## Dead ends / near-misses

- My first instinct for the branch-current sanity test was to assert the
  VCVS's solved branch-current unknown equals `+V(out)/R_load` (current
  the load draws). It came back as `-0.006` instead of `+0.006`. Rather
  than "fix" the stamp, I checked whether this was a stamp bug or just a
  sign-convention fact by running the *existing*, untouched `V` + `R`
  circuit through the same introspection (`get_branch_current` on
  `V1`) -- it showed the identical relationship (`-0.01` against an
  expected `+0.01` load current). That confirmed the sign flip is an
  existing, consistent internal convention of `stamp_voltage_branch`
  (which my VCVS stamp deliberately reuses verbatim for the output-pin
  part), not a bug I introduced -- so I fixed the *test's* expectation
  instead of the code. Worth flagging for whoever adds the next branch-
  current-based element: `get_branch_current()`'s sign is the negative of
  "current delivered from n_plus to the load," and that's not written down
  anywhere in `mna.py`'s docstrings currently.
- Considered whether `Circuit.__init__`'s node-collection loop
  (`node_names.update(element.nodes)`) would need a change for a
  4-node element. Read it before touching anything: it already iterates
  over `element.nodes` generically with no arity assumption, so this was a
  non-change, confirmed by reading rather than by trial and error.

## Where `stitchgraph` helped vs. didn't (honest assessment)

- **Helped**: `orient` up front, as a table of contents before reading
  code -- it pointed straight at `Element`/`AnalysisContext`/`MNASystem`
  as the load-bearing abstractions, which matched exactly what turned out
  to matter. `impact-of` on the two symbols I was about to change
  (`_parse_element_line`, `Element.__init__`) gave concrete confirmation
  that "run the whole suite, not a subset" was the right call for this
  change -- useful less as new information (a base class touching
  everything is unsurprising) and more as a forcing function against
  under-testing. `find-holes`/`find-stale` after the edit were cheap,
  fast, and came back clean, which is exactly the reassurance they're for.
- **Didn't add much / was redundant with reading**: `find-similar` on "stamp
  a voltage source" surfaced exactly the files I'd already located by
  reading `elements/base.py`'s own "how to add an element" docstring and
  following its pointer to `vsource.py`. In a codebase this size (29
  files) with unusually good self-documentation (the base classes
  literally spell out the extension steps), semantic search and direct
  reading converged on the same answer, so the tool's marginal value here
  was confirmation rather than discovery. `get-callers "nodes"` failed
  outright ("not a unique symbol") -- a reminder that attribute-style
  names need a qualified query (e.g. `Element.nodes`) or a different tool
  (grep) in this size of codebase; I didn't retry it because grep answered
  the same question faster once I noticed `circuit.py`'s single call site
  by reading.
- **Net take**: for a codebase this small and this well-organized (clean
  one-directional module graph, registries instead of if/elif chains,
  extension points documented in the code itself), stitchgraph's biggest
  win was orientation-in-seconds (`orient`) and a cheap regression net
  (`impact-of`, `find-holes`, `find-stale`) around an edit -- not discovery
  that direct reading wouldn't have gotten to almost as fast. I'd expect
  the discovery tools (`find-similar`, `trace-path`) to earn their keep
  more clearly in a codebase too large to read end-to-end in one sitting,
  or one without this project's habit of documenting its own extension
  points inline.
