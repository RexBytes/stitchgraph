# stitchgraph v2.1.9 — runtime / native (FFI) entry-point directives (Rust, C#, Go)

Doc-driven hunt into each language's *runtime-entry* surface — the functions a runtime or native
caller invokes automatically. Each candidate was confirmed a cardinal false-positive with a minimal
fixture before fixing (and the already-covered forms were left alone — see below).

## The bugs (all confirmed cardinal, flagged dead at 0.6)

- **Rust** `#[panic_handler]`, `#[start]`, `#[alloc_error_handler]` — invoked by the runtime (on
  panic, as the entry point, on allocation failure). Unlike `#[no_mangle]` (handled in v2.1.3) or a
  `pub fn`, these need not be public, so nothing rooted them; the function and its callees were
  flagged dead.
- **C#** `[UnmanagedCallersOnly]` — a method exported to native (C-ABI) callers, invoked from
  unmanaged code and typically non-public, so `_has_public` didn't root it.
- **Go** lowercase cgo `//export name` — a function callable from C. A *capitalised* name is already
  live via Go's capitalised-export rule, but a lowercase `//export lower_entry` had no other root.

## The fixes (all cardinal-safe — only add roots)

- `_is_rust_runtime_entry_attr` recognises the three Rust attributes (path-matched, covering the
  `unsafe(...)`/`cfg_attr(...)` wrappers, never a `#[doc="…start…"]` substring); the function is
  rooted `callback`.
- `[UnmanagedCallersOnly]` (and `[JSInvokable]` for non-public Blazor methods) added to the curated
  C# callback-attribute set.
- `_go_has_export_directive` roots a Go function whose immediately-preceding `comment` sibling is
  `//export <thisname>`.

## What was deliberately *not* changed (already covered)

Probed and confirmed already-live, so no code added (the JS `export *` lesson — don't "fix"
non-issues): Rust `#[proc_macro]` / `#[proc_macro_derive]` / `#[proc_macro_attribute]` (require
`pub`), C# `[JSInvokable]` on a public method, Go capitalised `//export`, and Rust
`#[global_allocator]` (applied to a `static`, which isn't extracted as a node, so never flagged).

## Compatibility

No API or schema change; indexes rebuild cleanly.

## Quality gate

Full suite (incl. Rust/C#/Go regression tests + two helper tests with name-mismatch and prev-sibling
guards) + ruff + mypy clean; differential oracles green; mutation meta-oracle over both new helpers
(all mutants killed); two-round full-diversity multi-model adversarial review.
