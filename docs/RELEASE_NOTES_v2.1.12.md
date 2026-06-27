# stitchgraph v2.1.12 — Transitive framework-inheritance callback rooting (tree-sitter)

A single cardinal fix that clears the **same root cause across PHP, C#, Java, and C++** — found by
dogfooding Magento (PHP) and a doc-driven C# manual pass, and traced to a symmetry gap: the Python
extractor already did this, the tree-sitter extractor did not.

## The bug

`_seed_callback_roles` marks the methods of a *framework class* (a class that extends an external,
unindexed framework base) as `callback`, so framework-invoked overrides aren't false-flagged dead.
But the set of framework classes was built from the **direct** parent only:

```
ExternalFramework (unindexed)
   └── Base            (in-tree, direct external base — correctly marked framework)
         └── Child     (in-tree, extends Base — MISSED: parent is a project class)
```

When the framework calls `$this->handle()` polymorphically, `Child.handle` (a live override) got no
`callback` role and no in-tree caller, so it was confidently flagged dead at confidence ≥ 0.5 —
**cardinal**. Confirmed in real Magento 2.4.7 (`Shipment::_getValidationRulesBeforeSave`,
`Transaction\Collection::_renderFiltersBefore`) and on the C# shape where an explicit
`void IDisposable.Dispose()` is reached only via `using` through a *project* interface that extends
the framework `IDisposable`.

## The fix

New `_framework_classes` helper computes the framework-class set as (a) every class with a direct
external base **plus** (c) the transitive first-party descendants of those classes — a fixpoint
closure down the in-tree INHERITS tree. This mirrors exactly what the Python extractor's
`_apply_callback_roles` already did (cases (a) + (c)); the fix ports the missing transitive step to
the tree-sitter extractor, so PHP/C#/Java/C++ get the same protection Python had.

The change only ever **adds** roots — cardinal-safe by construction. A pure first-party inheritance
chain with no external base anywhere gets no framework rooting, so genuinely-dead overrides stay
flagged (asserted in the regression suite). Same-name self-loops (`class X extends pkg.X`) are not
treated as their own subclass.

## Compatibility

No API or schema change; indexes rebuild cleanly.

## Quality gate

Full suite (incl. PHP transitive, C# explicit-interface-via-project-chain, a no-over-mask
cardinal-safety boundary test, and a `_framework_classes` helper unit test) + ruff + mypy clean;
differential oracle suite green; mutation meta-oracle over `_framework_classes` (all mutants killed);
two-round full-diversity multi-model adversarial review.
