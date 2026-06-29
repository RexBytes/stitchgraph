# stitchgraph v2.0.1 — PHP callable-string precision fix

A precision patch found by **dogfooding v2.0.0 on Magento**. Running `find_stale` on the
Magento Framework (`lib/`, 3,968 PHP files) surfaced a **cardinal-class false-positive**: live
methods flagged dead because PHP invokes them through *string* callables the syntactic call
scan couldn't see.

```php
usort($rows, [$this, 'compareRows']);                 // compareRows was flagged dead
uasort($this->routers, [$this, 'compareRoutersSortOrder']);
preg_replace_callback($p, [$this, '_convertEntities'], $s);
```

## What changed

- **The tree-sitter PHP extractor now recognizes array callables** and emits a REFERENCES edge
  to the named method, so the target isn't false-flagged dead: a 2-element callable array
  `[$this, 'method']` / `[self::class, 'method']` / `[static::class, 'method']` /
  `['Class', 'method']` / `[$obj, 'method']` → the **method**.
- **Cardinal-safe by construction.** Only project symbols resolve (via the same `_ref` path as
  every other by-name reference), so a non-callable 2-element array that merely happens to name
  a method over-roots — masking dead code — and can *never* produce a false-dead.
- **`'Class::method'` string callables need no special handling** — a string static call
  requires a `public` target, which is already rooted as exported.
- **Byte-identity preserved.** The new edges flow through the shared extractor path, so
  `reindex(streaming=True)` stays byte-identical to `streaming=False` (the differential oracle
  is green).

## Impact (measured on the Magento Framework)

| | PHP dead-code candidates (excl. test fixtures) |
|---|---|
| v2.0.0 | 39 (≥9 were `[$this, 'method']` callback false-positives) |
| v2.0.1 | **30** — the callback false-positives are gone; genuinely-unused private methods still flagged |

## Compatibility

- No API or schema change. Indexes rebuild cleanly; no migration.
- Same constant-memory streaming indexer as v2.0.0 — this only widens PHP liveness coverage.

## Quality gate

Full test suite (incl. a new PHP-callable regression test) + ruff + mypy clean; the streaming
differential oracle (byte-identical, incl. `name_based`/`weight`/`provenance`) green; the
mutation meta-oracle covers the new callable-string code; multi-model adversarial review.
