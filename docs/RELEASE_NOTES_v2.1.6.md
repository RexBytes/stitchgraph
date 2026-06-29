# stitchgraph v2.1.6 — C/C++ class-level export-attribute cardinal fix

The last cardinal item from the `LIMITATIONS.md` audit (R80 Finding 2), completing the C/C++
export-attribute story: definition attributes (v2.1.4), header-declaration attributes (v2.1.5), and
now class-level attributes.

## The bug

A C++ class can export its entire public interface with a single **class-level** attribute:

```cpp
// api.hpp
class __attribute__((visibility("default"))) Foo {   // attribute on the CLASS
public:
  int alpha(int x);     // public ABI — no per-method attribute
  int beta(int x);
private:
  int secret();         // not ABI
};

// api.cpp
int Foo::alpha(int x) { return helper(x); }           // no attribute here
```

`Foo::alpha`/`Foo::beta` are public ABI (live), but their out-of-line definitions carry no attribute
and have no in-tree caller, so they — and everything they reach — were flagged dead at confidence
0.6 (cardinal, general to C++ libraries that mark the class rather than each method).

## The fix

When `_c_export_decl_names` encounters a class/struct carrying a class-level export attribute, it
collects the public/protected method names in its body and roots their definitions project-wide by
name (the same mechanism as the v2.1.5 header-declaration fix). `struct` defaults to public, `class`
to private; an access label switches the section, so a `private:` method is not collected and stays
dead-code-eligible. Cardinal-safe: it only ever *adds* roots.

### Panel R81 broadened the coverage

The first review round found the collector handled only declared-only methods (`field_declaration`).
Added (panel R81):

- **Inline-defined methods** (`int m(int x){ … }` with the body in the class) parse as
  `function_definition`, not `field_declaration`, and were missed — a live inline public method was
  flagged dead at 0.6 (CARDINAL). Now collected.
- **Templated methods** (`template<class X> X m(X)`) parse as `template_declaration`; the function is
  descended into and collected.
- **`protected`** members are collected alongside `public`: for an exported class they are the
  extensibility ABI, reachable by out-of-tree subclasses. `private` stays internal/dead-eligible.

## Compatibility

No API or schema change; indexes rebuild cleanly. C++ indexes now root the public methods of a class
marked with a class-level `visibility("default")` / `dllexport` attribute.

## Quality gate

Full suite (incl. a class-level regression test + helper test) + ruff + mypy clean; all differential
oracles green; mutation meta-oracle over the new helper (all mutants killed); two-round full-diversity
multi-model adversarial review.
