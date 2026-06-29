# stitchgraph v2.1.19 — `const X = class {…}` class-expression declarator (#80)

A cardinal fix continuing the JS/TS object/class extraction hardening: a **class expression bound
to a `const`** was never modeled, so a helper called only from its methods was confidently flagged
dead.

## The bug

```ts
export class Base {}
function tok() { /* ... */ }
export const Widget = class extends Base {     // class expression bound to a const
  render() { return tok(); }                   // tok called ONLY here
};
// consumer: new Widget().render();
```

`tok` is live (reachable through `Widget.render`), but the `variable_declarator` branch of the
extractor handled `arrow`/`function`/`generator`/`object` values — **not** `class`/
`class_expression`. So `Widget` never became a node, its body was never walked, and `tok` lost its
only caller. (The sibling `assignment_expression` branch already handled `obj.X = class {…}`; this
closes the asymmetry.)

## The fix

The `variable_declarator` branch now models a `class`/`class_expression` value as a CLASS node,
mirroring the assignment-expression class handling:

- emits INHERITS edges for the heritage (`_bases`) — so `extends Base` / framework bases resolve;
- walks the class body so its methods (and their callees) are extracted;
- takes the `exported` role when the const is `export`ed, so `_seed_exported_class_methods` rescues
  its **public** methods (private methods stay dead-eligible — R46A);
- gates the methods to the class when the const-class is nested in a function (the round-3/4 rule:
  chain enclosing-fn → class → methods), so they are never orphaned;
- peels TS value wrappers (`class {…} as const`) via the existing `_unwrap_ts_value`.

Behaviour is now at **parity with a regular `class X {}`** declaration: exported + consumed →
public methods live; `extends` a base → base live; a genuinely-unused private method still flags.

## Compatibility

No API or schema change; indexes rebuild cleanly. Cardinal-safe: a fn-nested const-class
over-roots its genuinely-dead methods (precision loss only, never a false-dead on live code),
consistent with the object/assignment member treatment.

## Known limitations (unchanged, deferred)

The broad "object literal reached only via an EXPRESSION shape" family (#75 — IIFE / ternary /
`||`/`&&`/`??` / `Object.freeze` / array element / sequence / chained-or-parenthesized assignment),
`this.#m()` private dispatch (#76), and bare-identifier function reassignment (#81) remain
pre-existing and deferred to a focused follow-up. Decorating a `const` binding
(`@Dec const X = class{}`) is invalid TypeScript (parse error) and out of scope (#82).

## Quality gate

Full suite — 475 tests (exported cross-file consumption, private dead-eligible, `extends`
project + framework base, fn-scoped live/dead gating — each parity-checked against a regular
`class X {}`) + ruff + mypy clean; differential oracle suite (27) green; two-round full-diversity
multi-model adversarial review (R119–R120) — parity confirmed, no in-scope cardinal, no crash.
