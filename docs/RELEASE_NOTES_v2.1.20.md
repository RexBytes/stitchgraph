# stitchgraph v2.1.20 — JS/TS object & class literals in expression positions (#75)

The cardinal fix that closes the last broad object/class extraction gap on the JS/TS line
(after v2.1.11 object-member shorthand, v2.1.18 `_object_members`, v2.1.19 `const X = class {…}`):
an object or class literal reached only through an **expression shape** now has its members
extracted, rooted, and their bodies walked.

## The bug

```ts
function helper() { /* ... */ }

// every one of these used to flag `helper` dead:
register({ onInit() { helper(); } });                 // call argument
export const C = Object.freeze({ run() { helper(); } });   // call argument
export const C = cond ? { run() { helper(); } } : null;    // ternary
export const C = opts || { run() { helper(); } };          // logical
export const C = [ { run() { helper(); } } ];              // array element
export const routes = m.exports = { run() { helper(); } }; // chained assignment
export const C = (() => ({ run() { helper(); } }))();      // IIFE return
```

`helper` is live — it is reachable through the object member `run`/`onInit` — but it was flagged
dead. Two distinct failures fed the same cardinal:

- A `variable_declarator` whose value was an expression shape (`const C = f({…})`, `[ {…} ]`,
  `a ? {…} : b`, …) was **swallowed**: the declarator branch handled only arrow/function/object/
  class *values*, with no `else`, so the wrapping call/array/ternary was never descended and the
  inner literal never reached.
- A bare-statement form (`register({…})`) *was* descended generically — and generic descent minted
  the object's `method_definition`s as **unrooted, module-scope nodes**, so the live method itself
  was flagged dead (the "round-11" cardinal).

## The fix

- **Expression-position object literals** are routed through `_object_members` (the same pass that
  backs `const obj = {…}` and `module.exports = {…}`) wherever generic descent reaches one. Members
  at module scope take the `callback` role (the dispatch-table idiom — over-rooting here is the
  precision-over-recall, cardinal-safe direction); members nested in a function body stay
  reachability-gated via a CONTAINS edge. A position-synthesized qual (`<obj@line_col>`) keeps an
  anonymous object's members from colliding with a same-named real module function.
- **Anonymous / expression-position class literals** (`reg(class {…})`, `[ class {…} ]`) are modeled
  as CLASS nodes with INHERITS edges and their bodies walked, mirroring the `const X = class {…}`
  (#80) and `obj.X = class {…}` paths. At module scope the class takes the `exported` role so its
  public methods are rescued; nested in a function it is reachability-gated and its methods gated to
  the class. A `body`-field guard skips the bare `class` **keyword token** (also typed `class`)
  inside a regular `class X {}`, so ordinary class declarations are completely unaffected.
- The `variable_declarator` branch now **descends** into non-arrow/object/class values instead of
  swallowing them — made safe by the interception above, which roots any literal it finds rather
  than letting raw descent mint unrooted methods.

## Compatibility

No API or schema change; indexes rebuild cleanly. Cardinal-safe throughout: the change only ever
adds nodes/roots for previously-invisible members. A genuinely-dead top-level `class X {}` /
`function f(){}` still flags (verified by regression). The differential streaming oracle confirms
streaming output stays byte-identical to the in-memory graph.

## Known limitations (unchanged, deferred)

`this.#m()` private dispatch (#76), `#private`/computed-key methods inside a class body dropped by
`_name_of` (#78), and bare-identifier function reassignment (`g = function(){…}`, #81) remain
pre-existing and deferred. Private-method dead-eligibility on *anonymous* expression-position
classes is cardinal-safe (over-rooting only).

## Quality gate

Full suite — 487 tests (11 expression-position shapes parametrized + anonymous-class liveness +
over-rooting guard that a regular dead class/function still flags) + ruff + mypy clean; differential
oracle suite (27) green; mutation meta-oracle on `_collect` — the cardinal-relevant interception
site killed; two-round full-diversity multi-model adversarial review — no in-scope cardinal, no
crash.
