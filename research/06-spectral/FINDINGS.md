# §6 — spectral / linear-algebraic analysis of the system matrix (first spike, 2026-07-02)

Proof-of-concept for IDEAS §6: does a spectral read of stitchgraph's node-adjacency matrix `A`
surface structure the shipped PageRank/reachability sweeps don't? numpy-only, dense decomposition on
one repo (stitchgraph's own `src/`, 839 nodes / 10 176 liveness edges — dense is fine at 10³ scale; a
real feature needs sparse iterative solvers). Scripts: `spectral_probe.py`, `spectral_cluster.py`.

The load-bearing question the §6 entry set: is spectral output *sensibly different AND useful* vs the
shipped PageRank? Answer after two pushes: **one thread is promising (Laplacian decomposition), one
is not-yet (authority-SVD importance), and one structural fact is confirmed (low-rank).**

## Confirmed: the call graph is effectively low-rank
Singular values of `A`: σ = [57.5, 28.5, 22.8, 21.4, 21.3, …]. σ₁ ≫ σ₂, and the **top-10 modes carry
69.5% of the matrix energy**. The dependency structure genuinely has a few dominant modes — the
premise that a reduced-order / modal view is meaningful holds.

## Promising: Laplacian spectral clustering recovers subsystems
k-way spectral clustering (normalised Laplacian, smallest-k eigenvectors → k-means) on the 830-node
giant component, scored against the directory tree as a ground-truth subsystem proxy:

| metric | spectral clustering | baseline |
|---|---|---|
| purity | **0.822** | 0.657 (majority class) |
| NMI | **0.406** | 0 (random) |

It cleanly isolates the extractor subsystem (one cluster = 94% `core/extract`) and splits `core` into
several coherent clusters (91–99% pure). **The signal is likely understated**: the flat `core` label
merges ~545 nodes across many real sub-subsystems (store/reach/operations/the structure_* frontends/
similar/graphdiff), so the clustering finding finer structure *inside* `core` earns no credit against
this coarse label. This is the on-brand §6 payoff — a candidate to sharpen `summarize_subsystem`
(automatic module boundaries) and an architecture-modularity number (spectral gap λ2 = 0.255 on this
repo). Advisory, as required.

## Not yet: authority-SVD (HITS) importance is dominated by duplication artifacts
Authority-SVD is *complementary* to PageRank (Jaccard@10 = 0.54, Spearman +0.45 — a genuinely
different ranking), BUT its top mode concentrates on the **largest block of structurally-identical
nodes** — here the ~12 per-frontend nested `text`/`ev`/`do` helpers (`_walk.text`, `_build_vfg.ev`,
…), which all share one connectivity profile and so load the dominant singular vector. Module-
aggregating authority just recovers "core is central" (core 8.6, everything else ≈ 0) — size + the
helper mode, not architectural centrality. **PageRank stays the better importance ranking**;
raw authority-SVD would need de-duplication (collapse structurally-identical nodes) or a degree
renormalisation before it competes. Verdict: "sensibly different" ✓, "useful as-is" ✗.

## Read for the backlog
- **Pursue** the Laplacian *decomposition* use-case (spectral clustering → subsystem boundaries +
  spectral-gap modularity metric). It's the thread with a real, measurable signal and a clear home
  (`summarize_subsystem`, cross-version drift via `graph_diff`).
- **Deprioritise** authority-SVD as an importance measure — PageRank already does importance better;
  the extra singular vectors are complementary but artifact-prone. Revisit only with node
  de-duplication.
- Consistent with the §2 finding: spectral measures are a **within-repo structural** tool (here,
  decomposition), never a cross-language "what does it do" signal.

---

# Full sweep — pushes A–D (2026-07-02, "research them all")

Ran every §6 candidate to a verdict. Scripts: `spectral_decompose.py` (A), `spectral_scale.py` (B),
`spectral_criticality.py` (C), `pod_coverage.py` (D).

