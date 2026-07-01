# Ideas / Nice-to-have backlog (parked)

> **Status: parked.** These are exploratory research directions, not committed work
> and not part of the v1.0.0 scope. Do **not** action anything here until the
> maintainer explicitly raises it again. The near-term, committed roadmap lives in
> [`STATUS.md`](STATUS.md#roadmap-whats-left); this file is for longer-horizon,
> higher-risk/higher-reward ideas.

Captured 2026-06-23. Author: maintainer (zorani). The four items below are believed
**related and parallelisable** (see §4).

---

## 1. Validate / iterate effectiveness against real-world repos using bug-fix commits as ground truth

Rather than only synthetic fixtures, measure how good stitchgraph actually is by
pointing it at existing, well-known git repositories and using their history as
**labelled data**:

- Take a commit that **fixes a known issue/bug**. Check out the code *before* the
  fix and run stitchgraph — does it detect/flag the problem (dead code, hole,
  risky hotspot, missing edge, etc.)? Then run it on the code *after* the fix and
  compare.
- Bug-fix commits (and their linked issues) become a stream of "training" /
  evaluation points: before = positive case, after = negative case.
- This gives a real precision/recall signal on real code, complements the panel
  process, and could be turned into a repeatable benchmark harness.

Open questions: which issue/defect classes stitchgraph can realistically detect
vs. which are out of scope; how to label automatically (issue text, CI failures,
test diffs); how to avoid overfitting to one project's style.

> **Spike result (2026-06-23, `research/risk_centrality_check.py`):** the strongest
> achievable framing is *risk-prediction*, and a first non-circular check is positive
> — **structural centrality alone** (no git) correlates with historical change
> frequency at **Spearman ρ ≈ 0.65** on stitchgraph's own repo (top-5 central files
> overlap 4/5 with top-5 most-changed). Suggestive only (one self-skewed repo).
> stitchgraph is **not** a logic-bug finder, so scope this to structural defects +
> risk prediction. **Blocked on** external repos *with git history* (registry
> downloads have no `.git`; clones are egress-blocked) — maintainer to provide.

## 2. Is there a connection between graph structure and *function* (what the code does)?

Hypothesis: the dependency / call graph for the **same kind of application** is
structurally similar **across languages**. E.g. would the graph of an *audio
player* written in Python and the same app in another language look alike?

- **First, check if it's true** — build the graph for several implementations of
  the same application type (same app, different languages; and different apps,
  same language) and compare structure (motifs, shapes, hub patterns, subgraph
  isomorphism / graph similarity metrics).
- **If it holds**, start building a **pattern database** shipped as part of the
  package, so stitchgraph can **identify what a codebase *does*** from its graph
  (application archetype / "this looks like an audio player / web server / CLI
  parser / ETL pipeline").

Open questions: what's the right structural fingerprint (graph kernels, motif
counts, role/centrality distributions)? How language-invariant is it really?
How big a corpus is needed before patterns are stable?

> **Spike result (2026-06-23, `research/archetype_fingerprint.py`, 5 archetypes ×
> 2 languages):** the naive hypothesis is **false for graph topology** — topology
> tracks **language/extractor**, not application function (0/10 nearest-neighbour
> archetype accuracy; same-*language* similarity +0.42 vs same-archetype −0.47).
> **But a semantic-name fingerprint works and is language-invariant:** identifier
> tokens with language-generic vocabulary down-weighted (TF-IDF) reach **~6/10**
> cross-language nearest-neighbour archetype accuracy (chance ~1/9). So "identify
> what the package does" is reachable via the **semantic/name** axis — ideally
> stitchgraph's pluggable `find_similar` dense embedder — augmented by the
> cross-language boundary signals (routes/SQL/events) it already extracts, **not**
> via pure topology. The pattern DB should store these semantic+boundary
> fingerprints, not graph shapes.

## 3. If we can infer the *purpose* of code, what else can we do with that?

If §2 works and we can recover intent/purpose from structure:

- What new **helper functions / operations** could the package expose on top of
  that understanding? (e.g. "summarise what this subsystem is for," "find the
  component that plays audio," "locate the auth boundary," purpose-aware
  navigation, archetype-aware risk/orientation.)
