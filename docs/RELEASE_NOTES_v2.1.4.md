# stitchgraph v2.1.4 — C/C++ attribute entry-point cardinal fix (doc-driven)

Found by **continuing the doc-driven hunt** that produced v2.1.3's Rust FFI-export fix — this time
against the GCC/Clang/MSVC function-attribute reference, which enumerates the attributes that make
a C/C++ symbol an implicit entry point or part of the public ABI.

## The bug

stitchgraph treats a C/C++ function as dead when nothing in-tree references it and it carries no
recognised entry signal. But several attributes make a function live with **no in-tree by-name
caller** — and none was recognised:

```c
__attribute__((constructor)) static void init_module(void) { setup(); }  // runs before main
static void setup(void) { ... }                                          // reached only from it

__attribute__((used))        static void keep_me(void) { ... }           // compiler must keep
__attribute__((visibility("default"))) void exported_api(void) { ... }   // public ABI
```

A minimal fixture confirmed the whole cluster — `init_module`, its destructor sibling, `keep_me`,
`exported_api`, **and every helper they reach** — was flagged dead at confidence 0.6 (≥ 0.5), a
cardinal-class false positive general to any C/C++ project using these idioms. The
`__attribute__((constructor))` case is the sharpest: that function is **guaranteed to execute** at
load time, yet was reported dead.

## The fix

A `_c_attr_roots(node, src)` helper inspects the attributes on a C/C++ function definition (the GNU
`__attribute__((…))` / `attribute_specifier`, the C++11 `[[…]]` / `attribute_declaration`, and the
MSVC `__declspec(…)` / `ms_declspec_modifier` — including the GNU *trailing* form that attaches to
the declarator) and roots the function:

- `constructor` / `destructor` → `callback` (runtime-invoked around `main`)
- `used` / `retain` → `callback` (explicitly kept; a use the compiler can't see by name)
- `visibility("default")` / `dllexport` → `exported` (public ABI)

Cardinal-safe: it only ever *adds* roots, so a broad match can over-root (mask dead code) but can
never flag live code dead. `visibility` is matched only for `"default"` — `"hidden"` is genuinely
internal and stays dead-code-eligible; plain uncalled statics stay dead.

## Why doc-driven found it

This is the C analogue of the Rust `#[no_mangle]` gap. As with Rust, real repos exercise only a
slice of the attribute surface and overwhelmingly pair these attributes with patterns that mask the
gap; the reference enumerates each mechanism directly, and a minimal fixture isolates it.

## Compatibility

No API or schema change; indexes rebuild cleanly. C/C++ indexes now root functions carrying these
entry-point / export attributes.

## Quality gate

Full suite (incl. new C, C++, and helper regression tests) + ruff + mypy clean; all differential
oracles green; mutation meta-oracle over the new helper (all mutants killed); two-round
full-diversity multi-model adversarial review.
