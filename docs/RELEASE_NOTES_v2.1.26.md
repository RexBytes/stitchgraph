# stitchgraph v2.1.26 — JS/TS implicit-dispatch class members (#54)

The final cardinal in the campaign sweep: a class member the runtime invokes implicitly is no longer
flagged dead.

## The bug

```ts
function emit() { /* ... */ }

class Bag {                         // NOT exported
  items: number[] = [];
  [Symbol.iterator]() { return emit(this.items); }   // invoked by `for…of`, never by name
}

export function make() { const b = new Bag(); for (const x of b) {} return b; }
```

`[Symbol.iterator]` is live — the `for…of` loop invokes it — and `emit` is live through it. Both were
confidently flagged dead. The same held for:

- other well-known Symbols — `[Symbol.asyncIterator]`, `[Symbol.toPrimitive]`, `[Symbol.hasInstance]`,
  `[Symbol.toStringTag]` — invoked by spread / coercion / `instanceof` / `Object.prototype.toString`;
- `get`/`set` **accessors** — invoked by a property read/write (`b.value` / `b.value = …`), which the
  graph models as a member access, not a call;
- serialization/coercion hooks by name — `toJSON` (`JSON.stringify`), `toString` / `valueOf` (string
  & numeric coercion, template literals, `String(x)`).

None is ever reached by a plain `obj.method()` by-name call. Exported-class members were already
rescued by `_seed_exported_class_methods`, so the gap surfaced on non-exported (but instantiated)
classes.

## The fix

A new `_is_js_implicit_dispatch_method` recognizes the three forms — a `computed_property_name`
containing `Symbol.`, a `get`/`set` accessor, or one of the names `toJSON` / `toString` / `valueOf` —
and the JS/TS method-extraction path roots them `callback`.

- **Language-gated** to javascript / typescript / tsx (a Python method named `toJSON` is not a JS
  runtime hook and stays dead-eligible).
- **Cardinal-safe over-approximation:** rooting only adds a root, so a genuinely-unused accessor or
  hook is over-rooted (bounded — one per member), but live code is never flagged dead.
- A plain by-name method that is genuinely uncalled still flags dead (verified).

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Known limitations

A general (non-`Symbol`) computed-key class method (`[CONFIG_KEY]() {}`) is a separate dynamic-
dispatch concern (#78), out of this change's scope. The remaining cardinal-safe precision/coverage
follow-ups (#70–#89) stay deferred.

## Quality gate

Full suite — 521 tests (well-known Symbol methods, get/set accessors, toJSON/toString/valueOf,
each keeping its private callee live; plus guards that a plain uncalled method still flags and the
rooting is JS/TS-only) + ruff + mypy clean; differential oracle suite (27) green; mutation
meta-oracle on `_is_js_implicit_dispatch_method` (7/7 killed, incl. a direct unit test pinning the
Symbol-specific match); two-round full-diversity multi-model adversarial review — no in-scope
cardinal, no crash.
