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

## What would move it further (not done)
- De-duplicate structurally-identical nodes, then re-test authority-SVD.
- A finer ground-truth than directories (e.g. hand-labelled subsystems) to measure decomposition NMI
  without the flat-`core` penalty.
- Sparse iterative decomposition (scipy `svds`/`eigsh`) so it scales past the dense 10³-node PoC.
- POD over the **runtime coverage matrix** (`ingest_trace`) — co-activation modes, the idea's most
  control-theory-faithful form; needs a repo with ingested traces.
