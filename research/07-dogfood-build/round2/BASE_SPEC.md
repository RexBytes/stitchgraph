# Build task: a circuit simulator (Python) — base build

Build a working **circuit simulator** as a clean, modular Python package in your assigned project
directory. It parses a SPICE-style netlist, solves with **Modified Nodal Analysis (MNA)**, and
supports three analyses. Design for extensibility — new element types and new analyses should each
be localized and easy to add.

## Elements (netlist lines)

Node `0` is ground (0 V). Lines beginning with `*` are comments; blank lines ignored. Values are
plain numbers (`1000`, `4.7e3`, `1e-6`); engineering suffixes (k, m, u, n, p, meg) optional.

```
R<name> nA nB value          resistor, ohms
V<name> nP nM value          independent DC voltage source, volts (V(nP)-V(nM)=value)
I<name> nP nM value          independent DC current source, amps; `value` A flows from nP to nM
                             THROUGH the source (removes value A from nP, injects value A into nM)
C<name> nA nB value          capacitor, farads (initial voltage 0 at t=0)
L<name> nA nB value          inductor, henries (initial current 0 at t=0)
```

## Analyses (directives; exactly one per netlist)

```
.op                          DC operating point (resistors + sources; C = open, L = short at DC)
.tran tstep tstop            transient from 0 to tstop, Backward Euler.
                             C companion: conductance C/h ∥ current source from previous V.
                             L companion: resistance L/h in series-equivalent; i.e. a conductance
                             h/L ∥ current source carrying the previous inductor current.
.dc SRCNAME start stop step  DC sweep: set independent source SRCNAME to each value in
                             [start, start+step, ..., stop] and solve .op at each point.
```

## Required interface (you are judged on this — target it exactly)

Provide `run.py` at the project root. `python run.py <netlist>` parses, runs the analysis, and prints
**one JSON object to stdout** (logs to stderr). Node voltages are relative to ground; omit node `0`.

- `.op`  → `{"analysis": "op", "nodes": {"<n>": <volts>, ...}}`
- `.tran`→ `{"analysis": "tran", "tstop": <t>, "nodes": {"<n>": <volts at tstop>, ...}}`
- `.dc`  → `{"analysis": "dc", "source": "<SRCNAME>", "sweep": [{"value": <v>, "nodes": {...}}, ...]}`

## Worked examples (self-check; the grader uses different circuits)

`divider.ckt` — `V1 in 0 10` / `R1 in out 1000` / `R2 out 0 1000` / `.op` → V(out)=5.0
`rc.ckt` — `V1 in 0 1` / `R1 in n 1000` / `C1 n 0 1e-6` / `.tran 1e-6 1e-3` → V(n)≈0.632
`sweep.ckt` — `V1 in 0 0` / `R1 in out 1000` / `R2 out 0 1000` / `.dc V1 0 10 5` →
  sweep at V1∈{0,5,10} gives V(out)∈{0, 2.5, 5.0}

## Deliverables

1. The working package + `run.py` handling `.op`, `.tran`, `.dc` (passes the three examples above).
2. Your own pytest unit tests, passing.
3. A `DEVLOG.md` documenting your build.

Work only inside your assigned project directory. Aim for a clean, well-separated module layout
(parsing / element model / MNA assembly / solver / analyses / results formatting / CLI) — the code
will later be extended by someone else, so make the structure legible.