- Purpose could sharpen existing ops too (better orient/summaries, smarter
  entry-point inference, archetype-specific dead-code priors).

This is the "so what" of §2 — enumerate the capabilities that become possible
once the graph is tied to function.

> **Spike result (2026-06-23, `research/find_component.py`):** a first §3
> capability already works. `find_similar` (free-text → symbols by name + docstring
> + callee tokens) already exists; making it **purpose-aware** — exclude test code
> (by `test` role *and* test-file path) and **boost exported/public API** — turns it
> into a working *component locator*: "parse command line options" → `Command`/
> `Option`; "send an http request" → `Response`/`Session.request`; "render a
> template" → `Environment.get_template`; "match a url route" → `Blueprint.add_url_rule`.
> 3/4 nail the right public component as #1. Natural productisation: a first-class
> `find_component(query)` op (advisory, confidence-carrying) and a dense embedder in
> place of token similarity. This is the on-brand path: graph = verifiable
> role-aware structure, LLM/embedder = the fuzzy purpose layer on top.

## 4. Do all of the above together, across a wide, varied corpus

The three above are related and can run **in parallel**: pick a **wide range of
repos with different applications** (varied domains *and* languages), then
simultaneously (a) validate detection on their fix history (§1), (b) compare graph
structure within/across app types (§2), and (c) prototype purpose-derived helpers
(§3).

Intriguing possibility: we may find **patterns that do something not yet named in
the code** — recurring structures that perform a recognisable function the authors
never labelled. Surfacing/naming those could be a feature in itself.

### Logistics / access note
If repo access is needed and the agent environment can't clone them directly, the
maintainer will **clone the chosen repos and expose them through their own repo**
for access. When this work is picked up: agree a repo shortlist first (diverse
domains × languages), then the maintainer provisions access.

---

_When resuming: start with §2's "check if it's true" step — it's the load-bearing
hypothesis the rest depends on — and §1's benchmark harness, which is useful
regardless of whether §2 pans out._

---

## 5. To-do / candidates surfaced by the matrix-as-oracle research (2026-06-29)

Captured during the post-v2.2.1 research thread (`research/` — matrix-as-oracle). stitchgraph
**works as shipped**; nothing here is urgent. Two items, one small + concrete, one large + strategic.

### 5a. Refactor: extract a shared `_tarjan_scc(adj)` helper (candidate v2.3.x)
The body-matrix spike (`research/02-body-matrix/`) found a **byte-identical Tarjan SCC core**
duplicated in `core/dataloop.py` (`_tarjan`) and `core/reach.py` (`strongly_connected_components`)
— same `index`/`low`/`on_stack`/`stack` setup, the same recursion-limit raise (down to the
identical `panel QQQ LOW` comment), the same ~40-line `strongconnect`. Only the adjacency
construction and output post-filter differ. **Extract** a single `_tarjan_scc(adj) -> list[list]`
and have both sites build their adjacency + post-filter around it.
- Low-risk (graph-algorithm code, **not** the cardinal dead-code path) but still production code,
  so it goes through the **full gate** (ruff/mypy + pytest + oracles + mutation) and a **two-round
  panel** as its own change. A real, if small, reason to cut **v2.3.0**.

### 5b. Higher-granularity (body / CFG / DFG / PDG) matrix as a real feature → **v3.0.0**
Today's matrix is inter-procedural (defs ↔ defs via CALLS/REFERENCES/INHERITS/IMPORTS). The
research (`research/02-body-matrix/`, `research/03-pdg/`) shows the **stronger** redundancy/fidelity
signal lives *inside* functions — control- and data-dependence, not just call edges. Promoting an
intra-procedural matrix (body AST → CFG → def-use/PDG) to `src/` would be a **new representation**,
i.e. a genuine **MAJOR / v3.0.0** release, and it's the natural home for:
- structural clone/redundancy detection (`find_similar(..., mode="structure")`);
- a stronger translation-fidelity oracle and plan-vs-actual diff (Q2/Q3);
- the long-deferred **variable-granularity data flow** roadmap item.
- **Hard gates before it can ship:** scale (intra-procedural graphs fight the constant-memory
  streaming indexer — needs a budget/opt-in), the **cardinal rule** (data-flow soundness is much
  harder; it must never let `find_stale` under-root), and 12-language breadth (Python-first;
  per-language CFG/DFG is a large surface). Prototype + validate in `research/` first.

