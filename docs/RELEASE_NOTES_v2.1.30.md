# stitchgraph v2.1.30 — C/C++ struct used only as a type (#89)

A struct/union/enum used only as a **type** is no longer flagged dead.

## The bug

```c
struct Config { int port; };     /* definition */
struct Config g_config;          /* used only as a variable type */
int get_port(void) { return g_config.port; }
int main(void) { return get_port(); }
```

`Config` is live — it is the type of a global that live code reads — but C/C++ has no constructor
call to edge a plain struct type, so `Config` had no inbound edge and was confidently flagged dead.
The same held for a struct used only as a parameter type (`void f(struct Config *p)`), a field type,
or a return type.

## The fix

A new `_c_type_ref_names` collects the names of **bodyless** (type-use) `struct` / `union` / `enum` /
`class` specifiers — the *definition* carries a `body` field and is skipped, so only a type *use* is
collected — and the post-pass roots every matching C/C++ class node `callback`.

- **Project-wide** (a type is routinely defined in a header and used in a `.c`), scoped to C/C++.
- **Cardinal-safe over-approximation:** a struct that is genuinely never used as a type and never
  instantiated still flags dead (verified). A homonym only over-roots.

## Resolved without a code change

- **#88** — the C/C++ module-level walk treats a `#define` parameter name as a reference. This is
  *over-rooting* (the cardinal-SAFE direction); tightening it would un-mask and risk flagging a live
  macro-referenced symbol dead, so it is a deliberate precision boundary.
- **#87** — enum-constant-body overrides are already handled: a Java enum constant with a method
  override and the helpers it calls stay live (no reproduction). The companion class-scope anon-class
  over-rooting is the cardinal-SAFE direction, left intentionally.

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Quality gate

Full suite — 558 tests (struct used as variable / parameter / return type across `.c` and `.cpp`;
an unused struct still flags; a `_c_type_ref_names` unit-pin) + ruff + mypy clean; differential
oracle suite (27) green; mutation meta-oracle on `_c_type_ref_names` (4/4 killed). Two-round
full-diversity multi-model adversarial review — no in-scope cardinal, no crash.
