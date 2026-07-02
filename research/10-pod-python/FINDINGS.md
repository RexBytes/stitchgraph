# POD for Python (§6 win 3): SVD over a runtime co-activation matrix — what it's for

**Task (maintainer):** implement POD for Python and find out what it can be used for. POD (Proper
Orthogonal Decomposition = mean-centred SVD/PCA) is applied to the **runtime co-activation matrix**
`M[test, function] = 1` iff that test executed that function (from `coverage.py` per-test contexts).
Its singular vectors are the codebase's **behavioural modes** — sets of functions that fire together
across the suite.

**Data:** Flask 3.1.3, its own test suite run under `coverage --cov-context=test`. Matrix:
**831 tests × 296 functions** (273 ever executed), density 0.112. Scripts: `pod_modes.py`, `pod_uses.py`.

## Results

**Singular-value spectrum (behavioural energy):**
```
mode 1: 49.7%   mode 2: 16.8%   mode 3: 5.0%   mode 4: 3.4%   mode 5: 3.1%   mode 6: 2.7% ...
14 modes capture 90% of the behavioural variance (of 296 functions / 831 tests)
```

**The modes are coherent runtime subsystems** — each concentrates in specific Flask modules:

| mode | dominant modules | reads as |
|---|---|---|
| 1 (49.7%) | sessions.py, wrappers.py, app.py | the always-on request/session core |
| 2 (16.8%) | sansio/app.py, sansio/scaffold.py, json/provider.py | app construction + URL-rule registration |
| 3 | sansio/blueprints.py | blueprint registration |
| 4 | ctx.py, app.py | app/request context push/pop |
| 5 | json/tag.py | JSON tagged-object (de)serialisation |
| 6 | sansio/app.py, ctx.py | app setup / templating env |

## What it can be used for (demonstrated)

1. **Dynamic subsystem discovery.** The modes *are* runtime functional subsystems (routing, blueprints,
   contexts, sessions, JSON), recovered purely from what executes together — a runtime complement to
   the static `find_subsystems` (which clusters the call graph). It sees behaviour the call graph
   can't (e.g. framework dispatch that statically reaches everything).
2. **Intrinsic behavioural dimensionality.** "≈14 independent behaviours capture 90%" — a single
   number describing how many distinct things the suite actually exercises.
3. **Test-suite minimisation.** Greedy set-cover over `M`: **57 of 831 tests (6.9%) cover all 273
   executed functions**; the other 774 add no new function coverage. Directly actionable (CI time,
   redundancy triage).
4. **Feature ↔ code ↔ test mapping.** Each mode = (its top-loading functions = a feature's
   implementation) × (its top-expressing tests = that feature's tests). Powers "which tests exercise
   feature X", coverage-gap-by-feature, and behaviour-scoped impact.
5. **Redundancy / outliers.** Near-duplicate activation rows = redundant tests (23,097 cosine-≈1
   pairs here); rows orthogonal to all modes = unique-behaviour (or everything-touching) tests.

## Why this one is different from the rest of the dogfood thread

Every other capability tested this session (build, extend, impact, translate) **competed with an LLM's
ability to read code — and tied or lost**, because a capable model can just read. **POD does not
compete with reading.** It is grounded in *runtime measurement* (which lines actually ran, per test)
plus *linear algebra over the co-activation matrix* — neither of which an LLM can reproduce by reading
source, at any context size. You cannot "read your way" to "these 57 tests cover everything" or "the
suite has 14 behavioural modes concentrated in these modules." **This is the first capability in the
thread that is genuinely LLM-complementary rather than LLM-redundant.**

## Self-dogfood: POD on stitchgraph itself (via the shipped `find_modes` op)

Ran stitchgraph's own tests under the **shipped** capture recipe (`pytest --cov-context=test`) →
shipped `to_canonical.py` → `find_modes` (representative 7-test-file subset; the full ~2,300-test run
under per-test contexts was too slow to block on):

```
81 tests × 622 functions,  density 0.126
intrinsic dimensionality:  8 modes (90% energy)
minimal covering set:      33 / 81 tests cover all 622 executed functions
```
The modes recovered stitchgraph's own subsystems: (1) extraction+store [46%], (2) tree-sitter polyglot
extraction [21%], (3) similarity / body-matrix, (4) graph algebra, (5) operations/get_matrix,
(7) spectral decomposition. i.e. POD run on its own author factors it into extraction → tree-sitter →
body-matrix → algebra → operations → spectral — the real architecture — and flags ~40% of these tests
as redundant for function coverage. Confirms the op reproduces the research spike (Flask: 831→57) on a
second, independent codebase (stitchgraph).

## Honest caveats

- **Requires a runnable suite + coverage** — the "runtime" cost. Unlike static ops it isn't free; you
  must execute the tests (here, in a pinned-pytest venv). Only the *exercised* code is seen (untested
  code is invisible — a limitation, but also the point: it characterises real behaviour).
- **Modes are axes, not a hard partition** — a function can load on several; mode 1 is the "everything"
  axis. For hard clusters, run k-means on the mode embedding (as `find_subsystems` does statically).
- Coverage granularity is line→function here (a function counts as activated if any line ran).

## Productisation proposal

A new advisory operation — call it `find_modes` (or `behavioral_subsystems`) — that consumes the
same coverage stitchgraph already ingests via `ingest_trace` (coverage.py JSON / per-test contexts),
builds `M`, and returns: the mode→function/module breakdown, the intrinsic dimensionality, a
minimal covering test set, and a feature↔test map. numpy-only (SVD); scipy `svds` for large `M` via
the existing `[spectral]` extra; matrix-free/streamed like `find_stale` if `M` is huge. Advisory,
read-only, cardinal-safe. This is §6 win 3 and, per the finding above, the most defensible new
capability because it is LLM-complementary.
