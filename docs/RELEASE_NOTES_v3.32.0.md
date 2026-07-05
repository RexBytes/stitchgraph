# v3.32.0 — the purpose release

The first capability from the parked IDEAS research ships (archetype/purpose,
IDEAS §2–3, quantified in `research/05-archetype-purpose/FINDINGS.md`), and the
last hub metric picks up the provenance discount recorded in v3.29.0.

## `find_component(query)` — "where is the thing that does X?"

The §2 spike established that *what code does* lives on the semantic/name axis
(topology tracks the extractor, not the application). The §3 spike turned that
into a recipe; the ablation on 17 labelled queries × 17 packages earned each
ingredient its place:

| variant | P@1 | MRR |
|---|---|---|
| raw `find_similar` | 53% | 0.64 |
| + exclude test code (role AND test-file path) | 59% | 0.70 |
| + boost exported/public API | **76%** | **0.80** |

Shipped as a first-class op — advisory, confidence-carrying, INFERRED provenance,
the public boost visible in the score. Registry-registered, so
`stitchgraph find-component "parse command line options"` and the MCP tool exist
automatically.

Field smoke on the 16M-edge graph: "set up a config entry flow for an
integration" → `async_setup_entry` (public) at rank 1 — the right answer.
Honest caveats carried from the research: token similarity inherits
`find_similar`'s O(nodes) scan (~minutes at 59k nodes — the dense-embedder path
with a prebuilt index is the recorded fix), minified/bundled sources defeat name
search, and a specific public symbol can drown under same-token siblings.

## Hub metrics: the provenance discount is now everywhere

`transitive_fan_in` and `pagerank` build their matrices from EXTRACTED edges only
(`confident_only=True`, overridable) via the new
`Store.iter_resolved(confident_only=True)` lean stream. This closes the v3.29.0
follow-up: direct fan-in got the discount then (`confident_fan_in`), the closure
metrics now agree — a homonym's AMBIGUOUS widening arms are resolution artifacts,
not dependency mass. Falsified both directions in tests: the raw matrices rank a
6-ambiguous-arm homonym above a 3-confident-edge hub; confident-only inverts.

**Liveness sweeps deliberately stay raw**: an ambiguous edge must keep its target
alive (precision-over-recall — the cardinal rule). Only *ranking* discounts guesses.

## Tests

- `find_component`: public symbol outranks a same-vocabulary internal helper, test
  code never surfaces, and a falsification arm proves the boost (not raw
  similarity) is what wins it; refusal cases pinned.
- Algebra: homonym-vs-hub inversion pinned for both metrics, both directions;
  a liveness test pins that reachability still traverses AMBIGUOUS edges.
- Full suite green.
