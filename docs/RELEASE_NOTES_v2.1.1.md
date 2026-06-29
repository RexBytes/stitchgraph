# stitchgraph v2.1.1 — Ruby operator-method cardinal fix

Found by **dogfooding across a Rust / Go / Ruby hunt** (serde, clap, gorm, cobra, gin, logrus,
grape). Go and Rust came back **clean** — zero cardinal false-positives; serde's library is
fully live (only test-suite / trybuild fixtures flag). **Ruby surfaced a real one.**

## The bug

In grape, `Grape::Util::Lazy::ValueArray#initialize` was flagged dead — even though the class
*is* instantiated (`ValueArray.new(value)`). Its sibling `ValueHash#initialize` was live,
despite identical structure. That asymmetry pointed at the root cause:

**Ruby operator methods were being dropped from the graph entirely.** `def []`, `def []=`,
`def <=>`, `def ==`, `def <<`, … have a name node of tree-sitter type `operator`, which the
extractor's name resolver didn't recognize — so the whole method was silently skipped. Two
consequences:

1. The operator method itself was **un-navigable / un-analyzable** (not a node).
2. Anything used **only inside** an operator method's body was **false-flagged dead** — the
   `ValueArray.new(value)` that constructs `ValueArray` lives inside `def []=`, so with `[]=`
   invisible, `ValueArray`'s constructor had no live construction site. (`ValueHash` survived
   only by a *coincidental* mis-resolved edge — pure luck.)

## The fix

- **Capture operator method names** — `operator` joins the leaf name-node types `_trailing_id`
  recognizes (alongside C++'s `operator_name`/`destructor_name`).
- **Root them** — operator methods are invoked through operator/index **syntax** (`a[k]`,
  `a[k] = v`, `a <=> b`, `sort`, `a + b`), never a by-name call the call-graph sees, so they're
  marked `callback` (the Ruby analogue of the existing C++ special-member pass). Cardinal-safe:
  only adds roots.

On grape: `ValueArray#initialize` is now live, `ValueEnumerable#[]` and `#[]=` are captured and
live (+18 operator-method nodes), and `find_stale` dropped from 23 to 19 (the false-positives
gone; the rest are genuine dynamic-dispatch / registry candidates).

## Hunt scorecard

| Project | files | find_stale | cardinal false-positives |
|---|---|---|---|
| Rust: serde | 171 | 24 (all test-suite / trybuild fixtures) | 0 in library |
| Go: gin | 92 | **0** | 0 |
| Ruby: grape | 283 | 23 → **19** after fix | **1 fixed** (operator-method class) |

## Compatibility

- No API or schema change; indexes rebuild cleanly. Ruby indexes now contain operator-method
  nodes that were previously absent (strictly more complete).

## Quality gate

Full suite (incl. a new Ruby operator-method regression test) + ruff + mypy clean; all
differential oracles green (the polyglot streaming oracle covers Ruby); mutation meta-oracle
over the changed extraction; multi-model adversarial review.