## A — decomposition done right (`spectral_decompose.py`, 3 repos)
- **Spectral-summarize is the keeper.** k-way spectral clustering + per-cluster distinctive
  name-tokens (composing §6 clusters with §2/§3's semantic axis) auto-labels subsystems. On the
  well-structured repo (stitchgraph, purity 0.82 / NMI 0.41) the labels *name the real subsystems*:
  `source, fingerprint, parser, walk, vfg` / `resolve, resolver, route, edges` / `build, pdg, vfg`.
  This is the concrete capability — a `summarize_subsystem` upgrade.
- **Authority-SVD is repo-dependent, not a general importance measure.** Overlap of its top-10 with
  exported public API: requests **8/10** (mode 0), flask **5/10** (mode 2, not the dominant mode!),
  stitchgraph **0/10** (the 12 near-identical `structure_*` frontends hijack the top modes). So it
  can complement PageRank on hub-and-spoke codebases but can't replace it; de-dup didn't rescue it
  (the dominant modes aren't true graph-twins — `dedup` collapses only 185/839 nodes and not the
  helper block). **PageRank stays the importance ranking.**
- **Confound:** PyPI sdists bundle large test suites (requests giant = 439 nodes, 282 tests),
  wrecking the directory ground-truth (requests NMI 0.05). A real eval needs source-only / hand
  labels.

## B — the scaling design, validated (`spectral_scale.py`)
- **B1 matrix-free = dense:** a `LinearOperator` whose matvec STREAMS the edge list (no matrix
  formed) fed to scipy `svds` reproduces the dense top-6 singular values to **max Δ = 2.9e-3**.
- **B2 scale:** a synthetic planted graph of **100 000 nodes / 400 k edges** — **80 GB dense** — held
  as **6 MB sparse CSR**, decomposed (`eigsh`) + clustered in **1.5 s**, recovering the planted
  communities at **NMI 0.959**. The matrix-free / O(edges)-memory design in IDEAS §6 is correct and
  fast; it reuses the same edge-streaming pattern `find_stale` already uses.

## C — criticality & controllability (`spectral_criticality.py`, stitchgraph)
- **Articulation points = promising.** 43/830 cut-vertices (`graph_diff`, `OrmResolver.resolve`,
  `WebRouteResolver.resolve`, `build_app`) — a small, interpretable set of structural chokepoints
  whose removal fragments the graph; distinct from PageRank hubs. A real criticality signal for
  robustness triage / `impact_of`.
- **Current-flow closeness = redundant** with PageRank (Jaccard@10 = 0.67) and inherits the
  helper-noise — not additive.
- **Structural-controllability driver nodes = negative.** N_D = 348 (42% of the giant) — far too
  diffuse to be a "leverage set", and only 48% entry-point recall. Code call-graphs (many leaves/
  sinks) aren't the sparse controllable networks the theory targets. Drop this one.

## D — POD over runtime coverage (`pod_coverage.py`) — the novel win
Snapshot matrix = 8 test modules × 359 activated functions (each module run under coverage). POD
(SVD of the centred matrix) recovers interpretable **dynamic co-activation modes**:
- mode 0 (84% energy): the universal indexing substrate (`core/extract` — every test reindexes);
- mode 1 (7.7%): the **reachability subsystem** (`reachable_from`, `pagerank`, `transitive_fan_in`);
- mode 2 (3.2%): the **scan/resolution** mode (`_scan_calls.rec`, `_import_names.rec`);
- mode 3 (2.7%): the **cycle-detection** mode (`tarjan_scc.strongconnect`, `find_data_loops`).

Crucially, POD-over-coverage uses **runtime behaviour, not topology**, so it **sidesteps the §2
language-confound entirely** — it decomposes what actually executes together. It's the most
control-theory-faithful reading of the "components in a system" analogy and it produces meaningful
functional modes even at 8 snapshots. Best paired with `ingest_trace` (which already fuses coverage).

## Verdict for the backlog (what to pursue vs drop)
- **Pursue:** (1) **spectral-summarize** — labelled clustering → `summarize_subsystem`; (2)
  **articulation-point criticality** — chokepoint triage; (3) **POD over coverage** — dynamic
  subsystem modes, confound-free. All advisory, all matrix-free/streamable per push B.
- **Drop / deprioritise:** authority-SVD as importance (PageRank is better), current-flow closeness
  (redundant), structural-controllability drivers (too diffuse for code graphs).
- **Design settled:** never materialise the matrix — matrix-free Krylov over streamed edges,
  O(k·n) time / O(n) memory (push B); an optional `[spectral]` extra (scipy). Cardinal rule holds —
  all of this is advisory, never feeds `find_stale`.
