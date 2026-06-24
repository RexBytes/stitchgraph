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
