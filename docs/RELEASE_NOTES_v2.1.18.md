# stitchgraph v2.1.18 — JS/TS object-literal function-member bodies

A cardinal fix for the rxjs/lodash-style **config object** pattern: a top-level function called only
inside an object-literal member had its sole caller invisible to the graph, so it was confidently
flagged dead.

## The bug

The JS/TS extractor never traversed an object-literal *value*, so the function bodies inside it were
never walked. A top-level helper called only from such a body had no inbound edge:

```js
export const obj = {
  run() { return reach(); },          // method shorthand
  arrow: () => inner(),               // function-valued property
  nested: { onClick() { deep(); } },  // nested object
};
obj.run();
function reach() { /* ... */ }   // called only inside run()    -> flagged dead (cardinal)
function inner() { /* ... */ }   // called only inside arrow     -> flagged dead (cardinal)
function deep()  { /* ... */ }   // called only inside onClick    -> flagged dead (cardinal)
```

`reach`/`inner`/`deep` are live, but each was flagged dead at confidence ≥ 0.5. The same gap hit the
CommonJS `module.exports = { handler() {…} }` form. String-keyed members (`{ "do-it"() {…} }`) were
an even quieter variant: `_name_of` returns `None` for a string key, so the member was dropped
outright and its body never walked.

## The fix

New **`_object_members`** pass extracts the function-valued members of an object literal — method
shorthand, `arrow`/`function`/`function_expression`-valued properties, and members of nested object
values — as METHOD nodes, so pass 2 walks their bodies and the calls inside become visible. It is
wired into both the `variable_declarator` branch (`const obj = {…}`) and the `assignment_expression`
branch (`module.exports = {…}`, `Foo.prototype = {…}`).

A module-scope, non-underscore member is invoked dynamically/externally (passed as a callback, spread
into config, looked up by a computed key), never by a plain local name, so it takes the `callback`
role — mirroring the existing member-assignment precedent (`app.render = function(){…}`). A member
nested inside a function body stays reachability-gated via a CONTAINS edge (a dead initializer must
not mint live roots). An underscore-private member opts out of the root, matching the
member-assignment gate.

New **`_obj_key_name`** helper reads a member key's static name including STRING keys
(`{ "do-it"() {…} }`, `{ "k": fn }`) by taking the unquoted fragment — `_name_of` returns `None`
there, which would silently drop the member. Computed keys (`[Symbol.iterator]`) and number keys have
no static name and are left to their own concern.

## Hardening from the adversarial panel

The multi-model review surfaced four more cardinal-class gaps in the same surface, all now fixed
and regression-pinned:

- **Dynamically-dispatched underscore members.** Object literals are the canonical dispatch-table
  idiom (`handlers["_" + action]()`); an underscore member has no by-name call site. An earlier
  underscore opt-out minted an *unrooted* node that was then confidently flagged dead while live.
  Module-scope members are now rooted **unconditionally** (underscore and computed included).
- **Computed-key members** (`{ [k]: () => h() }`) are extracted under a synthesized id (from the
  key text) and rooted, so their bodies are walked.
- **TypeScript value wrappers** (`{…} as const`, `{…} satisfies T`, `({…})`) are peeled by
  `_unwrap_ts_value` so member extraction fires for the pervasive `export const obj = {…} as const`.
- **Functions defined inside a member body** (`run() { function inner(){…} }`) are now extracted via
  `_collect` recursion with a CONTAINS edge — pass 2 skips nested defs, so a helper such a nested
  fn alone calls was previously flagged dead. Members nested in a dead function stay gated.

## Compatibility

No API or schema change; indexes rebuild cleanly. Precision-over-recall trade (cardinal-safe): a
genuinely-dead, public, module-scope object-literal member is masked (rooted as a dynamic callback),
the same trade as the member-assignment handler. A genuinely-uncalled top-level function still flags.

## Quality gate

Full suite (incl. end-to-end regressions for method-shorthand, function-valued property,
nested-object, CommonJS `module.exports = {…}`, the non-exported arrow-property path, string-keyed
members, and the underscore-private gate — each asserting live-stays-live and dead-stays-dead) + ruff
+ mypy clean; differential oracle suite green; mutation meta-oracle over both new helpers
(`_object_members`, `_obj_key_name` — all mutants killed); two-round full-diversity multi-model
adversarial review.
