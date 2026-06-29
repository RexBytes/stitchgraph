# stitchgraph v2.1.2 — C# custom-attribute cardinal fix

Found by **continuing the cross-language dogfood hunt** into Java and C# (jackson-core, mockito,
okhttp, serilog).

## The bug

serilog defines and uses a custom attribute: `sealed class NoEnumerationAttribute : Attribute`,
applied as `[NoEnumeration]` on a `Guard.AgainstNull` parameter. stitchgraph flagged
`NoEnumerationAttribute` **dead** — because **C# applies attributes with the `Attribute` suffix
omitted**. The `[NoEnumeration]` usage reads as a reference to `NoEnumeration`, which never
matched the class `NoEnumerationAttribute`, so the attribute class had no resolved in-edge.

A custom in-tree attribute used *only* via `[Foo]` was therefore always false-flagged dead — a
cardinal-class false positive, general to every C# project with its own attributes.

## The fix

When the extractor walks a C# `attribute` usage, it now also emits the **suffixed** reference
(`Foo` → `FooAttribute`) alongside the bare name. The attribute class resolves and stays live.
Cardinal-safe: it only *adds* a reference, and the suffixed name resolves only if such a class
actually exists. On serilog, `NoEnumerationAttribute` is now live (resolved in-edge from
`Guard.AgainstNull`).

## Also: external framework annotations documented (not a code change)

Most of this round's findings were the **documented external-framework-annotation limitation**,
now with concrete examples in `LIMITATIONS.md`:

- **mockito** `MockMethodAdvice.enter`/`exit` — ByteBuddy `@Advice.OnMethodEnter`/`@OnMethodExit`,
  invoked by bytecode instrumentation.
- **okhttp** (slack sample) `urlToJson`/`urlFromJson` — Moshi `@ToJson`/`@FromJson`, invoked by
  reflection.

These are methods invoked by an *unrecognised* framework annotation. stitchgraph roots a curated
set of dominant-framework annotations; niche ones aren't covered (expanding the set is endless).
Pin them via `stitchgraph.toml [entry_points]`. (This is distinct from the C# fix above, which
is about the attribute *class* being referenced, not an annotated *method* being rooted.)

## Hunt scorecard

| Project | files | find_stale | assessment |
|---|---|---|---|
| Java: jackson-core | 358 | 13 | abstract-base protected methods (called by out-of-tree subclasses) + test helpers |
| Java: mockito | 982 | 39 | ByteBuddy `@Advice` (documented) + test fixtures |
| Java: okhttp | 155 | 3 | Moshi adapters in sample code (documented) |
| C#: serilog | 206 | 18 → **17** | **1 fixed** (custom attribute class); rest are reflection-convention tests |

## Compatibility

No API or schema change; indexes rebuild cleanly. C# indexes now link `[Foo]` attribute usages
to their `FooAttribute` class.

## Quality gate

Full suite (incl. a new C# attribute regression test) + ruff + mypy clean; all differential
oracles green; mutation meta-oracle over the changed extraction; multi-model adversarial review.
