# stitchgraph v2.1.17 — Ruby `&:symbol` / `enum_for` / `&method(:m)` symbol dispatch

A cardinal fix from the Ruby dogfood pass: Ruby names a method via a **literal symbol** in idioms the
name-based call graph can't see, so the named method (and its callees) was confidently flagged dead.

## The bug

- **`xs.map(&:upcase)`** — `Symbol#to_proc` turns `:upcase` into a block that calls `upcase` on each
  element. No textual call to `upcase` exists.
- **`enum_for(:m, …)` / `to_enum(:m)`** — wrap method `m` as a lazy enumerator, invoked when the
  enumerator is iterated.
- **`method(:m)` / `instance_method(:m)`** — a (bound/unbound) `Method` object for `m`, commonly
  invoked as `&method(:m)`.

A value class with `def upcase`/`def valid?` used only via `tokens.map(&:upcase)` /
`tokens.select(&:valid?)`, or a generator method exposed via `enum_for(:filter_tokens)`, had those
methods flagged dead at confidence ≥ 0.5.

## The fix

New `_ruby_symbol_refs` pass (mirroring the Bash callback-arg pass) collects each literal symbol from
`&:sym` block arguments and from the first symbol argument of `enum_for`/`to_enum`/`method`/
`instance_method` calls, and routes the name through `_ref` — so it is rooted **only if it resolves to
a project method** (cardinal-safe). Ruby method-name suffixes are handled (`:valid?`, `:save!`,
`:name=`). `send`/`public_send` are deliberately **not** covered — they remain the documented
dynamic-dispatch limitation. A genuinely-dead method still flags (asserted in the regression).

## Compatibility

No API or schema change; indexes rebuild cleanly. Precision-over-recall trade (cardinal-safe): a
genuinely-dead method whose name coincides with a symbol used elsewhere as `&:name` is masked.

## Quality gate

Full suite (incl. an end-to-end regression for `&:symbol`/`enum_for` + dead-stays-dead, and a
`_ruby_symbol_refs` parser unit test incl. the `send`/`public_send` exclusion) + ruff + mypy clean;
differential oracle suite green; mutation meta-oracle over the new helpers (all mutants killed);
two-round full-diversity multi-model adversarial review.
