# v3.33.0 — the runtime-completeness release

The POD/co-activation roadmap (`research/11-pod-roadmap.md`) shipped its big rocks
in v3.21–v3.24; this release lands the recorded leftovers — A5, B4, C3 — closing
the file.

## `audit_graph` — the call graph, audited by reality (C3)

The one op in the family that points back at *us*. For every test with both a
coverage row and a graph node, compare what it **executed** (runtime ground truth)
with what it **statically reaches**:

- **recall** — of the functions tests actually executed, how many the graph
  predicted. The headline precision number for the call graph itself.
- **`missed_functions`** — executed on paths the graph cannot see (dynamic
  dispatch, `getattr`, framework wiring), ranked by how many tests hit them.
  This is the actionable resolver-gap worklist: each entry is a place the
  extractor/resolvers could improve, measured, not guessed.
- **over-approximation ratio** — reachable-but-not-executed is *reported as a
  ratio, never as a defect list*: static reach over-approximates by design and a
  run may simply not exercise a branch.

No numpy — set math plus one sidecar-fast forward closure per test. Falsified in
test: wiring the missing static edge takes recall to 1.0 and empties the list.

## `co_change` anchored on a test (A5)

Passing a **test** symbol flips the question from "what co-moves with this
function" to **"what does this test really cover"** — the union of its executed
functions across parametrized/phase rows. The test-intent audit: does the test
named `test_checkout` actually exercise checkout?

## `find_coupling`: explain-away annotation + scope (A5)

Every reported pair now carries **`common_callers`** — static callers shared by
both sides. A populated list usually *explains* the co-activation (siblings of one
dispatcher — visible coupling, not hidden); an empty list is the genuinely hidden
kind. `scope="cross_file"` / `"same_file"` filters the view. The envelope reason
now says exactly this.

## `find_similar(mode="behavior")` (B4)

`snippet` names a symbol; the ranking is nearest neighbours in the coverage
matrix's **mode space** — singular-value-scaled function loadings, cosine. Two
functions close here *behave* similarly across the suite even when lexically and
structurally unrelated: the denoised complement to `co_change`'s raw column cosine
(the SVD's top-k modes smooth away single-test noise). numpy-gated; only functions
the suite exercised can appear; refuses honestly otherwise.

## Tests

Four new tests pin each feature, including the audit falsification arm and a
mode-space test where the co-activating pair beats the solo-activating function.
Full suite green.
