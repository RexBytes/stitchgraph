# stitchgraph v2.1.8 — recall: PHP bare-string function callables

The last queued **non-cardinal** item from the `LIMITATIONS.md` audit.

## What changed

**PHP bare-string function callables.** A project global function passed by plain string name to a
callback-taking builtin — `usort($x, 'topcmp')`, `call_user_func('handler')`, `array_map('mapper',
$xs)`, `preg_replace_callback(…, 'cb')`, etc. — is reached at runtime, but the syntactic call scan
can't see the string, so the function surfaced as a stale candidate. It now emits a REFERENCES edge
to the named function, the bare-string analogue of the v2.0.1 array-callable form (`[$this,
'method']`).

Scoped to a curated set of callback-taking builtins (`_PHP_CALLBACK_BUILTINS`: usort/uasort/uksort,
call_user_func(_array), array_map/filter/walk/reduce, preg_replace_callback(_array),
register_shutdown_function, set_error_handler/set_exception_handler, spl_autoload_register, …) so an
ordinary string literal that merely matches a function name doesn't over-root. A `'Class::method'`
string needs no handling — a static string call requires a public target, which is already rooted.

## Docs correction (no code change)

The "obscure JS/TS/CJS export indirections" note previously listed `export * from './m'` as an
unrooted form. That is **incorrect**: `export *` re-exports symbols that `m` already `export`s
inline, so they are already rooted in `m` — a star re-export adds no false-dead. Verified with a
fixture (panel R86) and the note corrected. The genuinely-unrooted forms (`module.exports = X` inside
a function body, `Object.assign(module.exports, {…})`, `module.exports = ns.Member`) remain
documented.

## Compatibility

No API or schema change; indexes rebuild cleanly.

## Quality gate

Full suite (incl. a PHP bare-string regression test + helper test, with an over-match guard case) +
ruff + mypy clean; differential oracles green; mutation meta-oracle over `_php_string_callable_names`
(all mutants killed); two-round full-diversity multi-model adversarial review.
