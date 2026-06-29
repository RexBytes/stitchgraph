# stitchgraph v2.1.27 — JS/TS shorthand member of an exported object (#74)

A function referenced only via object-literal **shorthand** in an exported object is no longer
flagged dead.

## The bug

```js
function onClick() { return clickHelper(); }
function onHover() { return hoverHelper(); }
function clickHelper() { /* … */ }
function hoverHelper() { /* … */ }

export const handlers = { onClick, onHover };   // shorthand — public API
```

`handlers` is exported, so `handlers.onClick` is reachable by any importer — `onClick` is live, and
`clickHelper` is live through it. But `{ onClick, onHover }` is shorthand: each member is a
`shorthand_property_identifier`, a form the call graph never models as a reference. So all four
functions were confidently flagged dead.

The CJS/default-export forms were already handled — `module.exports = { onClick }` and
`export default { onClick }` both root their members. The gap was specifically the
**named-const-export** form, `export const X = { … }`.

## The fix

`_reexport_names` now also walks an exported declaration's object-literal value
(`export const/let/var X = { … }`) and collects its member names — shorthand identifiers and `pair`
value identifiers (`{ run: doRun }` → `doRun`) — feeding them into the same reexport→`exported`
rooting path the CJS/default forms already used.

- **Cardinal-safe:** rooting only adds a root. A shorthand member of a NON-exported object still
  flags dead (verified).
- **Language-gated** to JS/TS via the existing reexport role-application guard (a same-named symbol
  in another language is not marked exported by a JS file's export).

## Resolved without a code change (same cluster)

- **#77** — `obj._x = fn` underscore member-assignment isn't rooted like the object-literal path. A
  statically-named underscore member that is actually called resolves by name (verified), so the
  member-assignment underscore gate is a deliberate, cardinal-safe precision boundary — not a defect.
- **#81 / #83** — bare-identifier reassignment (`g = function(){…}`) and a bare function/arrow
  expression in expression position. Already covered for exported modules: module-scope
  `_module_uses` and pass-2 def-body recursion root these. The no-export-module case is the same
  library-detection boundary as any unreferenced top-level function.

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Quality gate

Full suite — 539 tests (named const/let/var object exports, default-export object, `pair` value
members, plus guards that a non-exported object's shorthand still flags and an uninitialized
`export let x;` doesn't crash or silently drop JS extraction) + ruff + mypy clean; differential
oracle suite (27) green; mutation meta-oracle on `_reexport_names` (the new object-collection
branch pinned; two surviving mutants are pre-existing None-guard relaxations in untouched code).
Two-round full-diversity multi-model adversarial review — no in-scope cardinal, no crash.
