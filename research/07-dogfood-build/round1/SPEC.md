# Build task: a small circuit simulator (Python)

Build a working **circuit simulator** as a Python package, from scratch, in the project
directory you are told to use. It parses a SPICE-style netlist, solves the circuit with
**Modified Nodal Analysis (MNA)**, and prints node voltages.

You decide the internal architecture, but aim for a clean, modular design (e.g. separate
concerns: netlist parsing, element/component model, matrix assembly, the linear solve, the
analyses, and a CLI entry point). Write your own unit tests and make them pass.

## Numerics scope (what must work)

1. **DC operating point (`.op`)** — resistors + independent DC voltage/current sources.
2. **Transient (`.tran tstep tstop`)** — add capacitors, integrated with **Backward Euler**
   (companion model: a capacitor becomes a conductance `C/h` in parallel with a current
   source carrying its previous-step value). All capacitors start at **0 V** at `t=0`.

You may use `numpy` (it is installed) for the linear solve.

## Netlist format (a SPICE subset)

Plain text, one element or directive per line. Node `0` is **ground** (fixed at 0 V).
Lines beginning with `*` are comments; blank lines are ignored. Values are plain numbers
(e.g. `1000`, `4.7e3`, `1e-6`); engineering suffixes are optional.

```
R<name> nodeA nodeB value        resistor, ohms
V<name> nodeP nodeM value        independent DC voltage source, volts (V(nodeP) - V(nodeM) = value)
I<name> nodeP nodeM value        independent DC current source, amps.
                                 Convention: `value` amps flow from nodeP to nodeM THROUGH the
                                 source — i.e. the source removes `value` A from nodeP and
                                 injects `value` A into nodeM.
C<name> nodeA nodeB value        capacitor, farads (initial voltage 0 at t=0)
.op                              analysis: DC operating point
.tran tstep tstop                analysis: transient from t=0 to tstop with step tstep
.end                             end of netlist (optional)
```

A netlist contains exactly one analysis directive (`.op` or `.tran`).

## Required interface (this is how you will be judged — target it exactly)

Provide an executable **`run.py` at your project root**. Running:

```
python run.py <netlist_path>
```

must parse the netlist, run whichever analysis it contains, and print **only a single JSON
object to stdout** (send any logging to stderr). Node voltages are relative to ground; omit
node `0`.

- For `.op`:
  `{"analysis": "op", "nodes": {"<nodeName>": <volts>, ...}}`
- For `.tran`:
  `{"analysis": "tran", "tstop": <tstop>, "nodes": {"<nodeName>": <volts at t=tstop>, ...}}`

(The `.tran` result reports each node's voltage **at the final time `tstop`**.)

## Worked examples (use these to self-check; the grader uses different circuits)

**Voltage divider** — `divider.ckt`:
```
V1 in 0 10
R1 in out 1000
R2 out 0 1000
.op
```
Expected: `V(in)=10`, `V(out)=5.0`.

**RC charging** — `rc.ckt`:
```
V1 in 0 1
R1 in n 1000
C1 n 0 1e-6
.tran 1e-6 1e-3
```
Here `R*C = 1e-3 s`, so at `tstop = 1e-3` (one time constant) `V(n) ≈ 1*(1 - e^-1) ≈ 0.632 V`.

## Deliverables

1. The working package + `run.py` (passes the two worked examples above).
2. Your own unit tests (pytest), passing.
3. A **`DEVLOG.md`** documenting your development process step by step: the order you built
   things, decisions, how you navigated/verified your own growing codebase, any dead ends,
   and how you checked correctness. Be concrete and honest.

Work only inside your assigned project directory. Stop when you believe the requirements are
met and your `run.py` handles both the `.op` and `.tran` worked examples correctly.