### 5c. Tag the matrix by granularity layer (the layered / Code-Property-Graph design)
**Phases 1–2 SHIPPED: all three layers exist.** `model.Layer` (CALL / EXPRESSION / STATEMENT) tags
the granularity. Phase 1 (v3.8.0): `get_matrix(layer="expression")` drills into a function's
value-flow graph (`structure.vfg_source`, all 12 languages) and `graph_diff` is the two-layer diff.
Phase 2 (v3.9.0): `get_matrix(layer="statement")` drills into a function's program-dependence graph
(`structure.pdg_source` — statement nodes, control/data edges; **Python-only so far**, other
languages a future sweep). All on-demand (no persisted deep edges — the scale-driven choice below),
advisory-only (never feeds liveness). Remaining: sweep the STATEMENT layer to the tree-sitter
languages. Original design note follows.

When the deeper granularity (§5b) is promoted, do NOT build a second, separate graph. Carry a
**granularity/layer tag** on nodes and edges so all layers coexist in one matrix and a consumer
picks the depth:

- **call layer** (shipped): `NodeKind` (Module/Class/Function/Method/Variable) + `Relation`
  (CALLS/REFERENCES/INHERITS/IMPORTS). The class-level + function call-surface view.
- **statement layer**: control + data dependence between statements within a function (the PDG).
- **expression / value-flow layer**: operations and the values flowing between them (what
  `core/structure.py` fingerprints today, computed on demand).

Tagging (a `layer` field, analogous to how `Relation`/`provenance` already qualify edges) lets the
same primitives work at any depth: `get_matrix`/`graph_diff` default to the call layer (cheap,
whole-repo) and **drill down** into a function's value-flow layer on request; clone/redundancy and
plan-vs-actual checks choose the layer that fits. This is the Code-Property-Graph pattern (AST +
CFG + PDG in one graph distinguished by edge/level tags). Maintainer idea (2026-06-29). Open design
qs: persist deep layers in the store vs compute on demand (scale — the body layers are large and
fight the streaming indexer, so on-demand-per-function is the likely default); how `provenance`/
cardinal rules apply per layer (deeper = less sound → advisory-only until proven).

### 5d. Mutation-harden `core/similar.py`'s semantic / dense-embedding path (surfaced R163/R164)

While hardening v3.0.0, the mutation meta-oracle was driven to zero survivors on the **new** body
matrix (`structure.py` 15/15, `graphdiff.py` 9/9). Running it over `core/similar.py` leaves ~15
survivors — **all pre-existing, none in the v3.0.0 code path**, so deliberately *not* chased for the
release. They cluster in two buckets:

- **optional `model2vec` / dense-embedder path** (`_try_model2vec`, `load_config`, the embedding
  cosine `na/nb`): distinguishing these mutants needs a real model load (network/install) the core
  suite deliberately never triggers — the documented "optional-dep blind spot" (see
  `REVIEW_HISTORY.md` standing themes).
- **`_dense` ranking** (`reverse=`, `> 0` filter): the one test that exercises it
  (`test_pluggable_dense_embedder`) has a *score tie at the top*, so the sort-direction / filter
  mutants are indistinguishable — a weak test, not a product bug. (The **structure**-mode ranking
  in `find_similar_structure` IS pinned — `test_structure_mode_ranks_the_clone_first` asserts the
  top result and `top > csv`.)

Hardening pass (a future cardinal-loop-style chore, not a release blocker): (1) add a `_dense` test
with a strict, tie-free ranking so `reverse=True` / the `> 0` filter are pinned; (2) add an offline
fake-embedder fixture (or skip-marks gated on the `model2vec` extra) that distinguishes the
config/model-load mutants. Goal: `similar.py` joins `structure.py`/`graphdiff.py` at a clean
mutation score under a documented kill-signal.
