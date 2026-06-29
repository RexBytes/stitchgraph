# stitchgraph v2.1.5 — C/C++ header-declaration export-attribute cardinal fix

Produced by a **full audit of `LIMITATIONS.md`** (per the maintainer's direction: *fix a limitation
rather than document it*). The audit triaged every note into fixable / fundamental / intentional;
this release lands the one genuinely-**cardinal** fixable item — the C/C++ header-declaration
export-attribute gap surfaced by panel R77 (Finding 2) during the v2.1.4 review.

## The bug

v2.1.4 roots a C/C++ function when an entry-point/export attribute sits **on its definition**. But
the dominant C++ convention puts the export attribute on the **declaration** — in the header —
while the out-of-line definition in the `.cpp` carries none:

```cpp
// widget.h
struct Widget {
  __attribute__((visibility("default"))) int compute(int x);   // attribute here
};

// widget.cpp
int Widget::compute(int x) { return helper(x); }                // no attribute here
```

`Widget::compute` is public ABI (live), but its definition has no in-tree caller and no attribute,
so it — and `helper`, reachable only from it — was flagged dead at confidence 0.6 (a cardinal
false-positive, general to C++ libraries that mark their API in headers).

## The fix

`_c_export_decl_names` collects, **project-wide**, the names of functions/methods *declared*
(body-less) with an export attribute — handling the top-level qualified form (`int W::compute(int);`),
the free-function form, and the in-class member form. Those names root the matching definition,
wherever it lives. Project-wide is required because the declaration (header) and the definition
(`.cpp`) are in different files — the C/C++ analogue of Python's project-wide `__all__`.

Cardinal-safe: it only ever *adds* an `exported` root, so over-rooting a homonym masks dead code (the
safe direction) and never flags live code dead. `visibility("hidden")` declarations and unattributed
methods stay dead-code-eligible. The AST walk is byte-gated (`b"visibility"` / `b"dllexport"`), so it
is skipped on the vast majority of files.

## What the audit left documented (correctly)

- **Macro-wrapped attributes** (`#define EXPORT __attribute__((visibility("default")))`): unseeable
  without a preprocessor — tree-sitter does not expand macros. Genuinely unfixable; documented.
- **Fundamental items** (INFERRED vs EXTRACTED, ambiguous-call fan-out, declared-type method
  widening, advisory 0.6 confidence): these exist *because* there is no per-language type model;
  "fixing" them means building type inference, which trades the local-first design.
- **Intentional contract** (read-only on source, derived matrices, bounded `get_matrix`, no bundled
  embedder, `find_holes` = edit-orphaned): by design.
- **Remaining recall gaps** (Rust third-party test attributes, ByteBuddy/Moshi annotations, PHP
  bare-string callables, JS `export *`): non-cardinal under-reporting, queued as follow-on
  doc-driven releases.

## Compatibility

No API or schema change; indexes rebuild cleanly. C/C++ indexes now root a definition whose export
attribute lives on its header declaration.

## Quality gate

Full suite (incl. a new header-declaration regression test + helper test) + ruff + mypy clean; all
differential oracles green; mutation meta-oracle over the new helper (all mutants killed); two-round
full-diversity multi-model adversarial review.
