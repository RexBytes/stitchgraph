# stitchgraph v3.5.0 — the body matrix learns C and C++

v3.0.0 added the intra-procedural **body matrix** for Python; v3.2.0 ported it to the JavaScript
family, v3.3.0 to Go, v3.4.0 to Rust. v3.5.0 adds **C and C++** — language 4 of the multi-language
sweep (`docs/IDEAS.md` §5b), and the one the lessons doc flagged as likely to be *harder* (pointers,
the preprocessor, out-of-line method definitions, templates).

A new language for an existing representation earns the MINOR bump, but it is **backward-compatible**:
schema, on-disk indexes, and every existing operation are unchanged, and the new behavior is opt-in
and advisory.

## Added

### `core/structure_cpp.py` — one walker for C and C++
The `cpp` tree-sitter grammar is a superset that parses C cleanly, so a single walker covers both
(the extractor already unifies them). It emits the **same `_VFG` vocabulary** as the other frontends
and reuses the WL kernel, so a C/C++ clone with renamed locals or reordered statements fingerprints as
the *same shape*. Specifics handled:

- **Declarator name extraction** — the function name lives *inside* the declarator; the walker
  unwraps `pointer_declarator`/`reference_declarator` (`int* f()`, `int& f()`) to reach it, and an
  out-of-line `int Foo::m()` keys to the bare last component `m` (matching the extractor).
- **Statement-oriented** (explicit `return`, unlike Rust's trailing expression).
- Compound assignment (`x += e` ≡ `x = x + e`), `?:`, C-style/functional **casts** (operand flows,
  the type carries none), `*p`/`&x`, `a[i]` (the cpp grammar's `indices` → `subscript_argument_list`
  shape), range-`for`, `switch` (case value walked once), `new`/`delete`, initializer lists.
- **Lambdas** (C++) are opaque `NESTED` leaves; `this` carries value flow.
- **`sizeof(expr)`** collapses to a CONST — *correct*, not a dropped sub-expression: `sizeof` never
  evaluates its operand (C/C++ semantics), so `sizeof(helper())` genuinely equals `sizeof(0)`.
- **The preprocessor is not expanded.** A function-like `#define` macro is a preprocessor construct,
  not a `function_definition`, so it's out of scope; a *call* to a macro parses as a normal
  `call_expression` and is fingerprinted like any call.

### `find_similar(mode="structure")` — now detects C/C++
Auto-detects the snippet's language (Python → JS/TS family → Go → Rust → C/C++) and ranks it **only
against stored functions of the same language**.

### `graph_diff` — body layer covers C/C++ too
A diverged body shape in a C/C++ function present in both indexes is reported just like the other
frontends. Same-language by construction.

## Scope & caveats

- **Advisory and read-only** — it never feeds `find_stale`, so the cardinal rule (*live code is never
  confidently flagged dead*) is structurally unaffected.
- The C/C++ layer needs the optional **tree-sitter extra**; without it the C/C++ paths return nothing
  (advisory degrade). The Python body matrix remains stdlib-only.
- **Cross-language body comparison stays oracle-only** — topology tracks the extractor; the features
  rank/diff within one language.
- Same structural-approximation limits as the other frontends, plus C/C++-specific ones: **no
  pointer/alias analysis**, the **preprocessor is not expanded**, and templates are fingerprinted as
  written (not per-instantiation). Constants are collapsed (so `sizeof` is a constant). Qualname
  scheme matches the extractor: free/namespace/template functions bare, inline methods
  `Class.method`, out-of-line `Foo::m` bare `m`. The method is in `docs/BODY_MATRIX_LESSONS.md`.

## Quality gate

- ruff + mypy clean; full suite **915** passing; differential oracle suite **281**.
- New **C/C++ body-matrix completeness oracle** (`tests/oracles/test_structure_cpp_completeness.py`):
  40 metamorphic cases — `helper()` (a CALL) vs `0` (a CONST) in every value-bearing statement and
  expression position must change the fingerprint — plus dedicated invariants (compound-assign
  rebind, cast carries operand not type, out-of-line keyed bare vs inline keyed `Class.method`,
  `sizeof` is a compile-time constant, nested lambda opaque, reference-return function captured,
  constructor member-initializer-list walked, array-new size captured). The oracle caught a real
  drop during development: the cpp grammar keeps a subscript index under
  `indices`/`subscript_argument_list`, not C's `index` field, so `a[helper()]` initially collapsed.
- Mutation meta-oracle unchanged: `structure.py` 15/15, `graphdiff` 9/9, `similar.py` 29/32.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean.

## Upgrading

Nothing to do — no schema/API/behavior change to existing operations; indexes don't need
rebuilding. To try the C/C++ body matrix (with the tree-sitter extra installed):

```python
import stitchgraph as sg
with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")          # a C / C++ project
    print(sg.find_similar(store, open("some_fn.cpp").read(), mode="structure"))
    print(sg.graph_diff(store, "other_index.db"))   # body-aware, C/C++ + Rust + Go + JS + Python
```
