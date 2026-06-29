# research/ — exploratory spikes (not part of the package)

Experiments on top of stitchgraph, exploring the parked ideas in
[`../docs/IDEAS.md`](../docs/IDEAS.md). **Nothing here ships in the `stitchgraph`
package** (`pyproject.toml` packages only `src/stitchgraph`); these are throwaway-ish
research scripts kept for reproducibility. They need `stitchgraph[all,dev]` installed
and network access to the PyPI + npm registries.

Downloaded corpora land in `research/_corpus/` (git-ignored).

## Spikes & findings (2026-06-23, post-v1.0.0)

### §2 — Does the graph reveal *what the application is*, across languages?
`archetype_fingerprint.py` builds a 5-archetype × 2-language corpus from real
packages (cli: click/commander · web: flask/express · http: requests/axios ·
template: jinja2/handlebars · date: arrow/dayjs), indexes each, and compares
fingerprints.

| Fingerprint | same-archetype (×-language) | same-language | baseline | NN archetype acc. |
|---|---|---|---|---|
| **Topology** (degree/hub/kind metrics) | −0.47 | **+0.42** | −0.50 | **0/10** |
| Names (raw token bag) | +0.15 | +0.49 | +0.07 | 1/10 |
| **Names (TF-IDF)** | **+0.22** | +0.14 | +0.10 | **6/10** |

**Conclusion:** the naive IDEAS.md §2 hypothesis is **false** — graph *topology*
tracks **language/extractor**, not application function (topology never picks the
same-archetype repo as its nearest neighbour). But a **semantic-name fingerprint**
(identifier tokens, language-generic vocabulary down-weighted by TF-IDF) **does**
identify the application archetype and the signal is **language-invariant** (~6/10
nearest-neighbour accuracy vs ~1/9 chance; cleanest cross-language pairs
click~commander, requests~axios, jinja2~handlebars, arrow~dayjs). The viable path to
"identify what the package does" is semantic, not topological — and is exactly what
stitchgraph's pluggable `find_similar` embedder (`set_embedder`) could do better than
a TF-IDF bag.

Caveats: small corpus (10 packages); topology is *confounded by extractor asymmetry*
(Python `ast` is deeper than JS tree-sitter), which inflates the language split;
published package source differs in scope (e.g. flask 83 files vs express 7).

### §3 — Purpose-aware "find the component that does X"
`find_component.py` builds on the §2 result (the *semantic* axis is what carries
purpose) and stitchgraph's existing `find_similar` (token similarity over name +
docstring + callees), made usable by exploiting structure stitchgraph already
models: **exclude test code** (by `test` role *and* test-file path — needed because
function-local helpers nested in test methods are now first-class nodes after the
Panel Q/T nesting work) and **boost exported/public API**.

Result: free-text purpose queries locate the right public components across
application types —

| Query | Package | Top public hits |
|---|---|---|
| "parse command line options" | click | `Command`, `Option`, `Group.resolve_command` |
| "send an http request / response" | requests | `Response`, `Session.request`, `Request` |
| "render a template" | jinja2 | `Environment.get_template`, `get_or_select_template` |
| "match a url route to a handler" | flask | `Blueprint.add_url_rule`, `Scaffold.route`, `url_for` |

3/4 nail the right component as the #1 public hit; flask routing has a noisy #1
(a decorator wrapper) but the real router ranks 2–5. **On-brand:** the graph
supplies verifiable, role-aware structure; the ranking stays advisory. A natural
next step is exposing this as a first-class purpose helper and swapping the token
similarity for a dense embedder.

### §1 — Does structural signal predict where code gets fixed?
`risk_centrality_check.py` isolates the non-circular question (since `risk()` uses
churn as both input and label): does **structural centrality alone** (fan_in+fan_out,
no git) correlate with a file's **historical change frequency** (`git log`)?

On stitchgraph's own repo: **Spearman ρ ≈ 0.65**, top-5 most-central files overlap
**4/5** with top-5 most-changed. Supports the premise behind `risk()` — centrality
carries fix-proneness signal independent of churn. **Suggestive, not conclusive:** a
single repo whose history is skewed by its own development.

## What's blocked / needs real repos
- The agent environment **cannot clone arbitrary git repos** (org egress policy
  blocks github.com; registry downloads give sdists/tarballs with **no `.git`**).
- **§1 needs external repos with diverse git history** to be conclusive (the
  maintainer offered to provide them). §2/§3 can grow on registry packages alone.

## Suggested next steps

