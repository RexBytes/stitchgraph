# stitchgraph v2.1.28 — TS class-member resolution cardinals (#76, #78)

Two ways a genuinely-live TS class method (and the private helpers it alone calls) was confidently
flagged dead.

## #76 — `#private` method via `this.#m()`

```ts
export class Svc {
  run() { return this.#impl(); }
  #impl() { return implHelper(); }   // implHelper was flagged dead
}
```

A `#private` name is a `private_property_identifier` node. Both `_name_of` (the def name) and
`_callee` (the `this.#impl()` call site) returned None for it, so:
- the `#impl` def was dropped → its body was never walked → `implHelper` flagged dead; and
- the `this.#impl()` call edge was lost.

**Fix:** `_trailing_id` now handles `private_property_identifier`, so both sides resolve to the same
`#impl` and the call edge connects. A `#private` method resolves by name, so an *uncalled* one (and
its private helper) still flags dead — precision preserved.

## #78 — string / computed / numeric-keyed class methods

```ts
class Bag {
  ["do it"]() { return strHelper(); }   // strHelper was flagged dead
  "plain"()   { return ... }            // string key — same drop
  42()        { return ... }            // numeric key — same drop
}
```

`_name_of` returned None for a string key, a computed-string key, or a numeric key, so the method
def was silently dropped and the helper it alone calls was flagged dead. (A computed *identifier*
key, `[NAME](){}`, was named but, in a non-exported class, never rooted.)

**Fix:** a JS/TS class method with a dynamic key (string / computed / numeric) is now modeled as a
node — named from the raw key text — with its body walked, and rooted `callback`. This is the
class-body analogue of the object-literal computed-key rule: such a method is reachable only via a
dynamic `obj["k"]()` / `obj[expr]()` subscript, never a static `.name` call. Cardinal-safe (only
adds a root); a plain by-name method that is genuinely uncalled still flags dead.

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Quality gate

Full suite — 551 tests (#private resolve + uncalled-#private-still-flags precision; string/computed/
numeric-keyed method body-walk across both exported and non-exported classes; plain-uncalled guard)
+ ruff + mypy clean; differential oracle suite (27) green; mutation meta-oracle on `_trailing_id`
(the `private_property_identifier` leaf pinned by the #76 tests). Two-round full-diversity
multi-model adversarial review — no in-scope cardinal, no crash.
