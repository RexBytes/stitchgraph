# stitchgraph v3.6.0 — the body matrix learns Java and C#

v3.0.0 added the intra-procedural **body matrix** for Python; v3.2.0 ported it to the JavaScript
family, v3.3.0 to Go, v3.4.0 to Rust, v3.5.0 to C and C++. v3.6.0 adds **Java and C#** — languages 5
and 6 of the multi-language sweep (`docs/IDEAS.md` §5b), and the first release to land a *pair* in a
single MINOR (the two are close cousins, and the C/C++ round had already paid down the hard
value-flow lessons).

A new language for an existing representation earns the MINOR bump, but it is **backward-compatible**:
schema, on-disk indexes, and every existing operation are unchanged, and the new behavior is opt-in
and advisory.

## Added

### `core/structure_java.py` — one walker for Java
Emits the **same `_VFG` vocabulary** as the other frontends and reuses the WL kernel, so a Java clone
with renamed locals or reordered statements fingerprints as the *same shape*. Specifics:

- **Qualname = the dotted chain of enclosing TYPE names** (the `package` and imports are NOT part of
  the key): `Outer.compute`, nested `Outer.Inner.m`, interface default method `Shape.area`,
  constructor `C.C` — matching the extractor.
- Statement-oriented (explicit `return`). Compound assignment (`x += e` ≡ `x = x + e`), `?:`, casts
  (operand flows, the type carries none), `a[i]`, enhanced-`for` (`for (T x : it)`), `switch`
  statement **and** arrow `switch` expression, try-with-resources (the resource initializer flows),
  `synchronized`/labeled blocks, `throw`/`yield`.
- **Lambdas and anonymous classes are opaque `NESTED` leaves.**
- **Java has no free functions**, so a top-level method — which only appears in an error-tolerant
  parse of non-Java source — is intentionally not keyed (this also keeps the `find_similar` language
  sniff from grabbing a C/C++ free function as Java).

### `core/structure_csharp.py` — one walker for C#
Same `_VFG`, same kernel. Specifics:

- **Qualname = the dotted TYPE chain** (the `namespace` is NOT part of the key): `Calc.Compute`,
  constructor `Calc.Calc`, and **local functions** keyed `Calc.Local.Inner` — matching the extractor
  (which keys local functions as their own nodes).
- Call/index **arguments are unwrapped** from their `argument` wrapper nodes; `element_access` keeps
  its index under a `bracketed_argument_list`. Compound assignment, `?:`, casts, `foreach`, `switch`
  statement and `switch` expression, `using`/`lock`/`checked` blocks, `await`/`yield` carry flow.
- **Expression-bodied members** (`int M(int a) => helper(a);`) are walked as a return of the
  expression.
- **Lambdas and anonymous methods are opaque `NESTED` leaves.** Properties, operators, and destructors
  are not method nodes in the extractor, so they are not keyed (the body matrix keys exactly what the
  extractor keys, so `graph_diff` always finds the matching node id).

### `find_similar(mode="structure")` and `graph_diff` — now detect Java and C#
Auto-detects the snippet's language (Python → JS/TS family → Go → Rust → Java → C# → C/C++) and ranks
it **only against stored functions of the same language**; `graph_diff`'s body layer reports a diverged
Java/C# body present in both indexes. Same-language by construction (a node id maps to exactly one
file, hence one language).

## Scope & caveats

- **Advisory and read-only** — never feeds `find_stale`, so the cardinal rule (*live code is never
  confidently flagged dead*) is structurally unaffected.
- The Java/C# layer needs the optional **tree-sitter extra**; without it those paths return nothing
  (advisory degrade). The Python body matrix remains stdlib-only.
- **Cross-language body comparison stays oracle-only** — topology tracks the extractor; the features
  rank/diff within one language.
- The JS/TS grammar is permissive enough to also parse a bare `class { … }` snippet, so a
  class-*method* snippet handed to `find_similar(mode="structure")` may be sniffed as JS/TS. This
  affects only the advisory snippet auto-detect — never the extension-keyed `graph_diff` body layer,
  which maps each file to exactly one language.
- Same structural-approximation limits as the other frontends: no alias analysis, constants are
  collapsed, generics are fingerprinted as written. The method is in `docs/BODY_MATRIX_LESSONS.md`.

## Quality gate

- ruff + mypy clean; full suite **1021** passing; differential oracle suite **387**.
- Two new **body-matrix completeness oracles** — Java
  (`tests/oracles/test_structure_java_completeness.py`, 45 metamorphic cases + invariants) and C#
  (`tests/oracles/test_structure_csharp_completeness.py`, 43 metamorphic cases + invariants): a
  `helper()` (a CALL) vs `0` (a CONST) in every value-bearing statement and expression position must
  change the fingerprint, plus dedicated invariants (compound-assign rebind, cast carries operand not
  type, constructor keyed, nested-lambda opaque, Java nested-class/interface-default keying, C#
  namespace-not-in-key / local-function keyed / expression-bodied member walked).
- Mutation meta-oracle: `structure.py` 15/15, `graphdiff` 9/9, `similar.py` **53/61** — the new
  Java/C# fingerprint corpora are mutation-pinned by `graph_diff` body tests; the 8 survivors are
  justified-equivalent (`not sep or … is None` short-circuit guards, unreachable because every node id
  contains `::`, plus the `_cosine`/`_dot_cos` defensive guards).
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean.

## Upgrading

Nothing to do — no schema/API/behavior change to existing operations; indexes don't need
rebuilding. To try the Java / C# body matrix (with the tree-sitter extra installed):

```python
import stitchgraph as sg
with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")          # a Java / C# project
    print(sg.find_similar(store, open("Some.java").read(), mode="structure"))
    print(sg.graph_diff(store, "other_index.db"))   # body-aware, Java/C# + C/C++ + Rust + Go + JS + Python
```
