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
>
> **Scale-up (2026-07-02, `research/archetype_scale.py`, 11 archetypes × py/js, n=21):** the result
> replicates and strengthens — NAMES_TFIDF holds at **13/21 (~62%, ~6× chance)** and stays the only
> fingerprint whose same-archetype cosine beats same-language; TOPOLOGY stays ~chance (2/21). **NEW:
> the "augment with boundary signals" idea below is REFUTED** — route/SQL/event/ORM signals as a
> *global* fingerprint track language (0/21) because they're sparse across archetypes (only web has
> routes, only orm has MAPS_TO) so the vector collapses onto its language-driven kind-mix; blending
> them into the name vector *degrades* it (13/21 → 1/21). Boundary signals are a positive *detector*
> for the archetype that bears them, not a general classifier. Path stays: semantic-name axis, ideally
> the dense embedder. Full writeup: `research/05-archetype-purpose/FINDINGS.md`.

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
>
> **Quantified (2026-07-02, `research/find_component_eval.py`, 17 labelled queries × 17 py+js
> packages):** ablation confirms both ingredients earn their place — RAW `find_similar` **53% P@1 /
> 0.64 MRR** → drop-tests **59% / 0.70** → +public-boost **76% P@1 / 0.80 MRR** (the recipe). Failure
> modes both argue for the dense embedder: **minified npm dist tarballs** (`marked`/`dayjs` ship
> bundled single-char names) defeat name search outright, and token cosine can drown a specific public
> fn under same-token siblings (pygments `highlight` vs dozens of `*Lexer`). Works cross-language where
> *source* ships (express→`app.route`, axios→`Axios.request`). Full writeup:
> `research/05-archetype-purpose/FINDINGS.md`.

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

### 5a. Refactor: extract a shared `_tarjan_scc(adj)` helper → **SHIPPED v2.3.0**
**Done.** The shared core now lives in `core/_scc.py` as `tarjan_scc(adj, seeds, node_count)`;
`core/reach.py` (`strongly_connected_components`) and `core/dataloop.py` (`_tarjan`) both build their
own adjacency + post-filter around it, and the duplicated ~40-line `strongconnect` (with the
`panel QQQ LOW` recursion-limit handling) exists once. `_scc.py` imports only `sys` + `collections.abc`,
so `reach.py` importing it introduces no cardinal-rule violation. Original note follows.

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
**Phases 1–2 SHIPPED: all three layers exist.** `model.Layer` (CALL / STATEMENT / EXPRESSION) tags
the granularity. Phase 1 (v3.8.0): `get_matrix(layer="expression")` drills into a function's
value-flow graph (`structure.vfg_source`, all 12 languages) and `graph_diff` is the two-layer diff.
Phase 2 (v3.9.0): `get_matrix(layer="statement")` drills into a function's program-dependence graph
(`structure.pdg_source` — statement nodes, control/data edges). Phase 3 begins sweeping the STATEMENT
layer to the tree-sitter languages: **v3.10.0 adds the JS family** (js/ts/tsx, `structure_js.pdg_source`)
**v3.11.0 adds Go**, **v3.12.0 adds Rust**, **v3.13.0 adds C/C++**, **v3.14.0 adds Java**, and
**v3.15.0 adds C#**, **v3.16.0 adds Ruby**, **v3.17.0 adds PHP**, and **v3.18.0 adds Bash — the
sweep is COMPLETE**, so the statement layer now covers **every body-matrix language: Python + the JS
family (js/ts/tsx) + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash**. All on-demand (no persisted deep edges —
the scale-driven choice below), advisory-only (never feeds liveness). Original design note follows.

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

## 6. Spectral / linear-algebraic analysis of the system matrix (POD / SVD / Laplacian)

Maintainer idea (2026-07-02). **Framing:** a program's functions are like components in a complex
electronic system, and stitchgraph already materialises the *system matrix* — the sparse adjacency
`A` over nodes (CALLS/REFERENCES/INHERITS/IMPORTS), the same matrix `orient`/`risk` already run
GraphBLAS reachability and PageRank over. So this asks: **do classical matrix decompositions (SVD,
POD/PCA, the graph Laplacian, electrical-network methods) extract structure the current
frontier-BFS / PageRank sweeps don't — especially a principled "importance of parts of the code"?**

Note stitchgraph is *already doing one spectral method*: PageRank is the dominant eigenvector of the
stochastic adjacency. This generalises that from one eigenvector to the full spectrum + SVD + the
Laplacian, and asks what each buys.

**Candidate decompositions and what each would compute:**
- **SVD of `A` (≡ HITS).** Left/right singular vectors of the adjacency are exactly hub/authority
  scores; the top **authority** vector ranks the most-depended-upon code (interfaces you break most
  by touching), the top **hub** vector ranks orchestrators — a *different* importance axis from
  PageRank (which conflates the two). Singular-value magnitude = how dominant each mode is.
- **POD / PCA (SVD of a snapshot matrix).** The rows of `A` are per-node connectivity profiles;
  PCA over them yields the dominant *connectivity modes* and a low-dim node embedding whose clusters
  are subsystems. A richer snapshot source: the **runtime coverage matrix** stitchgraph already
  ingests (`ingest_trace`) — POD over "which functions fired together across executions" finds
  co-activation modes ("this cluster is the request path"), the genuinely control-theory-flavoured
  version.
