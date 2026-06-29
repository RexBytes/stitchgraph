# stitchgraph v2.1.15 — C++ range-based-`for` `begin()`/`end()` customization points

A cardinal fix from the C++ doc-driven manual pass: a range-based `for (x : r)` loop is desugared by
the compiler to `r.begin()` / `r.end()` (or ADL `begin(r)`/`end(r)`), so the name-based call graph
never sees those calls. An iterable type's `begin`/`end` methods — and whatever their bodies reach —
were confidently flagged dead.

## The bug

`_IMPLICIT_HOOKS` rooted the implicit-invocation protocol for Ruby/PHP/Java but had no C++ entry, and
the C++ special-member pass only covers `operator…`/destructors. So a custom iterable:

```cpp
struct Range {
    int* begin() { return a; }   // flagged dead
    int* end()   { return a + n; }  // flagged dead
};
for (int x : r) ...   // compiler calls r.begin()/r.end() — no textual call site
```

had `begin`/`end` (and any private helper they alone reach) flagged dead at confidence ≥ 0.5.

## The fix

Add a `"cpp"` entry to `_IMPLICIT_HOOKS` rooting `begin`/`end` as `callback`. A class defining
`begin`/`end` is iterable by design, so rooting them is semantically correct; cardinal-safe
over-rooting otherwise (only adds roots). Confirmed on the range-for desugaring; a plain method with
no caller (`Range::truly_dead`) still flags dead.

Scope: `.cpp`/`.cc`/`.cxx`/`.hpp` files (raw language `cpp`), **and** any `.h` that `_header_lang`
content-sniffs as C++ (it carries `class`/`namespace`/`template`/`virtual`/… markers) — so a
header-only C++ class in a `.h` is also covered. Only a pure-C `.h` (no C++ markers) is parsed as C,
and a `begin`/`end` there stays dead-eligible (no C over-rooting).

## Compatibility

No API or schema change; indexes rebuild cleanly. Precision-over-recall trade (documented,
cardinal-safe): a genuinely-dead method named `begin`/`end` in a C++ file is now masked.

## Quality gate

Full suite (incl. a regression asserting range-for `begin`/`end` + their callee live and a genuinely
dead method still flagged) + ruff + mypy clean; differential oracle suite green; two-round
full-diversity multi-model adversarial review.
