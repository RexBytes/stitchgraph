# stitchgraph v3.2.0 — the body matrix learns JavaScript / TypeScript / TSX

v3.0.0 added the intra-procedural **body matrix** for Python — a per-function value-flow fingerprint
that sees *inside* a function, powering `find_similar(mode="structure")` and a body-aware
`graph_diff`. v3.2.0 ports it to the **JavaScript family** (JS, TS, TSX) — the first step of the
multi-language roadmap (`docs/IDEAS.md` §5b), validating the porting recipe in
`docs/BODY_MATRIX_LESSONS.md` before any wider sweep.

A new representation for new languages earns the MINOR bump, but it is **backward-compatible**:
schema, on-disk indexes, and every existing operation are unchanged, and the new behavior is opt-in
and advisory.

## Added

### `core/structure_js.py` — a JS/TS/TSX value-flow walker
A tree-sitter walker that emits the **same `_VFG` vocabulary** as the Python frontend (operations +
control points, data + control edges, copy propagation) and reuses the language-neutral
Weisfeiler-Lehman kernel (`structure._wl_features` / `similarity`). So a JS clone with renamed
locals, reordered independent statements, or an arrow rewrite fingerprints as the *same shape* —
exactly as for Python. One walker covers all three grammars; it captures every idiomatic function
form (declarations, methods, `const f = () =>…` / `= function(){…}`, object methods, class-field
arrows, nested functions) and treats TS type annotations as no-value-flow, so **`TS ≡ JS`**.

### `find_similar(mode="structure")` — now language-aware
Auto-detects the snippet's language (Python first, else the JS/TS family) and ranks it **only against
stored functions of the same language**. A body fingerprint's topology tracks its extractor, so
cross-language scores aren't comparable — same-language ranking keeps the result honest.

### `graph_diff` — body layer covers JS too
For JS/TS/TSX functions present in both indexes, a diverged **body shape** is reported just like
Python — so a data-flow change that leaves the call graph identical (the plan-vs-actual /
translation-fidelity signal) is caught in JS as well. Same-language by construction (a node id maps
to one file).

## Scope & caveats

- **Advisory and read-only**, like the Python layer — it never feeds `find_stale`, so the cardinal
  rule (*live code is never confidently flagged dead*) is structurally unaffected.
- The JS layer needs the optional **tree-sitter extra**; without it, the JS paths return nothing
  (the body layer simply adds nothing). The Python body matrix remains stdlib-only.
- **Cross-language body comparison stays oracle-only** — per the matrix-as-oracle research, topology
  tracks the extractor across languages, so a Python fingerprint and a JS one are not directly
  comparable. The features rank/diff within one language.
- Same structural-approximation limits as Python (copy propagation, no SSA/φ/alias, constants
  collapsed). The JS construct→value-flow mapping is its own work; the method is in
  `docs/BODY_MATRIX_LESSONS.md`.

## Quality gate

- ruff + mypy clean; full suite **762** passing; differential oracle suite **140**.
- New **JS/TS body-matrix completeness oracle** (`tests/oracles/test_structure_js_completeness.py`):
  51 metamorphic cases — `helper()` (a CALL) vs `0` (a CONST) in every value-bearing statement and
  expression position must change the fingerprint. It caught a real drop during development
  (template-literal substitutions, the JS analogue of the Python f-string bug) and a TS
  `as`/`satisfies` cast that dropped its operand, both now fixed.
- Mutation meta-oracle unchanged: `structure.py` 15/15, `graphdiff` 9/9, `similar.py` 29/32.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean.

## Upgrading

Nothing to do — no schema/API/behavior change to existing operations; indexes don't need
rebuilding. To try the JS body matrix (with the tree-sitter extra installed):

```python
import stitchgraph as sg
with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")          # a JS/TS project
    # rank stored JS/TS functions by body shape (renamed/reordered/arrow clones)
    print(sg.find_similar(store, open("some_fn.ts").read(), mode="structure"))
    print(sg.graph_diff(store, "other_index.db"))   # body-aware, JS + Python
```
