# Findings — graph-diff oracle prototype

**Role:** the shared primitive behind questions **#2** (translate a codebase) and **#3**
(matrix-first development). Both reduce to the same operation: *given two indexes, where do
their graphs differ?* — translation fidelity is "does B preserve A's structure?", plan-vs-actual
is "does the built graph match the planned one?".

**Date:** 2026-06-29  ·  **Files:** `graphdiff.py` (primitive), `demo.py` (3 scenarios)

## What it does

A symmetric, *located* structural diff between two stitchgraph snapshots, in two modes:

- **id mode** — compares node identities `(kind, qualified-name)` and edges keyed by
  `(src-name, relation, dst-name)`. Exact; for same-codebase / same-language comparisons.
- **leaf mode** — compares node *shapes* `(kind, leaf-name)` and edges keyed by
  `(relation, src-leaf, dst-leaf)`. For cross-language comparisons where module paths and
  qualified names differ but the call/def shape should survive.

Every delta carries the node/edge it concerns, so a human/LLM can act on it.

## Demo results (all reproduce via `python research/graphdiff/demo.py`)

| Scenario | Mode | Expected | Result |
|---|---|---|---|
| 1. same source indexed twice | id | empty | **EQUIVALENT ✓** (no phantom deltas) |
| 2. drop a call + rename a fn | id | locate both | flagged `helper→compute` rename **and** the dropped `run→log` edge, exactly |
| 3a. literal Py→JS translation | id | (coincidental match) | equivalent — top-level names coincide |
| 3b. literal Py→JS translation | leaf | shape preserved | **EQUIVALENT ✓** — call shape survives the language hop |
| 3c. *restructured* Py→JS (fn→class method) | id | noisy | located `validate`→`Validator`+`validate-method`, new `main→Validator` |
| 3d. *restructured* Py→JS | leaf | locate real delta | same — and critically, the *preserved* `validate→parse` / `main→validate` edges are **not** flagged |

The headline is **3d**: when a translation restructures `validate()` into `Validator.validate()`,
the oracle flags exactly that (one fn becomes a class+method, one new construction edge) while
correctly reporting the unchanged call shape as unchanged. That is the "matrix as oracle, not
generator" thesis working: the LLM writes the translation; this primitive *verifies* what survived.

## Real cross-language twin — the honest Q2 number (`measure_translation.py`)

The demo toys were too clean, so a faithful Python↔JS translation of the *same* ~9-function
recursive-descent calculator (`fixtures/calc_py`, `fixtures/calc_js`) was indexed and diffed.

| | nodes | edges |
|---|---|---|
| Python | 14 | 20 |
| JS | 14 | 20 |
| **leaf-mode recall (before ctor-normalisation)** | **93%** | **95%** |
| **leaf-mode recall (after ctor-normalisation)** | **100%** | **100%** |

The *entire* algorithm call-shape — `tokenize → parse → _expr → _term → _factor` (recursive),
`evaluate` recursion, `calc` driver — matched across the language hop. The **only** residual was
`__init__` (Python) vs `constructor` (JS): a pure naming convention, not a lost edge. Normalising
constructor spellings to a canonical `<init>` token (`_CTOR_ALIASES` in `graphdiff.py`) closes it
to a clean **100%** — and it's safe, because a genuinely missing/extra constructor still shows as a
node-count delta.

**Takeaway:** for a *faithful* translation, leaf-mode graph-diff is a high-recall fidelity oracle —
the structural signal survives the language hop almost perfectly once trivial naming conventions are
normalised. This is the strongest evidence yet for the "matrix as verifier" half of Q2: the LLM
writes the JS, this primitive confirms the call graph it produced matches the Rust/Python original.
The complementary harder case (a *restructured* translation) is demo scenario 3c/3d, where the
oracle instead *locates* the genuine structural change.

## Body-aware diff — folding the PDG in (the Q3 result; `structure_diff.py`)

The call-level diff above answers "same defs, same call edges?". It is blind to *how* each function
is implemented. `structure_diff.py` adds the missing dimension: for every function present on both
sides, it compares the **expression-level value-flow fingerprint** (experiment 04) and flags bodies
whose shape diverged even when the call graph did not.

Demo — a *plan* vs two builds:

| comparison | call-level oracle | body-aware oracle |
|---|---|---|
| plan vs faithful build | EQUIVALENT ✓ | all 3 bodies match ✓ |
| plan vs **buggy** build | **EQUIVALENT ✓** | **`score()` body CHANGED (0.63)** |

The buggy build has an identical call graph — `score` still calls `heavy` twice and `combine` once —
but a data-flow bug: the second `heavy()` is fed `a` instead of `b`, so `b` no longer flows into the
result. The call-level oracle cannot see this (the call multiset is unchanged); the body-aware
oracle locates the exact offending function.

**This is the Q3 spine.** "Matrix-first development" = the LLM proposes a structure, builds, and the
diff is the located gap between planned and actual — and the gap that matters is usually *inside* a
function, not in which functions/calls exist. The call graph alone cannot make that check; the
value-flow matrix can. (Same engine doubles as a stronger Q2 translation-fidelity check for
same-language refactors; cross-language stays call-level, since expr-DFG is Python-only for now.)

## Honest limits (the §2 caveat, made concrete)

- **3a is a coincidence, not a triumph.** id mode matched only because both toys use bare
  top-level names. Real cross-language translation diverges in qualified names *and* in extractor
  depth (Python's `ast` is deeper than the JS tree-sitter grammar), so id mode is the wrong tool
  cross-language — use leaf mode and read deltas as *candidates*, not verdicts.
- **Extractor asymmetry is a confound, not signal.** A non-empty leaf-mode diff between two
  languages mixes genuine translation differences with extractor differences. The oracle therefore
  answers "did the shape change?" with a *located* delta list a human triages — it never asserts
  "the translation is wrong." This is exactly the §2 finding (topology tracks the extractor)
  encoded as a usage rule.
- **Set/multiset keys lose order and control flow.** Two functions that call the same helpers in
  a different order/branching read as equivalent. Sufficient for an oracle; insufficient as proof.

## Verdict & path to `src/`

The primitive is sound and the baseline guard (scenario 1 empty) makes it safe to build on.
**It is the right thing to promote** — it is the verifier half of both #2 and #3. Before promotion
it needs, per the project's own bar:

1. a real home + API (`sg.graph_diff(a, b, mode=...) -> Result`-shaped, with confidence/provenance);
2. the full release gate (ruff/mypy + pytest + a *new differential oracle*: `diff(idx, idx)` is
   empty for every language in the corpus — the streaming-vs-inmemory oracle already proves the
   inputs are deterministic, so this is cheap);
3. a two-round full-diversity adversarial panel, same as every cardinal fix.

Recommended next research step before promotion: run leaf-mode diff on a *real* known translation
pair (e.g. a small library that exists in both Python and JS) to measure the genuine
delta-to-noise ratio, so the promoted op ships with an honest precision number.
