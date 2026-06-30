# stitchgraph v3.4.0 — the body matrix learns Rust

v3.0.0 added the intra-procedural **body matrix** for Python; v3.2.0 ported it to the JavaScript
family and v3.3.0 to Go. v3.4.0 adds **Rust** — language 3 of the multi-language sweep
(`docs/IDEAS.md` §5b), driven by the same porting recipe in `docs/BODY_MATRIX_LESSONS.md`.

A new language for an existing representation earns the MINOR bump, but it is **backward-compatible**:
schema, on-disk indexes, and every existing operation are unchanged, and the new behavior is opt-in
and advisory.

## Added

### `core/structure_rust.py` — a Rust value-flow walker
A tree-sitter walker that emits the **same `_VFG` vocabulary** as the Python, JS, and Go frontends
(operations + control points, data + control edges, copy propagation) and reuses the language-neutral
Weisfeiler-Lehman kernel. So a Rust clone with renamed locals, reordered independent statements, or
different literals fingerprints as the *same shape*. Rust specifics handled:

- **Expression-oriented blocks** — a block's trailing expression (no `;`) is its value, so `{ x }`
  fingerprints like `{ return x; }`. `if`/`match`/`loop`/`while`/`for` are expressions and yield
  values.
- **Operand-carrying wrappers** — `expr?`, references (`&x`), `as` casts, `.await`, ranges, tuples,
  arrays, and struct literals carry their operand's value flow; the cast/asserted *type* carries
  none.
- **Macros** — `vec![…]` / `println!(…)` expose their arguments as a raw token tree, not parsed
  expressions. We walk the tree's identifier/literal tokens best-effort, so a variable passed to a
  macro still threads value flow.
- **Closures** (`|x| …`) are opaque `NESTED` leaves; `self` and named results seed like parameters.

### `find_similar(mode="structure")` — now detects Rust
Auto-detects the snippet's language (Python → JS/TS family → Go → Rust) and ranks it **only against
stored functions of the same language**. A body fingerprint's topology tracks its extractor, so
cross-language scores aren't comparable.

### `graph_diff` — body layer covers Rust too
For Rust functions/methods present in both indexes, a diverged **body shape** is reported just like
the other frontends — so a data-flow change that leaves the call graph identical is caught in Rust as
well. Same-language by construction.

## Scope & caveats

- **Advisory and read-only** — it never feeds `find_stale`, so the cardinal rule (*live code is never
  confidently flagged dead*) is structurally unaffected.
- The Rust layer needs the optional **tree-sitter extra**; without it the Rust paths return nothing
  (advisory degrade). The Python body matrix remains stdlib-only.
- **Cross-language body comparison stays oracle-only** — topology tracks the extractor; the features
  rank/diff within one language.
- Same structural-approximation limits as the other frontends (copy propagation, no SSA/φ, no
  borrow/lifetime/alias analysis, constants collapsed; macro bodies are token-walked, not expanded).
  Qualname scheme matches the Rust extractor: free functions bare (`free_fn`), impl methods
  `Type.method`. The method is in `docs/BODY_MATRIX_LESSONS.md`.

## Quality gate

- ruff + mypy clean; full suite **862** passing; differential oracle suite **232**.
- New **Rust body-matrix completeness oracle** (`tests/oracles/test_structure_rust_completeness.py`):
  37 metamorphic cases — `helper()` (a CALL) vs `0` (a CONST) in every value-bearing statement and
  expression position must change the fingerprint — plus dedicated invariants (trailing expression
  equals explicit return, `x += e` rebinds like `x = x + e`, `x as T` carries the operand's flow not
  the type, `&self` seeded like a parameter, a nested closure opaque).
- Mutation meta-oracle unchanged: `structure.py` 15/15, `graphdiff` 9/9, `similar.py` 29/32.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean.

## Upgrading

Nothing to do — no schema/API/behavior change to existing operations; indexes don't need
rebuilding. To try the Rust body matrix (with the tree-sitter extra installed):

```python
import stitchgraph as sg
with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")          # a Rust project
    # rank stored Rust functions by body shape (renamed/reordered clones)
    print(sg.find_similar(store, open("some_fn.rs").read(), mode="structure"))
    print(sg.graph_diff(store, "other_index.db"))   # body-aware, Rust + Go + JS + Python
```
