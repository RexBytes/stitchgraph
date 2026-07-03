# Change request: add a voltage-controlled voltage source (VCVS)

You have been handed an **existing** circuit-simulator codebase that **you did not write**. Read
`SPEC.md` for what it already does (a SPICE-subset simulator: R/V/I/C/L elements; `.op`, `.tran`,
`.dc` analyses; `run.py` prints JSON). Familiarize yourself with the existing structure before
changing it.

## What to add

Add support for a **linear voltage-controlled voltage source (VCVS)** — the SPICE `E` element:

```
E<name> nP nM ncP ncM gain
```

It enforces an ideal constraint on the output node pair, independent of load:

```
V(nP) - V(nM) = gain * (V(ncP) - V(ncM))
```

`nP`/`nM` are the output nodes; `ncP`/`ncM` are the controlling (sense) nodes; `gain` is a
dimensionless number (plain value / engineering suffix, like other element values). It draws
whatever output current is needed to hold the constraint (like an ideal op-amp output).

### Requirements

1. Parse the `E` line into a new element type.
2. Stamp it correctly in Modified Nodal Analysis. Like an independent voltage source, a VCVS
   introduces **one extra branch-current unknown**; its constraint row couples in the two control
   nodes with the `gain` factor. (Work out the stamp from the constraint equation above.)
3. It must work in **all existing analyses** — `.op`, `.tran`, and `.dc` — not just one.
4. **Do not regress existing behavior.** All previously-working netlists and the existing test
   suite must still pass. Run the existing tests.
5. Add your own tests for the VCVS (at least: a basic gain check, and one where the VCVS output
   drives a resistive load so the extra branch current is exercised).

### Self-check example

```
V1 in 0 2
E1 out 0 in 0 3
R1 out 0 1000
.op
```
The VCVS forces `V(out) = 3 * V(in) = 6.0 V` regardless of `R1`.

## Deliverables

- The VCVS working end-to-end (`run.py` on a netlist containing an `E` element produces correct
  node voltages), existing analyses unbroken, your new tests passing.
- Append to `DEVLOG.md`: how you oriented yourself in this unfamiliar codebase, how you found the
  places that needed changing (parser, element model, MNA branch-unknown numbering + stamping,
  and anywhere else), how you verified nothing regressed, and any dead ends. Be concrete and honest.
