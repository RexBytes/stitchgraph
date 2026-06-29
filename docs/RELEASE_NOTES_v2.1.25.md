# stitchgraph v2.1.25 — C/C++ function-pointer table / vtable promotion (#69)

A cardinal fix for C/C++: a function whose address is taken in a global dispatch table is no longer
flagged dead when its translation unit is a passive registration unit.

## The bug

```c
// reg.c — no main, no entry point of its own
int op_a(int x) { return x + 1; }
int op_b(int x) { return x - 1; }
int (*ops[])(int) = { op_a, op_b };   // global function-pointer table

// main.c
extern int (*ops[])(int);
int main(void) { return ops[0](5); }  // invokes op_a indirectly, in a different TU
```

`op_a`/`op_b` are live — invoked through `ops` from another translation unit — but were confidently
flagged dead. The same held for plugin/vtable structs (`struct ops P = {init, teardown}`),
designated-initializer tables (`{[0]=on_start}`), and scalar function-pointer globals (`cb h =
handler`).

A C global variable is shared across translation units via `extern`, but globals are not graph
nodes, so the cross-TU reference (`ops` used in `main.c`) is invisible to the graph. The address-take
of `op_a`/`op_b` happens in `reg.c`'s module scope, and `reg.c` has no entry point of its own, so its
module node is never seeded — leaving the functions unreachable.

## The fix

A new `_c_global_init_fn_refs` scan walks C/C++ **module scope** — top-level declarations, plus
`namespace {…}` and `extern "C" {…}` bodies, never descending into a function body — and collects the
function identifiers in global-variable initializers. Matching project C/C++ functions/methods are
rooted `callback` (invoked indirectly through the global), mirroring the object-literal (#75) and
macro-body (#59) indirect-dispatch rooting.

- **Dialect-robust:** it matches the `initializer_list` node directly, the common denominator. C
  parses the global as a `declaration` → `init_declarator`; C++ *mis-parses* `int (*tab[])() = {…}`
  (and the same inside a namespace) as an `expression_statement` → `assignment_expression` whose
  right side is still an `initializer_list`. Both are caught.
- **Project-wide** across the unified C/C++ bucket — the table and the function it points at
  routinely live in different files.
- **Cardinal-safe over-approximation:** resolution is by name to F/M nodes only, so a non-function
  initializer identifier (a global const, an enum value) merely over-roots a homonym; it can never
  flag live code dead.
- Local (in-function) function-pointer assignments are unchanged — already covered by `_direct_refs`.
- A function whose address is taken in **no** global initializer still flags dead (verified,
  including in a rootless TU).

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Known limitations

A C `struct` used only as the type of a global/extern variable is still flagged dead (#89) — a
bodyless data type, not executable code, so flagging it hides no logic (the same class as an abstract
method declaration). A function over-rooted because its name happens to appear in an *unused* global
initializer is a bounded, cardinal-safe precision cost. The JS/TS implicit-dispatch surface (#54)
remains pre-existing and deferred.

## Quality gate

Full suite — 520 tests (function-pointer table same-file and cross-TU, plugin/vtable struct,
designated initializer, C++ namespace / `extern "C"` / rootless-TU, scalar function pointer; plus
over-rooting guards that a function in no table and a static sibling not named in any initializer
still flag) + ruff + mypy clean; differential oracle suite (27) green; mutation meta-oracle on
`_c_global_init_fn_refs` + `_collect_init_idents` (5/5 killed); two-round full-diversity multi-model
adversarial review — no in-scope cardinal, no crash.
