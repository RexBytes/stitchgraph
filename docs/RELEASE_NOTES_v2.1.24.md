# stitchgraph v2.1.24 — C/C++ function called only inside a `#define` macro body (#59)

A cardinal fix for C/C++: a function reached only through a preprocessor macro is no longer flagged
dead.

## The bug

```c
static void log_impl(const char *msg) { /* ... */ }
#define LOG(msg) log_impl(msg)          // body is raw preprocessor text

int main(void) { LOG("hi"); return 0; } // the only "call" to log_impl
```

`log_impl` is live — it runs every time `LOG` expands — but it was confidently flagged dead. The
same held for function-pointer macros (`#define DEFAULT handler`) and helper-wrapping macros
(`#define BOTH() (a() + b())`).

Tree-sitter parses a macro body as a single raw-text `preproc_arg` node — it does **not** parse the
`log_impl(msg)` inside as a call expression. So the AST call scan never sees the call, and the
function loses its only caller.

## The fix

A new text-scan, `_macro_body_ref_names`, collects every identifier that appears in C/C++ `#define`
bodies. Any project C/C++ function or method whose name matches is rooted `callback` — it is invoked
indirectly wherever the macro expands. This is the direct analogue of the existing `EXPORT_SYMBOL`
text-scan, which already handles a construct the grammar doesn't model as a call.

- **Project-wide** across the unified C/C++ resolution bucket: a header macro routinely wraps a
  function defined in a separate `.c`, so the scan must match across files.
- **Byte-gated** to files that actually contain `#define`, so the walk costs nothing on the common
  case.
- **Cardinal-safe over-approximation:** matching resolves by name to F/M nodes only, so a macro
  parameter or a keyword that happens to share a name merely over-roots (keeps a genuinely-dead
  function live) — it can never flag live code dead.
- A function whose name appears in **no** macro body still flags dead; numeric/string macros
  (`#define MAX 100`) yield no identifiers and root nothing.

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Known limitations

A function over-rooted because its name happens to appear in an **unused** macro body is a bounded,
cardinal-safe precision cost (the same precision-over-recall direction as `EXPORT_SYMBOL`). The C
cross-TU global function-table promotion (#69) and the JS/TS implicit-dispatch surface (#54) remain
pre-existing and deferred to their own per-language reviews.

## Quality gate

Full suite — 509 tests (function-like / object-like / multi-helper / C++ macro bodies, cross-file
header→`.c`, plus an over-rooting guard that a dead function not named in any macro still flags and
numeric macros don't crash) + ruff + mypy clean; differential oracle suite (27) green; mutation
meta-oracle on `_macro_body_ref_names` (2/2 killed); two-round full-diversity multi-model adversarial
review — no in-scope cardinal, no crash.