> **▶ RESUME HERE (parked 2026-06-23):** promote `find_component` (§3) toward a real
> feature — (a) swap token similarity for `find_similar`'s pluggable **dense embedder**
> (`set_embedder`); (b) fold the cross-language **boundary signals** stitchgraph already
> extracts (routes / SQL tables / events) into the archetype fingerprint; (c) build a
> first **archetype classifier** ("this package is a web framework") and expose an
> advisory `find_component(query)` op. §1 stays parked (needs external repos with git
> history we can't move); §4 stays gated on a stronger §2 structural signal.

1. **§2/§3:** swap the TF-IDF bag for `find_similar`'s dense embedder; add the
   cross-language boundary signals stitchgraph already extracts (routes/SQL/events)
   to the fingerprint; grow the corpus; build an archetype classifier — the basis for
   purpose-aware helpers (§3), kept advisory/confidence-carrying per the cardinal
   stance.
2. **§1:** run `risk_centrality_check.py` (and a churn-controlled variant) over a
   maintainer-provided corpus of real repos with history; measure precision@k of
   `risk()` against real fix locations.
3. **§4 (unnamed patterns):** only once §2 shows a usable structural+semantic signal.

## Run
```bash
pip install -e '.[all,dev]'
python research/archetype_fingerprint.py      # §2 spike
python research/risk_centrality_check.py      # §1 spike (defaults to this repo)
```

---

# Matrix-as-oracle thread (2026-06-29, post-v2.2.1)

A second research push, asking whether the *structural matrix itself* can do generative-adjacent
work. Three questions, framed by one thesis and one prior result.

**Thesis — matrix as oracle, not generator.** stitchgraph's matrices encode **structure**
(topology, coupling, call shape), not **semantics**. So the matrix should never *write* code — but
it can **plan** structure and **verify** structure. The LLM supplies meaning; the matrix proposes
candidates and checks that the result has the intended shape. Every output stays advisory and
confidence-carrying, exactly as the cardinal stance demands.

**Prior result that constrains everything here — the §2 finding (above):** graph *topology* tracks
the **language/extractor**, not application function. Anything cross-language must treat raw
topology as a *candidate signal a human triages*, never as proof. This is why the questions below
split cleanly into "same-language (sound)" and "cross-language (oracle-only)".

| # | Question | Verdict | Where |
|---|---|---|---|
| 1 | Can the matrix surface **reducible / redundant code**? | **Capability real, but precision-sensitive — and a confident *negative* on this repo.** Raw callee-fingerprint clones are dominated by hub-callee noise; the required IDF + distinctive-helper refinement collapses it to *zero* actionable candidates here — stitchgraph's own code is already well-factored (only intentional forward/reverse twins, shared logic already extracted). Honest validation needs a corpus that actually contains duplication. | `01-structural-redundancy/` |
| 2 | Can it drive **translation** (e.g. Rust→JS)? | **Reframed: scaffold + verifier, not translator.** The matrix can't translate (it has no semantics), but graph-diff in *leaf mode* verifies a translation preserved the call/def shape. Cross-language confounded by extractor asymmetry → oracle, not proof. | `graphdiff/` |
| 3 | Is **matrix-first development** faster for an LLM? | **Reframed: plan + verify spine.** The valuable artifact is a graph-diff between *planned* structure and *built* structure — the LLM proposes a graph, builds, and the diff is the located gap. | `graphdiff/` |

**The unifying primitive is the graph-diff oracle** (`graphdiff/`). Both #2 (translation fidelity)
and #3 (plan-vs-actual) reduce to "where do two graphs differ?". It is prototyped here, demoed
(empty-baseline guard + located-delta cases + cross-language leaf-mode), and is the **planned
promotion to `src/`** — *after* research, and only through the full gate + two-round adversarial
panel, like every other change. See `graphdiff/FINDINGS.md` for the path-to-`src/` checklist.

### The granularity ladder (what level the matrix is built at)

The shipped matrix is **inter-procedural**: nodes are defs, edges are CALLS/REFERENCES/INHERITS/
IMPORTS. Experiment 02 drops one level *into* the function body, which is where redundancy actually
lives:

| Level | Built at | Experiment |
|---|---|---|
| call graph (shipped) | defs ↔ defs | `01-structural-redundancy/` |
| **body matrix** (normalised AST / control+data shape) | statements inside a function | `02-body-matrix/` |
| CFG / DFG / PDG (future) | basic blocks, def-use | deferred ("variable-granularity data flow" roadmap item) |

**Key result:** experiment 01 (call graph) returned a confident *negative* on this repo; experiment
02 (body matrix) found a **real** cross-module duplication the call graph is blind to — a
byte-identical Tarjan SCC core in `dataloop._tarjan` and `reach.strongly_connected_components`
(verified by reading both). Matrixifying function *contents*, not just their call edges, is the
stronger redundancy signal — and the same representation sharpens Q2/Q3.

### Layout
- `01-structural-redundancy/` — `experiment.py` (call-graph clones) + `experiment_idf.py`
  (IDF precision pass) + `FINDINGS.md` (question #1; confident negative on this repo).
- `02-body-matrix/` — `body_matrix.py` (normalised-AST body clones) + `fixtures/clones.py`
  + `FINDINGS.md` (found the Tarjan duplication the call graph missed).
- `graphdiff/` — `graphdiff.py` (the oracle primitive), `demo.py` (3 scenarios),
  `measure_translation.py` + `fixtures/calc_{py,js}/` (real Py↔JS twin: 100% leaf-mode recall),
  `FINDINGS.md` (questions #2 & #3, + promotion checklist).

### Run
```bash
PYTHONPATH=src python research/01-structural-redundancy/experiment.py        # Q1 call-graph clones
PYTHONPATH=src python research/01-structural-redundancy/experiment_idf.py    # Q1 IDF precision pass
python research/02-body-matrix/body_matrix.py                                # Q1 body-level clones (stdlib only)
PYTHONPATH=src:research/graphdiff python research/graphdiff/demo.py          # Q2/Q3 oracle demo
PYTHONPATH=src:research/graphdiff python research/graphdiff/measure_translation.py  # Q2 real twin number
```

> **▶ RESUME HERE (parked 2026-06-29):** (a) add IDF callee-weighting to experiment 01 and validate
> against a real "extract-helper" commit from git history; (b) run graph-diff leaf mode on a *real*
> Python↔JS translation pair to get an honest delta-to-noise number; (c) then promote `graph_diff`
> to `src/` as `sg.graph_diff(...) -> Result`, add a `diff(idx, idx)==∅` differential oracle per
> language, and run the two-round full-diversity panel before any release.
