# Body-matrix lessons learned (Python v3.0.0) — and what transfers to other languages

> **Short answer to "will recording Python lessons help the other languages?": yes, substantially.**
> The hard-won Python work converts into three reusable assets — a **bug taxonomy** (the predictable
> failure modes), a **white-box oracle methodology** (find the whole class deterministically instead
> of one bug at a time), and a set of **design decisions** that port. What does *not* transfer is the
> per-language extraction code itself and the language semantics. This doc records the transferable
> part so each subsequent language (`docs/IDEAS.md` §5b) is cheaper and safer, the same way the
> `CONTRIBUTING.md` cardinal-hardening loop made the v2.1.x language sweep efficient.

## 1. The bug taxonomy (a per-language checklist)

v3.0.0's intra-procedural body matrix went through ~7 adversarial panel rounds. Every finding fell
into a small number of classes. For the *next* language, expect — and pre-empt — these:

| Class | What it looked like in Python | Pre-empt by |
|---|---|---|
| **Dropped node type** | `except*`, control-flow-nested defs, `match`, then `Subscript` index, `Dict` keys — value-bearing AST/CST nodes the builder didn't walk | the **completeness oracle** (§2) + a generic walk fallback |
| **Recursion depth** | a deep `a+a+…` chain (which the extractor indexes) overflowed the recursive walk → traceback | catch the recursion error and degrade; never raise from the advisory layer |
| **Qualname-scheme drift** | the fingerprinter's qualnames must match the extractor's node ids (control flow adds no level; classes/functions do) | one shared qualname rule; a differential test extractor-id ↔ fingerprint-key |
| **Identity-key collision** | body matching keyed by bare name collapsed same-named functions in different files | key by full node id (path + qualname + disambiguator) |
| **Operator/semantic blindness** | `a+b` ≡ `a-b`, `<` ≡ `>` because operators weren't in the node label | tag operators (and other semantically-load-bearing tokens) into the label |
| **Naming-convention noise** | `__init__` vs `constructor` across languages | normalise known equivalents to a canonical token (leaf mode) |
| **Empty/degenerate inputs** | stub bodies (`pass`/`...`) → zero-norm similarity → self-flagged "changed" | equality pre-check before similarity; clamp similarity to [0,1] |
| **Alien/hostile inputs** | a non-index sqlite file was opened and silently *migrated* (mutation); corrupt file raised | validate read-only before touching; refuse, never mutate or crash |

This table is the single most valuable transfer: it turns "discover the failure modes by adversarial
review" (expensive, ~7 rounds for Python) into "run the checklist" for languages 2–12.

## 2. The reusable method: white-box completeness oracles

The recurring "dropped node type" class was closed not by more panels but by a deterministic oracle
(`tests/oracles/test_structure_completeness.py`). It has two parts, **both of which port to any
language with an enumerable grammar** — and tree-sitter grammars *are* enumerable (the node-type set
is introspectable, just as Python's is via `ast.{stmt,expr}.__subclasses__()`):

1. **Metamorphic battery** — for every value-bearing node type, two source variants differing only
   by an inner "marker" (a call) vs a constant must produce *different* fingerprints. If a node type
   is silently dropped, the two collapse to identical → the oracle fails. (It caught two bugs —
   `Subscript` index, `Dict` keys — that seven panel rounds had missed.)
2. **Introspective grammar guard** — enumerate every concrete node type and assert each is
   classified (covered / leaf / opaque). When the grammar gains a node type (a new language
   version), the guard fails and forces coverage. The class cannot silently reopen.

**Porting cost is low:** the oracle harness is language-neutral; per language you supply (a) the
grammar's node-type enumeration and (b) a small battery of source snippets per node kind. The
"marker vs constant changes the fingerprint" property is identical everywhere.

## 3. Design decisions that transfer

- **Advisory / cardinal-safe boundary.** The body matrix never feeds `find_stale`; an imprecise
  fingerprint can only mis-rank a suggestion. This is what made it safe to ship an *approximation*.
  Keep this invariant per language — it's why we can move fast on a hard analysis.
- **Value-flow graph + WL-kernel fingerprint.** The target representation (operations + control
  points; data + control edges; copy propagation; order/name-invariant Weisfeiler-Lehman kernel) is
  language-neutral. The per-language work is *mapping constructs into it*, not redesigning it.
- **On-demand, not persisted.** Fingerprint from source at query time (scale: don't bloat the
  index). Same trade-off everywhere; same documented limitation (source must be readable at query
  time).
- **Layered/tagged matrix** (`docs/IDEAS.md` §5c) — call layer shipped for all 12 languages already;
  body layers tag in underneath. One schema, many depths.

## 4. What does NOT transfer (be honest)

- **The walker code.** Python uses the clean abstract `ast`; the other 11 use tree-sitter *concrete*
  syntax trees (every token, messier shapes). The construct→value-flow mapping is real per-language
  work, generally harder than Python.
- **Semantics.** Pointers/refs (C/C++/Rust), prototypes/`this` (JS), traits, macros, operator
  overloading — value-flow rules differ. The taxonomy and oracle *find* the gaps; the *fix* is
  language-specific.
- **Cross-language comparison.** Per the prior research finding (`research/README.md` §2), topology
  tracks the extractor, so comparing fingerprints *across* languages stays an oracle (located
  candidate, not proof), never an equivalence claim.

## 5. The per-language porting recipe

1. Enumerate the language's grammar node types; classify (value-bearing / leaf / opaque).
2. Stand up the completeness oracle for that language *first* (battery + introspective guard) — it
   will drive the walker, not the other way round.
3. Map constructs into the shared value-flow schema; make the oracle pass.
4. Run the bug-taxonomy checklist (§1) explicitly.
5. Dogfood on a real repo in that language; gate (ruff/mypy/tests/oracles/mutation) + adversarial
   panel — but the panel now hunts *novel* classes only, because the oracle owns the known ones.

This is the same shape as the cardinal-hardening loop in `CONTRIBUTING.md`, specialised for the body
matrix. Recording it is exactly what makes languages 2–12 incremental rather than each re-deriving
Python's seven rounds.

### Operational rules for `scripts/mutate.py` (learned the hard way)

- **Always point it at a TARGETED test subset, never the whole suite.** `mutate.py <file> -- pytest
  -x -q <the few test files that exercise <file>>`. It re-runs the command *once per mutant*; against
  the full 1000+-test suite a single file's mutation pass takes ~2 hours and blocks the loop. Targeted,
  it finishes in a couple of minutes. (The script's own header examples already do this — follow them.)
  For `similar.py` the right subset is `tests/test_similar.py tests/test_find_similar_structure.py
  tests/test_graph_diff.py`; for `structure.py`/`graphdiff.py` use the structure/graphdiff oracle +
  unit files.
- **Run it serially, by the orchestrator only — never inside an adversarial-panel reviewer, never
  concurrently with another `pytest`/`git add`.** It rewrites the target file in place (AST
  round-trip + one mutation at a time) and restores at the end; a concurrent `git add -A` or an
  interrupted run can commit a transient mutant (this happened once in the C/C++ round and flipped a
  `graphdiff` comparison). If a run is killed, immediately `git checkout HEAD -- <file>` to restore
  the canonical bytes before doing anything else.
- **A surviving mutant in newly-added code is usually a real coverage gap, not a false alarm** — add
  the missing test rather than rationalising it. (Adding the JS/Go/Rust/C++/Java/C# fingerprint
  functions to `similar.py` each needs a `graph_diff`/`find_similar` test that actually drives that
  language's `_*_fn_fingerprints`, or its mutants survive.)