- **Graph Laplacian `L = D − A`.** Count of near-zero eigenvalues ≈ number of weakly-coupled
  subsystems; the **Fiedler vector** gives the best 2-way cut → automatic module decomposition
  (spectral clustering) that would sharpen `summarize_subsystem`. The **spectral gap** is a single
  "how decoupled is this architecture" number — trackable across versions via `graph_diff`.
- **Electrical-network view (leaning into the analogy).** Treat edges as conductances: **effective
  resistance** between nodes measures true coupling (beats shortest-path), and **current-flow
  betweenness** ranks load-bearing functions whose removal fragments the system — a physical
  criticality/robustness measure complementing `impact_of`'s reverse reachability.
- **Structural controllability (control theory proper).** Driver-node analysis (Liu–Slotine–Barabási)
  finds the minimal set of nodes that control the whole system — a first-principles take on
  entry-point / leverage-set detection.

**How we'd use it (the "so what"):**
- a spectral **importance score** for `risk`/`orient` (authority weight = "most dangerous to touch");
- automatic **subsystem boundaries** (spectral clustering) for `summarize_subsystem`;
- an architecture **modularity/health metric** (spectral gap) + drift detection across builds;
- **criticality ranking** (effective resistance / current-flow) for robustness triage;
- **driver/leverage set** as a principled entry-point complement.

**Caveats (load-bearing — set scope honestly):**
- **The §2 finding bounds this.** Topology tracks the *extractor/language*, not the *function*
  (0/21 archetype accuracy). So any spectral measure over pure topology inherits that bias: this is
  a **within-repo / within-language structural** tool (importance, modularity, criticality,
  decomposition), **not** a cross-language "what does it do" signal (that stays the semantic-name /
  embedder axis from §2/§3). Don't repeat §2's mistake of expecting topology to carry meaning.
- **Cardinal rule.** Every score here is **advisory** — spectral importance must never feed
  `find_stale` liveness, exactly as PageRank hubs and `risk` are fenced off today.
- **Scale — never materialise the dense matrix (the PoC's dense array was a ≤10³-node shortcut).**
  The design is **matrix-free**: the graph is naturally sparse (out-degree is bounded → edges ≈ O(n),
  not O(n²)), and the top-k spectral modes come from **Krylov-subspace iterative solvers** (Lanczos
  for the Laplacian, Golub–Kahan / `svds` for the SVD) that need *only* the matrix–vector product
  `y = A @ x` (and `Aᵀ @ x`) — never A², AᵀA, or any dense factor. k extreme modes = ~k·(few dozen)
  mat-vecs, i.e. **O(k·n) time, O(n) memory**, not O(n³)/O(n²). scipy's `svds`/`eigsh` take a
  `LinearOperator` (a *function* computing `A @ x`), so no matrix is ever formed. stitchgraph already
  does exactly this shape — **PageRank is matrix-free power iteration** (`rank.vxm(T)` in a loop).
  - **Larger than RAM → operate by parts (out-of-core).** `A @ x` is a sum over edges
    (`y[i] += x[j]` per edge i→j), so compute it by **streaming edges from SQLite in batches** —
    holding only the two length-n vectors + one edge-batch, O(n) RAM for any edge count. This reuses
    the **exact streaming pattern `find_stale` already uses** (the v2 stream-reachability fix for the
    16M-edge OOM). If even the length-n vectors don't fit (≫10⁸ nodes), **partition**: cluster first,
    decompose per-subsystem — which is the more useful object anyway.
  - **Numerics split:** the sparse/streamed mat-vec `A @ x` can be GraphBLAS `plus_times`, but SVD/
    eigen need subtraction/orthogonalisation/`sqrt` (not semiring ops), so the tiny k×k projected
    problem inside the Krylov loop is real-field (numpy). That's precisely what scipy `svds`/`eigsh`
    orchestrate — likely an optional `[spectral]` extra (scipy) rather than a core dependency.
- **Validate before shipping.** Like every idea here: prototype in `research/` first — the load-
  bearing spike is "does authority-SVD / Fiedler decomposition produce *sensibly different and
  useful* rankings vs the shipped PageRank/reachability on real repos, and does it survive the
  language-confound?" — before any `src/` surface.

_Relationship to the rest of the backlog: this is the **structural** counterpart to §2/§3's
**semantic** axis — §2/§3 answer "what does this code do" (names/embeddings), §6 answers "which parts
matter and how is it decomposed" (spectrum of the system matrix). They compose: semantic labels on
spectral clusters = "this dominant mode is the auth subsystem."_

> **Spike result (2026-07-02, `research/06-spectral/`, PoC on stitchgraph's own src, 839 nodes):**
> three verdicts. (1) **Low-rank confirmed** — top-10 singular modes carry **69.5%** of the matrix
> energy (σ₁=57.5 ≫ σ₂=28.5), so a modal/reduced-order view is meaningful. (2) **Laplacian spectral
> clustering is the promising thread** — k-way clustering recovers the directory subsystems at
> **purity 0.82 (vs 0.66 majority baseline), NMI 0.41**, cleanly isolating `extract` (94%-pure
> cluster); signal is likely understated by the coarse flat-`core` label. This is the candidate to
> sharpen `summarize_subsystem` + a spectral-gap modularity metric. (3) **Authority-SVD (HITS)
> importance is not-yet** — complementary to PageRank (Jaccard@10=0.54) but its top mode is captured
> by the largest block of structurally-identical nodes (per-frontend `text`/`ev` helpers), so
> PageRank stays the better importance ranking; revisit only with node de-duplication. Full writeup:
> `research/06-spectral/FINDINGS.md`. Pursue decomposition; deprioritise authority-importance.
