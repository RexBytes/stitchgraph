# stitchgraph v3.3.0 — the body matrix learns Go

v3.0.0 added the intra-procedural **body matrix** for Python — a per-function value-flow fingerprint
that sees *inside* a function, powering `find_similar(mode="structure")` and a body-aware
`graph_diff`. v3.2.0 ported it to the JavaScript family; v3.3.0 adds **Go** — language 2 of the
multi-language sweep (`docs/IDEAS.md` §5b), driven by the same porting recipe in
`docs/BODY_MATRIX_LESSONS.md`.

A new language for an existing representation earns the MINOR bump, but it is **backward-compatible**:
schema, on-disk indexes, and every existing operation are unchanged, and the new behavior is opt-in
and advisory.

## Added

### `core/structure_go.py` — a Go value-flow walker
A tree-sitter walker that emits the **same `_VFG` vocabulary** as the Python and JS frontends
(operations + control points, data + control edges, copy propagation) and reuses the language-neutral
Weisfeiler-Lehman kernel (`structure._wl_features` / `similarity`). So a Go clone with renamed locals,
reordered independent statements, or different literals fingerprints as the *same shape* — exactly as
for Python. It covers Go's statement/expression set:

- short-var (`:=`), `var`/`const`, and **multi-value** assignment (`a, b := f()`, `a, b = b, a`);
- compound assignment (`x += e` ≡ `x = x + e`) and `x++` / `x--`, both rebinding the target;
- `if` (with initializer), `for` (clause / `range` / cond-only), `switch` / `type switch`, `select`;
- channel `send` / receive, `go` / `defer`, slices, composite literals (struct/slice/map),
  type assertions (`x.(T)`) and conversions (`T(x)`).

It seeds the method **receiver** and **named results** like parameters, and treats nested `func`
literals as opaque leaves — matching the Go extractor, which keys a method by its bare field name
(`Method`, not `T.Method`) and does not mint closures as nodes.

### `find_similar(mode="structure")` — now detects Go
Auto-detects the snippet's language (Python first, else the JS/TS family, else Go) and ranks it **only
against stored functions of the same language**. A body fingerprint's topology tracks its extractor,
so cross-language scores aren't comparable — same-language ranking keeps the result honest.

### `graph_diff` — body layer covers Go too
For Go functions/methods present in both indexes, a diverged **body shape** is reported just like
Python and JS — so a data-flow change that leaves the call graph identical (the plan-vs-actual /
translation-fidelity signal) is caught in Go as well. Same-language by construction (a node id maps
to one file).

## Scope & caveats

- **Advisory and read-only**, like the other layers — it never feeds `find_stale`, so the cardinal
  rule (*live code is never confidently flagged dead*) is structurally unaffected.
- The Go layer needs the optional **tree-sitter extra**; without it, the Go paths return nothing (the
  body layer simply adds nothing). The Python body matrix remains stdlib-only.
- **Cross-language body comparison stays oracle-only** — per the matrix-as-oracle research, topology
  tracks the extractor across languages, so a Go fingerprint and a Python one are not directly
  comparable. The features rank/diff within one language.
- Same structural-approximation limits as the other frontends (copy propagation, no SSA/φ/alias or
  escape analysis, channel/pointer semantics flattened to value flow, constants collapsed). The Go
  construct→value-flow mapping is its own work; the method is in `docs/BODY_MATRIX_LESSONS.md`.

## Quality gate

- ruff + mypy clean; full suite **816** passing; differential oracle suite **190**.
- New **Go body-matrix completeness oracle** (`tests/oracles/test_structure_go_completeness.py`):
  45 metamorphic cases — `helper()` (a CALL) vs `0` (a CONST) in every value-bearing statement and
  expression position must change the fingerprint — plus dedicated invariants (receiver seeded like a
  parameter, `x += e` rebinds like `x = x + e`, `x.(T)` carries the operand's flow not the type, a
  switch case value walked once, a nested closure opaque).
- Mutation meta-oracle unchanged: `structure.py` 15/15, `graphdiff` 9/9, `similar.py` 29/32.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean.

## Upgrading

Nothing to do — no schema/API/behavior change to existing operations; indexes don't need
rebuilding. To try the Go body matrix (with the tree-sitter extra installed):

```python
import stitchgraph as sg
with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")          # a Go project
    # rank stored Go functions by body shape (renamed/reordered clones)
    print(sg.find_similar(store, open("some_fn.go").read(), mode="structure"))
    print(sg.graph_diff(store, "other_index.db"))   # body-aware, Go + JS + Python
```
