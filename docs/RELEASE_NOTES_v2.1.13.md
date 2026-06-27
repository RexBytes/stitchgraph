# stitchgraph v2.1.13 — Runtime/native entry-point attributes: C ISR, Rust `#[ctor]`, Java `native`

Three narrow cardinal fixes that extend the v2.1.9 "runtime / native (FFI) entry-point" arc to the
remaining attribute/modifier-marked entry points each language's runtime or toolchain invokes
automatically — no in-tree by-name caller, so the function (and whatever its body reaches) was
false-flagged dead. Found by the doc-driven manual pass over each language's reference. All three are
attribute/modifier-gated and only ever **add** roots — cardinal-safe by construction.

## The bugs & fixes

- **C interrupt service routines** — `__attribute__((interrupt))` (and AVR `((signal))`, ARM
  `interrupt_handler`) mark a function the hardware vector table calls; there is no in-tree caller.
  The implicit-entry attribute regex covered `constructor`/`destructor`/`used`/`retain`/`section`
  but omitted `interrupt`/`signal`. Now matched → rooted `callback` (so the ISR and its callees stay
  live). The GNU `__interrupt__` synonym is covered by the existing `_*` handling.

- **Rust `#[ctor]` / `#[dtor]`** — the `ctor` crate's `#[ctor::ctor]` / `#[ctor]` / `#[ctor::dtor]`
  run a function automatically before/after `main` (the direct Rust analogue of C
  `__attribute__((constructor))`, which the C extractor already roots). They are idiomatically
  *private*, so the `pub` safety net never fired. Added `ctor`/`dtor` to `_RUST_RUNTIME_ENTRY_ATTRS`,
  matched as path tokens (`#[ctor::ctor]` and bare `#[ctor]` both hit; `#[constructor_helper]` does
  not).

- **Java `native` methods** — a `native` method is a JNI entry point implemented in C and invoked
  across the JNI boundary; it has no Java body and no in-tree caller. A non-`public` one was flagged
  dead (a `public` one survived only incidentally via the `exported` role). New `_is_java_native`
  helper roots a `native` method `callback` (the Java analogue of the Go cgo `//export` / C#
  `[UnmanagedCallersOnly]` rooting shipped in v2.1.9).

Each fix is cardinal-safe: a plain function with no such attribute/modifier still flags dead
(asserted in the regression suite — `truly_dead` C static, plain Rust private fn, non-native Java
private method).

## Compatibility

No API or schema change; indexes rebuild cleanly.

## Quality gate

Full suite (incl. three per-language regressions with dead-stays-dead assertions + a
`_is_rust_runtime_entry_attr` helper unit test) + ruff + mypy clean; differential oracle suite green;
mutation meta-oracle over `_is_rust_runtime_entry_attr` / `_is_java_native` / `_c_attr_roots` (all
mutants killed); two-round full-diversity multi-model adversarial review.
