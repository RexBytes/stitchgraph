# stitchgraph v3.24.0 — release notes

**Roll-up release: the runtime-analysis (POD) toolkit lands on `main`.** `main` was last at **v3.1.0**;
this release brings it through everything built since. The v3.24.0 tip is byte-identical to v3.23.1 —
this is a release marker, not new code. Every capability below shipped incrementally and was gated,
per version, to two consecutive clean full-diversity adversarial panels on a frozen post-fix HEAD.

## Headline

**30 operations** (+ admin `reindex`), all advisory, read-only, and cardinal-safe (they never feed
`find_stale`; stitchgraph never executes your code). Three arcs since v3.1.0:

### 1. The layered code-property graph (§5b/§5c) — v3.2.0–v3.12.0
The intra-procedural **body matrix** across all **12 languages** (Python, JS/TS/TSX, Go, Rust, C/C++,
Java, C#, Ruby, PHP, Bash): a per-function value-flow fingerprint (order- and name-invariant,
Weisfeiler–Lehman). It became a first-class, drill-down **layer** — `get_matrix` drills one function
into its **call ↔ statement (PDG) ↔ expression (VFG)** graphs on demand. Powers
`find_similar(mode="structure")` and body-aware `graph_diff`.

### 2. Spectral analysis (§6) — v3.19.0–v3.20.1
- **`find_chokepoints`** — structural articulation points ranked by blast radius.
- **`find_subsystems`** — spectral clustering of the call graph into auto-labelled subsystems.

### 3. The POD toolkit — behavioural analysis from runtime coverage (§6) — v3.21.0–v3.23.1
POD = mean-centred SVD of the per-test co-activation matrix `M[test, function]`. This is the part of
stitchgraph that is genuinely **LLM-complementary** — grounded in runtime measurement + linear algebra,
not reproducible by reading source at any context size.

- **`find_modes`** — behavioural modes, intrinsic dimensionality, minimal covering test set.
- **`scaffold_coverage`** — generates a **sandboxed** (Docker / shell / CI) capture kit so you produce
  the coverage matrix in your own jail; stitchgraph only ever reads the inert JSON.
- Forward-looking query layer: **`select_tests`** (which tests to run for a change/changeset, runtime ×
  static blast radius), **`co_change`** (what code moves together), **`find_coupling`** (implicit
  coupling — co-run but no static edge), **`find_gaps`** (untested functions, live vs dead),
  **`test_order`** (fail-fast ordering), **`redundant_tests`**, **`find_core`** (always-on core),
  **`feature_map`** (mode ↔ code ↔ tests), **`find_outlier_tests`**, **`runtime_risk`** (churn ×
  behavioural centrality), **`coverage_drift`** (behavioural changelog across snapshots).

### Self-audit (v3.23.1)
stitchgraph analysed its own source with the full toolset (`research/12`) and found a real `scan`
`live_stub` false-positive — a Typer `@app.callback` with an idiomatic empty body flagged RED — plus
genuine coverage gaps. Fixed and re-gated through stitchgraph's own pipeline. The dogfood loop closed:
tool found a bug in itself, fix went back through its own gate.

## Format / compatibility

- **No store schema change**; every pre-existing operation is unchanged; all new behaviour is opt-in
  and **advisory**. The set-math coverage queries need **no numpy**; only `find_modes` /
  `find_subsystems` / `feature_map` / `find_outlier_tests` use numpy (scipy optional via `[spectral]`).
- The canonical coverage artifact is `stitchgraph-coverage-v1`
  (`{"tests": {"<test id>": ["<function id>", ...]}}`), language-agnostic.

## Cardinal rule (re-verified every panel)

The deeper layers and all POD/coverage ops are strictly advisory: `reach.py` / `find_stale` import
nothing from `structure*`/`similar`/`modes`/`coverage_query`; `import stitchgraph` pulls in none of
them (or numpy/scipy). A drill or a coverage query leaves `find_stale` **byte-identical**. Live code
can never be flagged dead.

## Quality gate

Full suite green (**2333 passed / 28 skipped** at the v3.23.1 tip), ruff + mypy clean, deterministic
deep-layer/POD output (byte-reproducible across `PYTHONHASHSEED`). Adversarial panels through **R286**
recorded in `release_readiness.json` + `REVIEW_HISTORY.md`; `scripts/readiness.py` verdict:
**RELEASABLE** (trailing clean streak ≥ 2, τ = 2).

## Dogfood & research

The `research/` tree documents stitchgraph used on itself and on external corpora — see
`research/README.md` (index), `research/10-pod-python/` (full self-run POD: 2338 tests × 792 functions
→ 10 modes recovering the real per-language architecture), `research/11-pod-roadmap.md`, and
`research/12-self-analysis.md` (the 30-op self-audit → v3.23.1 fixes).

## Tagging

Tagging is left to the maintainer. Per-version annotated tags (`v3.2.0` … `v3.23.1`) and this
`v3.24.0` roll-up tag are prepared for the branch tip; the automated environment can push branch refs
only, so tag pushes are done by the maintainer:

```
git tag -a v3.24.0 -m "v3.24.0: POD toolkit roll-up to main" <merge-or-tip-sha>
git push origin v3.24.0
```
