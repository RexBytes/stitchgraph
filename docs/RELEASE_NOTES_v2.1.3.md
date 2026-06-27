# stitchgraph v2.1.3 — Rust FFI/linker-export cardinal fix (doc-driven)

Found by **doc-driven hunting**: reading a language's reference enumerates its *complete*
implicit-invocation surface (magic methods, operator overloads, FFI exports, reflection hooks) —
including mechanisms a scanned repo only surfaces if it happens to exercise them. The Rust
reference's treatment of the `no_mangle` / `export_name` attributes exposed a gap no repo in the
cross-language hunt had hit.

## The bug

stitchgraph already roots `pub fn` as `exported` (public ABI, no in-tree caller expected). But a
function can export its symbol *without* being `pub`:

```rust
#[no_mangle]
extern "C" fn rust_entry() {   // no `pub` — still a valid, exported FFI symbol
    only_from_entry();
}
```

`#[no_mangle]` (and `#[export_name = "…"]`) export the symbol to the linker / foreign code
regardless of visibility. Without `pub`, the export-rooting never fired, so `rust_entry` — and
everything its body reached (`only_from_entry`) — was false-flagged **dead**. A minimal fixture
confirmed it: `rust_entry dead? True` (CARDINAL false-positive). This is the Rust analogue of C's
`EXPORT_SYMBOL`: the symbol is a public entry point with no caller inside the crate.

## The fix

The extractor now recognises `#[no_mangle]` and `#[export_name]` on a function and roots it
`exported`, exactly as it already does for `pub`. A `_is_rust_export_attr(attr_text)` helper
isolates the attribute test (parses the attribute path out of `#[ … ]`, matches `no_mangle` /
`export_name`), so it is unit-tested and mutation-pinned.

Cardinal-safe: it only *adds* a root. After the fix the fixture's `rust_entry`, a renamed
`#[export_name]` fn, and the body-only-reachable `only_from_entry` are all live, while genuinely
unreferenced functions remain correctly dead.

## Why doc-driven found what repos didn't

The Rust/Go/Ruby hunt (v2.1.1) confirmed Rust *`pub`* exports were handled — but real crates that
use `#[no_mangle]` overwhelmingly pair it with `pub` (cdylib convention), so the non-`pub` path
was never exercised. The reference documents the attribute independent of visibility; a minimal
fixture isolates exactly that combination, so the gap can't be masked by incidental repo style.

## Compatibility

No API or schema change; indexes rebuild cleanly. Rust indexes now root `#[no_mangle]` /
`#[export_name]` functions as entry points regardless of `pub`.

## Quality gate

Full suite (incl. a new Rust export regression test + helper test) + ruff + mypy clean; all
differential oracles green; mutation meta-oracle over the new helper (all mutants killed);
two-round full-diversity multi-model adversarial review.
