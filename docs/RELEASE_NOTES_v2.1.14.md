# stitchgraph v2.1.14 — Ruby implicit conversion / Enumerable protocol methods

A cardinal fix found by the doc-driven manual pass over the Ruby reference: the interpreter and
stdlib invoke a class's **conversion, Enumerable, Hash-key, and marshalling** methods *by name*, with
no textual call site — so a live class's protocol methods (and the helpers they alone reach) were
confidently flagged dead. The Ruby analogue of Python's dunder rooting, and an extension of the
existing Ruby implicit-hook set (`method_missing`/`inherited`/…).

## The bug

`_IMPLICIT_HOOKS["ruby"]` already rooted the metaprogramming hooks, but omitted the everyday
implicit-invocation protocol:

- **Conversion / coercion** — `to_s`/`inspect` (string interpolation, `puts`, `p`), `to_str`/`to_ary`/
  `to_hash`/`to_int`/`to_io`/`to_path` (implicit coercion), `to_a` (splat), `to_h` (double-splat),
  `to_proc` (`&obj` block conversion), `to_sym`, and numeric coercion `to_i`/`to_f`/`to_r` (note
  `Integer(obj)`/`Float(obj)` emit a call to `Integer`/`Float`, not to the object's hook, so the
  hook itself has no textual caller).
- **Enumerable** — `each` (driven by every `Enumerable` method: `map`/`select`/`reduce`/…), `each_pair`.
- **Hash-key / ordering** — `hash` and `eql?` (called by the interpreter when the object is used as a
  Hash key), `succ` (drives `Range#each`).
- **Marshalling** — `marshal_dump`/`marshal_load`/`_dump`/`_load` (invoked by `Marshal.dump`/`.load`).

A `class Money` with a `to_s` that calls a private `fmt`, used only via `puts money`, had both `to_s`
and `fmt` flagged dead; a `class Coll` driven via `.map` had its `each` flagged dead.

## The fix

Add the documented protocol names to `_IMPLICIT_HOOKS["ruby"]`, so a method with one of these names
is rooted `callback` (keeping it and its callees live). Only ever adds roots — cardinal-safe: a plain
method with no caller (e.g. `Money#really_dead`) still flags dead, asserted in the regression test.

## Compatibility

No API or schema change; indexes rebuild cleanly. As with every implicit-hook addition this is a
precision-over-recall trade: a genuinely-dead method named, say, `each` is now masked — the
documented, cardinal-safe direction.

## Quality gate

Full suite (incl. a regression asserting conversion/Enumerable hooks + callees live and a genuinely
dead method still flagged) + ruff + mypy clean; differential oracle suite green; two-round
full-diversity multi-model adversarial review.
