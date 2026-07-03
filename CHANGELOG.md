# Changelog

All notable changes to stitchgraph. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [3.28.0] — 2026-07-03

**The constant-memory release.** A field report falsified the core scale claim: Home
Assistant (pure Python, heavy homonym fan-out) OOM'd at ~7 GB with `streaming=True` while
PHP repos twice its size validated at 269 MB. Notes: `docs/RELEASE_NOTES_v3.28.0.md`.

### Fixed
- **The Python extractor never streamed**: its edge list was drained to the sink only after
  being fully materialised. It now drains per pass-2 file (INHERITS teed for the post-passes,
  override widening delegated to the store twin). A/B at 610 files: 412 MB linear → 43 MB
  flat; 8.64M edges peak at 50 MB.
- **`Store._propagate_overrides` was O(edges) in Python** (fetchall of every resolved
  CALLS/REFERENCES row + a seen-set) and re-OOM'd Home Assistant in the endgame after the
  index had streamed at a flat ~113 MB. Now symbol-scale Python + SQL-side scan/insert,
  byte-identical (streaming + incremental oracles). On HA's 16.15M-edge graph: ~160 s,
  113 MB peak.
- The streaming path's final holes tally used `len(unresolved_edges())` — an Edge object per
  hole; now `Store.unresolved_count()` (COUNT twin).

### Added
- A hard memory-regression gate: 450-file homonym+inheritance corpus (~1.2M edges) indexed in
  a subprocess under a 130 MB `RLIMIT_AS` cap, falsified in both directions against the
  pre-fix code. Constant-memory is now a tested invariant, not a claim.

## [3.27.1] — 2026-07-03

**The second dogfood patch** — v3.27.0 run on itself (`research/15-dogfood-v3.27.md`), the
controlled before/after of the D2 dedup. Notes: `docs/RELEASE_NOTES_v3.27.1.md`.

### Fixed
- `structure_common.parse_tree` shipped in v3.26.0 as dead code: the stage-2 transformation
  added the shared walk guard but never wired the nine `_walk` functions to call it (and
  `ruff --fix` removed the unused imports, hiding the slip from the output-equivalence gates —
  dead code has no outputs). Caught by `find_stale`, corroborated by `find_gaps`. Now guards all
  nine walk entries as intended; the seven remaining `_walk`-local `text` helpers delegate to
  the shared `node_text`.

### Verified by the run (no action)
- POD invariants across the dedup: intrinsic dimensionality **27 → 27**; `coverage_drift`
  (first real cross-release use) narrates exactly the intended moves — lost: the nine deleted
  per-language copies; gained: `structure_common.*`.
- The three `scan` oranges remain the same verified-deliberate constructs; holes 0;
  `runtime_risk` stable post-fix.

## [3.27.0] — 2026-07-03

**Docs release: the README becomes a front door.** No code change.

### Changed
- **README rewritten** for readers who don't live in this repo: a lead introduction (what it is,
  the two design commitments), install matrix, a five-minute human quickstart (every command
  verified against a live index), the operations as ask→operation tables, the behavioural toolkit
  with the sandboxed capture workflow, an **agents section** (MCP launch + Claude Desktop/Code
  config + rules of engagement distilled from `AGENTS.md`), the trust model, languages, and scale.
  The version-archaeology status section moved out (it lives in `docs/STATUS.md` / `CHANGELOG.md`).

### Added
- `docs/RELEASE_NOTES_v3.26.0.md` (the D2 dedup notes, missed at release time) and
  `docs/RELEASE_NOTES_v3.27.0.md`.

## [3.26.0] — 2026-07-03

**D2 dedup** (review 2026-07-03 finding D2; `research/14` showed the nine tree-sitter
body-matrix frontends as their own 329-node subsystem, and F5a/F5b proved the hand-sync
leaks fixes). Behaviour-preserving; follows v3.25.1 (the dogfood patch).

### Changed
- **Stage 1 — shared leaf helpers** (`core/structure_common.py`): the parser factory, the
  comment-safe child selectors (`_nc`/`_first`/`_last`), and the operator-token reader
  (`_op_text`), previously duplicated across all nine `structure_*.py` frontends. Frontends keep
  thin delegations; Java and Rust pass their grammars' split comment-type tuples.
- **Stage 2 — shared builder scaffolding**: every `_build_vfg`'s opening block (graph/env/free/
  `freevar`) and every `_build_pdg`'s (nodes/edges/counter/`new_id`) now come from
  `vfg_state()`/`pdg_state()` factories, plus `node_text` and the `parse_tree` walk guard; the
  R205-hardened sorted data-edge emission (previously nine copies, comment and all) is the single
  shared `data_from`. Call sites unchanged.
- **Stage 4 — one corpus iterator**: `similar.py`'s nine near-identical per-language
  `_*_fn_fingerprints` iterators are one generic `_ts_fn_fingerprints(store, mod)` + partials.
- Net **−387 lines**. Stage 3 (unifying the per-language `process` statement dispatchers) is
  deliberately NOT done — that code is semantic per-grammar knowledge; a config-object DSL would
  be worse than the duplication. The per-language `ev`/`do` expression mappings are untouched.
- Gates: byte-identical fingerprint/VFG/PDG outputs over a 9-language corpus (which caught a
  first-draft error in Rust's comment tuple — working as designed) + the full 1,618-test oracle
  battery + ruff/mypy.

## [3.25.1] — 2026-07-03

**The dogfood patch** — v3.25.0 run on its own source (`research/14-dogfood-v3.25.md`) validated
the review fixes live and caught one defect. Notes: `docs/RELEASE_NOTES_v3.25.1.md`.

### Fixed
- `runtime_risk` silently returned zero hotspots on src-layout repos: coverage fids are
  indexed-root-relative while git churn paths are repo-relative; the join now translates through
  the same `_git_path_mapper` as `risk`.


## [3.25.0] — 2026-07-03

**The external-review hardening release** — a full-repo external review
(`docs/REVIEW_FINDINGS_2026-07-03.md`, every finding recorded with status) fixed 1 CRITICAL,
6 HIGH, 8 MEDIUM, 9 LOW. Full notes: `docs/RELEASE_NOTES_v3.25.0.md`. No schema change.

### Fixed
- **`reindex` on an invalid root refuses instead of wiping the existing index** (was: a typo'd
  path executed `DELETE FROM nodes/edges` and returned ok/1.0).
- **Adapters refuse a missing/never-indexed DB** instead of silently creating an empty one and
  answering queries at full confidence (CLI + MCP + report; only `reindex` may create).
- **`find_modes.intrinsic_dimensionality` uses the full spectrum** (was: truncated to the top-16
  energy, silently saturating at 16); sparse path reports a flagged lower bound when applicable;
  `feature_map` energy fractions share the true denominator.
- **POD ops normalize test ids** (`|run`/`|setup` phases, `[param]` rows) — no more inflated test
  counts, spurious redundant pairs, or non-runnable ids in `minimal_test_set`.
- **Body matrix:** JS `for (let i = 0; …)` binds its loop variable; Bash multi-command `if`
  conditions are no longer truncated (VFG + PDG); Python walrus binds; PHP `foreach` key/value
  pair binds both names (VFG + PDG); the Python PDG no longer reads through lambda bodies.
- **tree-sitter references no longer bind to MODULE nodes** (the `_ref_edges` invariant; imports
  keep module resolution) — restores incremental == full convergence for tree-sitter languages.
- **Streaming:** the AUTO-stream probe prunes `SKIP_DIRS` (a `.venv` no longer forces streaming);
  a failed extractor mid-stream can no longer leave phantom edges (orphan sweep).
- `watch` shares the extractors' `SKIP_DIRS`; `iter_resolved` skips BLOB-corrupt rows; greedy
  minimal-cover loops prune exhausted rows; WL fingerprint hashes widen to 64-bit.

### Added
- **`stitchgraph-mcp` console script** with `--db` / `STITCHGRAPH_DB` (the MCP server was
  previously unpointable at a database).
- File-backed stores open in **WAL + busy_timeout(10s)** (watch + MCP on one DB no longer hits
  `database is locked`).
- `docs/REVIEW_FINDINGS_2026-07-03.md` — the review record; deferred items D1–D5 documented.
- Truncation markers ("… N more") in text render and report output.
- An end-to-end FastMCP `call_tool` test; the MCP build test can fail (no longer skip-on-any-error);
  the `dev` extra includes `mcp`.

### Changed (behavioural — see the release notes' ⚠️ section)
- `intrinsic_dimensionality` can exceed the reported mode count (the R273 clamp enforced the
  saturation bug and is removed; R272's zero-variance guard stays).
- `find_outlier_tests`: smoke detection keys on row **breadth** (solver-independent); payload field
  `mode1_load` → `breadth`.
- CLI exits **2** on operational failures (missing/unopenable `--db`); advisory refusals stay 0.
- POD `meta["tests"]` counts logical (normalized) tests.
- `test_order`/`greedy_order` gain ties pick the lowest test id (matching `find_modes`).

## [3.24.0] — 2026-07-02

**Release marker — the POD-toolkit line merges to `main`.** No new code beyond v3.23.1 (the tip is
byte-identical); this is the consolidated release that brings `main` (last at v3.1.0) up through the
full runtime-analysis toolkit. Everything below already shipped and was gated per-version — see
`docs/RELEASE_NOTES_v3.24.0.md` for the roll-up. Highlights since v3.1.0:

- **§5b/§5c — the layered code-property graph** (v3.2.0–v3.12.0): the intra-procedural body matrix
  across all 12 languages + the call ↔ statement ↔ expression layers, drill-down via `get_matrix`.
- **§6 spectral analysis** (v3.19.0–v3.20.1): `find_chokepoints`, `find_subsystems`.
- **§6 POD toolkit — behavioural analysis from runtime coverage** (v3.21.0–v3.23.0): `find_modes` +
  `scaffold_coverage`, then the forward-looking query layer `select_tests`, `co_change`,
  `find_coupling`, `find_gaps`, `test_order`, `redundant_tests`, `find_core`, `feature_map`,
  `find_outlier_tests`, `runtime_risk`, `coverage_drift`. **30 operations + admin `reindex`.**
- **Self-audit** (v3.23.1): stitchgraph turned on itself (`research/12`) found and fixed a `scan`
  `live_stub` false-positive on decorator-registered callbacks.

All advisory, read-only, cardinal-safe; every version reached two consecutive clean full-diversity
adversarial panels on a frozen post-fix HEAD (`release_readiness.json`, `REVIEW_HISTORY.md`).

## [3.23.1] — 2026-07-02

**Precision fix from the v3.23.0 self-analysis dogfood (`research/12`).** Turning the full toolset on
stitchgraph's own source surfaced a `scan` false-positive: a function registered via a **call/attribute
decorator** (`@app.callback(...)`, `@app.route("/")`, `@foo.register`) with an idiomatic empty
(`pass`/`…`) body was RED-flagged as an unimplemented `live_stub`. The decorator supplies the behaviour,
so the empty body is intentional. `_is_stub` now excludes such registered callbacks; a bare `pass`/`…`
(no decorator or a bare-name decorator like `@property`) and any explicit `raise NotImplementedError`
(even when decorated) are still stubs. Also added tests for two genuine coverage gaps `find_gaps` found
(`Edge.to_dict`, `Edge.resolved`). No API change.

## [3.23.0] — 2026-07-02

**POD toolkit completion: eight forward-looking runtime-analysis operations (§6).** Building on the
co-activation matrix (`find_modes`) and the v3.22.0 query ops, this fills out the roadmap
(`research/11-pod-roadmap.md`). All advisory, read-only, cardinal-safe; the set-math ones need no numpy.

Pure set-math over the matrix (`core/coverage_query.py`):
- **`find_gaps`** — functions no test executed, split `untested_live` (reachable → real coverage gap,
  write a test) vs `untested_dead` (unreachable → corroborates `find_stale`). The runtime complement to
  `find_stale`: static says "no one *can* reach it", coverage says "no test *did*".
- **`test_order`** — fail-fast ordering: each next test adds the most new coverage; the prefix is a
  minimal cover, the tail a fast-tier candidate list.
- **`redundant_tests`** — groups of tests with an identical coverage profile (a consolidation *review
  aid* — parametrized tests share a profile but test different inputs; never an auto-delete).
- **`find_core`** — the always-on core: functions executed by the most tests (highest behavioural blast
  radius); runtime companion to `find_chokepoints`.
- **`runtime_risk`** — git churn × behavioural centrality: files that change often *and* are exercised
  by many behaviours; sharper hotspots than `risk`'s churn × static-centrality.
- **`coverage_drift`** — functions that gained/lost test exposure between two coverage snapshots; a
  behavioural changelog to pair with `graph_diff`.
- **`select_tests`** now also accepts a **changeset** (comma-separated symbols — a PR's touched
  functions) and unions their tests.

POD/SVD (`core/modes.py`, numpy):
- **`feature_map`** — per behavioural mode: its implementing functions (full ids) × the files they span
  × the tests that most express it. The actionable feature ↔ code ↔ test map.
- **`find_outlier_tests`** — tests the mainstream modes reconstruct poorly (mode-space residual):
  unique-behaviour tests (keep) vs everything-touching smoke (high mode-1 load).

All exposed as library API + CLI + MCP. Total operations: **30 + admin `reindex`**. Dogfooded on
stitchgraph's own 2315×764 coverage (e.g. `runtime_risk` ranks the tree-sitter/Python extractors and
the store as top hotspots; `find_core` surfaces `Store.__init__`).

## [3.22.0] — 2026-07-02

**Forward-looking POD-based operations: `select_tests`, `co_change`, `find_coupling` — turn the runtime
co-activation matrix from advisory analysis into actionable, change-oriented queries (§6).** All three
are pure set math over the same inert `stitchgraph-coverage-v1` matrix `find_modes` consumes — **no
numpy**, advisory, read-only (stitchgraph never executes your code, never touches the graph). Backed by
a new `core/coverage_query.py`.

- **`select_tests(name, coverage)` — "which tests should I run for this change?"** Fuses the tests that
  *actually executed* the symbol (runtime ground truth) with the tests that *statically reach* it (like
  `impact_of`), classifying the union into `both`, `runtime_only` (ran it via a path the call graph
  missed — dynamic dispatch / framework), and `static_only` (reachable but never run — a coverage gap).
- **`co_change(name, coverage)` — "what code moves together / what implements this outcome?"** The
  functions whose per-test activation most resembles the symbol's (cosine over the test columns) — its
  behavioural neighbourhood. The runtime complement to static `get_callers`/`get_callees`.
- **`find_coupling(coverage)` — implicit coupling.** Function pairs that co-activate strongly yet have
  **no static edge** between them: dependencies (shared state, dispatch, protocol, or a common caller)
  the call graph cannot see. The runtime∖structure gap; `cross_file` pairs are the interesting ones.

Test-id keys are normalised to graph node-id convention (pytest `[param]` and coverage.py `|phase`
suffixes stripped; `file::Class::method` → `file::Class.method`), so runtime and static ids line up.
Dogfooded on stitchgraph's own 2315×764 coverage. All exposed as library API + CLI (`select-tests`,
`co-change`, `find-coupling`) + MCP. Total operations: 22 + admin `reindex`.

## [3.21.0] — 2026-07-02

**New advisory operations: `find_modes` + `scaffold_coverage` — behavioural analysis from runtime
coverage (§6 spectral research, win 3 — "POD over runtime coverage").** `find_modes` decomposes a
codebase's *runtime* behaviour via **POD** (mean-centred SVD of the per-test co-activation matrix
`M[test, function]`): it returns the ranked **behavioural modes** (function groups that fire together —
routing, sessions, …), the **intrinsic dimensionality** (modes to 90% energy), a **minimal test set**
that covers every executed function, and a redundant-test-pair count. It is the *runtime* complement to
the static `find_subsystems`, and — unlike static analysis — **language-agnostic**: it consumes a
canonical `stitchgraph-coverage-v1` JSON (`{test_id: [function_id, …]}`) produced by any language's
per-test coverage tool. Backed by a new `core/modes.py` (numpy SVD; sparse `svds` via the optional
`[spectral]` extra for large matrices). **stitchgraph never executes your code** — it only reads the
inert matrix. `scaffold_coverage` **generates a sandboxed capture kit** (`core/coverage_scaffold.py`)
so you can produce that matrix safely: per detected language it writes three interchangeable recipes —
**Docker** (no-network, non-root, read-only rootfs, capped), **plain shell**, and a **CI** snippet —
plus a README and the canonical-format spec; Python is turnkey (coverage.py `--cov-context=test` +
an AST line→function converter), JS/Go/Rust/Java ship a wired template. It writes helper files only
(like `report`), never touches source, never runs anything. Both advisory and read-only — never feed
`find_stale`. Auto-exposed on the library API, CLI, and MCP. Backward-compatible → MINOR.

## [3.20.1] — 2026-07-02

**Fix: `get_callers` / `get_callees` name resolution gives precise, actionable refusals.** Surfaced by
the dogfood build experiment (`research/07-dogfood-build`, round 2): both ops refused an unresolvable
name with the same message — *"'X' is not a unique symbol in the index"* — whether the name was
**unknown** (zero matches) or genuinely **ambiguous** (multiple definitions), and never surfaced the
candidates. Now they distinguish the two: an unknown name reports *"no symbol named 'X' in the index"*
(matching `find_symbol`), and an ambiguous one lists the candidate ids (*"'X' is ambiguous across N
definitions: a.py::Svc.save, b.py::Svc.save; pass a qualified id …"*) so the caller can re-issue with a
qualified `Type.method` or full `path::qual` id. Still a clean `ok=False` Result (never raises);
qualified/full ids resolve as before. Message/usability only — no API change → PATCH.

## [3.20.0] — 2026-07-02

**New advisory operation: `find_subsystems` — automatic subsystem decomposition (§6 spectral
research, win 2).** The second §6 result graduates in. `find_subsystems` partitions a repo's
call/reference graph into its **natural subsystems** by spectral clustering of the graph Laplacian,
and auto-labels each cluster with the identifier tokens that most distinguish it (a
"spectral-summarize"). It's the *structural* complement to the semantic `find_similar` /
`summarize_subsystem`: it **discovers** the module boundaries rather than describing a scope you name.
The cluster count is auto-selected from the spectral eigengap (or set via `k`). Backed by a new
`core/spectral.py` (normalised-Laplacian embedding + deterministic k-means++ + distinctive-token
labelling). Numerics: numpy-only out of the box (dense eigendecomposition, fine for typical repos,
capped at 2500 giant-component nodes); the optional **`[spectral]` extra (scipy)** adds a sparse
ARPACK solver that removes the cap and scales to large graphs — matrix-free in spirit (top-k
eigenvectors only). Deterministic — same store → same partition, run to run and across processes: the
reproducible dense (LAPACK) solver is preferred for every giant component within the cap (even when
scipy is installed), and the above-cap sparse path starts from a fixed generic vector plus a
deterministic symmetry-breaking term so it stays reproducible even on degenerate spectra (seeded
k-means++ throughout). **Advisory and read-only — like `orient`/`risk` it never feeds `find_stale`**
(re-verified byte-identical).
Auto-exposed on the library API, CLI, and MCP. Backward-compatible → MINOR.

## [3.19.0] — 2026-07-02

**New advisory operation: `find_chokepoints` — structural criticality (§6 spectral research,
promoted).** The first result from the §6 "system-matrix" research thread graduates into the package.
`find_chokepoints` returns the **articulation points** (cut vertices) of the call/reference graph —
nodes whose removal disconnects the graph — each ranked by its **blast radius**, i.e. how many nodes
get cut off from the main body if it fails. This is a robustness / "dangerous to touch" signal
*distinct* from `orient`'s hub ranking (which measures centrality, not cut-vertex-ness): a chokepoint
can have modest fan-in/out yet still be the sole bridge between two subsystems. Backed by a new
`reach.articulation_points` (one Tarjan DFS pass, subtree sizes computed inline, O(V+E),
deterministic; recursion-limit guarded like the shared SCC core). Code entities only (Module / pseudo
nodes excluded, as in `orient`/`scan`). Auto-exposed on the library API, CLI, and MCP server from the
operation registry. **Advisory and structural only — like hubs, cycles and god objects it never feeds
`find_stale`** (the cardinal rule is a liveness property; re-verified byte-identical). No new
dependency. Backward-compatible → MINOR.

## [3.18.0] — 2026-07-02

**The STATEMENT layer learns Bash — the §5c sweep is COMPLETE (language 10, all body-matrix
languages).** After Python (v3.9.0), the JS family (v3.10.0), Go (v3.11.0), Rust (v3.12.0), C/C++
(v3.13.0), Java (v3.14.0), C# (v3.15.0), Ruby (v3.16.0), and PHP (v3.17.0), v3.18.0 adds Bash to the
program-dependence-graph layer: `structure_bash.pdg_source` builds a per-function PDG (statement
nodes + a synthetic `ENTRY`; control `C` / data `D` sequential-reaching-def edges) and
`get_matrix(layer="statement")` drills it. Bash is the **command-oriented** outlier and has **no
declared parameter list** (shell functions read positional `$1…` as free variables), so `ENTRY`
carries no params — the same as the value-flow builder, which seeds no `PARAM` nodes. Its read/write
projection (`collect`/`bind_place`) mirrors the VFG's `ev`/`bind` node-for-node: a **literal command
name is a free callee, never a variable read** (a *dynamic* `$cmd`/`$(…)` name reads its expansions),
a bare `local x` declaration binds no value, `for` loop variables bind, and `$x`/`${x}`/`$(( … ))`/
string / here-string interpolation holes are read. A new white-box VFG-vs-PDG differential oracle
(`tests/oracles/test_pdg_bash_vfg_differential.py`) cross-checks the statement- and expression-layer
builders. `get_matrix(layer="statement")` now dispatches **every body-matrix language** (Python + the
JS family + Go + Rust + C/C++ + Java + C# + Ruby + PHP + Bash); the layer-level "unsupported language"
refusal is now reachable only for a foreign file extension. On-demand, advisory, never feeds liveness
(cardinal rule re-verified). Backward-compatible (no schema change, default behavior unchanged) →
MINOR.

## [3.17.0] — 2026-07-01

**The STATEMENT layer learns PHP (§5c sweep, language 9).** After Python (v3.9.0), the JS family
(v3.10.0), Go (v3.11.0), Rust (v3.12.0), C/C++ (v3.13.0), Java (v3.14.0), C# (v3.15.0), and Ruby
(v3.16.0), v3.17.0 adds PHP to the program-dependence-graph layer: `structure_php.pdg_source` builds
a per-function PDG (statement nodes + a synthetic `ENTRY` carrying params; control `C` / data `D`
sequential-reaching-def edges) and `get_matrix(layer="statement")` drills it. PHP is
**statement-oriented** (like Go/C/C++/Java/C#): its read/write projection (`collect`/`bind_place`)
mirrors the value-flow builder (`ev`/`bind`) node-for-node. A member/property NAME and a call's method
NAME carry no value read (the object + args do); a *dynamic* method call `$o->$v()` reads `$v` in both
builders (genuine dynamic dispatch); `Foo::$x` / `Foo::CONST` scoped accesses are opaque free
variables; `foreach` loop vars and `list()` destructuring bind, while a `$k => $v` `pair` binds
nothing (mirroring the VFG gap); string/heredoc interpolation holes are read; closures / arrow
functions are opaque NESTED leaves; `static $x = init` locals bind. A new white-box VFG-vs-PDG
differential oracle (`tests/oracles/test_pdg_php_vfg_differential.py`) cross-checks the statement- and
expression-layer builders. `get_matrix(layer="statement")` now dispatches Python + the JS family + Go
+ Rust + C/C++ + Java + C# + Ruby + PHP; the last tree-sitter language (Bash) is the rest of the sweep
and refuses with a supported-set message. On-demand, advisory, never feeds liveness (cardinal rule
re-verified). Backward-compatible (no schema change, default behavior unchanged) → MINOR.

## [3.16.0] — 2026-07-01

**The STATEMENT layer learns Ruby (§5c sweep, language 8).** After Python (v3.9.0), the JS family
(v3.10.0), Go (v3.11.0), Rust (v3.12.0), C/C++ (v3.13.0), Java (v3.14.0), and C# (v3.15.0), v3.16.0
adds Ruby to the program-dependence-graph layer: `structure_ruby.pdg_source` builds a per-function
PDG (statement nodes + a synthetic `ENTRY` carrying params; control `C` / data `D`
sequential-reaching-def edges) and `get_matrix(layer="statement")` drills it. Ruby is
**expression-oriented** (like Rust): control constructs (`if`/`case`/`while`/`for`) become control
nodes in statement position but FOLD their reads into the enclosing statement in value position
(`x = if c then a else b end`). A call's method name carries no value read (receiver + args do);
`self`/`@ivar` route through free variables; blocks / `do…end` / lambdas are opaque NESTED leaves;
`for`/rescue bindings are stores; string-interpolation holes are read. A new white-box VFG-vs-PDG
differential oracle (`tests/oracles/test_pdg_ruby_vfg_differential.py`) cross-checks the statement-
and expression-layer builders. `get_matrix(layer="statement")` now dispatches Python + the JS family
+ Go + Rust + C/C++ + Java + C# + Ruby; the remaining tree-sitter languages (PHP, Bash) are the rest
of the sweep and refuse with a supported-set message. On-demand, advisory, never feeds liveness
(cardinal rule re-verified). Backward-compatible (no schema change, default behavior unchanged) → MINOR.

## [3.15.0] — 2026-07-01

**The STATEMENT layer learns C# (§5c sweep, language 7).** After Python (v3.9.0), the JS family
(v3.10.0), Go (v3.11.0), Rust (v3.12.0), C/C++ (v3.13.0), and Java (v3.14.0), v3.15.0 adds C# to the
program-dependence-graph layer: `structure_csharp.pdg_source` builds a per-function PDG (statement
nodes + a synthetic `ENTRY` carrying params; control `C` / data `D` sequential-reaching-def edges)
and `get_matrix(layer="statement")` drills it. Type positions, the member name in a `.` access, and
pattern/label selectors carry no value read; member/element assignment targets are reads, not stores;
`foreach` loop vars, `using` resources, and tuple deconstructions bind; expression-bodied members
(`=> expr`), interpolated-string holes, and switch expressions/patterns are modelled. A new white-box
VFG-vs-PDG differential oracle (`tests/oracles/test_pdg_csharp_vfg_differential.py`) cross-checks the
statement- and expression-layer builders. `get_matrix(layer="statement")` now dispatches Python + the
JS family + Go + Rust + C/C++ + Java + C#; the remaining tree-sitter languages (Ruby, PHP, Bash) are
the rest of the sweep and refuse with a supported-set message. On-demand, advisory, never feeds
liveness (cardinal rule re-verified). Backward-compatible (no schema change, default behavior
unchanged) → MINOR.

## [3.14.0] — 2026-07-01

**The STATEMENT layer learns Java (§5c sweep, language 6).** After Python (v3.9.0), the JS family
(v3.10.0), Go (v3.11.0), Rust (v3.12.0), and C/C++ (v3.13.0), v3.14.0 adds Java to the
program-dependence-graph layer: `structure_java.pdg_source` builds a per-function PDG (statement
nodes + a synthetic `ENTRY` carrying params; control `C` / data `D` sequential-reaching-def edges)
and `get_matrix(layer="statement")` drills it. Type positions, a call's method name, field/member
names, statement labels, and switch case selectors carry no value read; field/array assignment
targets are reads, not stores; enhanced-`for` loop vars, try-with-resources, and caught exceptions
bind. A new white-box VFG-vs-PDG differential oracle (`tests/oracles/test_pdg_java_vfg_differential.py`)
cross-checks the statement- and expression-layer builders. `get_matrix(layer="statement")` now
dispatches Python + the JS family + Go + Rust + C/C++ + Java; the remaining tree-sitter languages
(C#, Ruby, PHP, Bash) are the rest of the sweep and refuse with a supported-set message. On-demand,
advisory, never feeds liveness (cardinal rule re-verified). Backward-compatible (no schema change,
default behavior unchanged) → MINOR.

## [3.13.0] — 2026-07-01

**The STATEMENT layer learns C/C++ (§5c sweep, language 5).** After Python (v3.9.0), the JS family
(v3.10.0), Go (v3.11.0), and Rust (v3.12.0), v3.13.0 adds C/C++ to the program-dependence-graph
layer: `structure_cpp.pdg_source` builds a per-function PDG (statement nodes + a synthetic `ENTRY`
carrying params; control `C` / data `D` sequential-reaching-def edges) and `get_matrix(layer=
"statement")` drills it. Type positions, `goto`/labels, field names, and switch case values carry no
value read; place/deref/index/member assignment targets are reads, not stores. A new white-box
VFG-vs-PDG differential oracle (`tests/oracles/test_pdg_cpp_vfg_differential.py`) cross-checks the
statement- and expression-layer builders. `get_matrix(layer="statement")` now dispatches Python + the
JS family + Go + Rust + C/C++; the remaining tree-sitter languages (Java, C#, Ruby, PHP, Bash) are
the rest of the sweep and refuse with a supported-set message. On-demand, advisory, never feeds
liveness (cardinal rule re-verified). Backward-compatible (no schema change, default behavior
unchanged) → MINOR.

## [3.12.0] — 2026-07-01

**The STATEMENT layer learns Rust (§5c sweep, language 4).** After Python (v3.9.0), the JS family
(v3.10.0), and Go (v3.11.0), v3.12.0 adds Rust to the program-dependence-graph layer.
Backward-compatible (no schema change, default behavior unchanged) → MINOR.

### Added
- **`structure_rust.pdg_source`** — a Rust function's program-dependence graph. Rust is
  expression-oriented, so control-flow *expressions* (`if`/`match`/`loop`/`while`/`for`) in statement
  position become control nodes; in value position (`let y = if …`) they fold into the enclosing
  statement's reads (mirroring how the Python PDG folds walrus/comprehensions). ENTRY seeds params +
  `self`; `if let`/`while let` bind their pattern and read their scrutinee; `for` binds its pattern
  and reads the iterator; `match` descends each arm body; closures/nested fns are opaque `NESTED`.
  Type positions (`type_identifier` / `*_type` / scoped paths) carry no value read, so `let x: T = …`
  and `x as T` never leak a false dependency. Keyed identically to `fingerprint_source`/`vfg_source`.
- **`get_matrix(layer="statement")` now covers Rust** — dispatches `.py` → `structure`, js/ts/tsx →
  `structure_js`, `.go` → `structure_go`, `.rs` → `structure_rust`. Other languages refuse with a
  supported-set message.

### Notes
- A structural **approximation** (sequential reaching-def, no SSA/alias analysis), **advisory** —
  never persisted, never feeds `find_stale`. Remaining tree-sitter languages (C/C++, Java, C#, Ruby,
  PHP, Bash) are the rest of the sweep.

### Guarantees
- Advisory — the STATEMENT layer never feeds `find_stale`/liveness (a test pins a Rust drill-down
  cannot change `find_stale`).
- **Deterministic output** — byte-reproducible across processes (sorted edge emission).

### Quality gate
- ruff + mypy clean; full suite passing. New: `tests/oracles/test_pdg_source_rust_layer.py`
  (key-parity, well-formed C/D graph, reorder-invariance, dependence sensitivity, type-position
  safety, Rust-specific constructs [if-let/while-let/match-guards/labeled-loop/closures/macros/`?`],
  never-raises, cross-`PYTHONHASHSEED` determinism) + a Rust drill case in `tests/test_layer_matrix.py`.
  Two-round full-diversity adversarial panel clean.

## [3.11.0] — 2026-07-01

**The STATEMENT layer learns Go (§5c sweep, language 3).** After Python (v3.9.0) and the JS family
(v3.10.0), v3.11.0 adds Go to the program-dependence-graph layer. Backward-compatible (no schema
change, default behavior unchanged) → MINOR.

### Added
- **`structure_go.pdg_source`** — a Go function's program-dependence graph, built from the
  tree-sitter tree and mirroring `structure.pdg_source` (Python) / `structure_js.pdg_source`:
  statement nodes (+ a synthetic `ENTRY` carrying the parameters and receiver), `C` (control) and
  `D` (data: sequential reaching-def) edges. Go constructs covered: `if`/`for` (for-clause, `range`
  binding, bare condition), expression/type `switch` (case bodies + values), `select` (comm cases),
  `defer`/`go`/labeled statements, `:=`/`var`/`const`/assignment (incl. compound `op=` and
  multi-value `a, b := …`), `++`/`--`, channel send; selector/index assignment targets read their
  object (no false store). `func_literal` is an opaque `NESTED` leaf. Keyed identically to
  `fingerprint_source`/`vfg_source`.
- **`get_matrix(layer="statement")` now covers Go** — dispatches `.py` → `structure`, js/ts/tsx →
  `structure_js`, and `.go` → `structure_go`. Other languages refuse with a supported-set message.

### Notes
- A structural **approximation** (sequential reaching-def, no SSA/alias analysis), **advisory** —
  computed on demand, never persisted, never feeds `find_stale`. Remaining tree-sitter languages
  (Rust, C/C++, Java, C#, Ruby, PHP, Bash) are the rest of the sweep.

### Guarantees
- Advisory — the STATEMENT layer never feeds `find_stale`/liveness (a test pins that a Go drill-down
  cannot change `find_stale`).
- **Deterministic output** — `pdg_source`'s edges and `get_matrix`'s `cells` are byte-reproducible
  across processes (edges emitted in sorted order, not `set`/`PYTHONHASHSEED` order).

### Quality gate
- ruff + mypy clean; full suite passing. New: `tests/oracles/test_pdg_source_go_layer.py`
  (key-parity, well-formed C/D graph, reorder-invariance, dependence sensitivity, Go-specific
  statements [range/type-switch/select/defer/goroutine], never-raises, cross-`PYTHONHASHSEED`
  determinism) + a Go statement-drill case in `tests/test_layer_matrix.py`. Two-round full-diversity
  adversarial panel clean.

## [3.10.0] — 2026-07-01

**The STATEMENT layer learns the JS family — js/ts/tsx (§5c sweep, language 2).** v3.9.0 shipped the
program-dependence-graph (PDG) layer for Python; v3.10.0 begins sweeping it to the tree-sitter
languages, starting with the JS family. Backward-compatible (no schema change, default behavior
unchanged) → MINOR.

### Added
- **`structure_js.pdg_source`** — a JS/TS/TSX function's program-dependence graph, built from the
  tree-sitter tree and mirroring `structure.pdg_source` (Python): statement nodes (+ a synthetic
  `ENTRY` carrying the parameters), `C` (control: nested-under-a-header) and `D` (data: a sequential
  reaching-def) edges. Nested functions are opaque `NESTED` leaves; try/catch/finally, switch cases,
  and `for…of`/`for…in` bindings are covered. The STATEMENT-layer companion to
  `fingerprint_source`/`vfg_source`, keyed identically (shared `_walk`).
- **`get_matrix(layer="statement")` now covers the JS family** — dispatches `.py` → `structure`
  and `.js/.jsx/.mjs/.cjs/.ts/.mts/.cts/.tsx` → `structure_js`. Other languages refuse cleanly with a
  message naming the supported set (Python + JS family).

### Notes
- Like the Python PDG, this is a structural **approximation** (sequential reaching-def, no SSA/alias
  analysis) and **advisory** — computed on demand, never persisted, never feeds `find_stale`. The
  remaining tree-sitter languages (Go, Rust, C/C++, Java, C#, Ruby, PHP, Bash) are a future sweep.

### Guarantees
- Advisory — the STATEMENT layer never feeds `find_stale`/liveness (a test pins that a JS drill-down
  cannot change `find_stale`).
- **Deterministic output** — `pdg_source`'s edges and `get_matrix`'s `cells` are byte-reproducible
  across processes (edges emitted in sorted order, not `set`/`PYTHONHASHSEED` order).

### Quality gate
- ruff + mypy clean; full suite passing. New: `tests/oracles/test_pdg_source_js_layer.py`
  (key-parity, well-formed C/D graph, **reorder-invariance**, dependence-change sensitivity,
  never-raises on TS/exotic input, try/switch/for-of, cross-`PYTHONHASHSEED` determinism) + a JS
  statement-drill case and an unsupported-language refusal in `tests/test_layer_matrix.py`.
  Two-round full-diversity adversarial panel clean.

## [3.9.0] — 2026-07-01

**The STATEMENT layer — drill into a function's program-dependence graph (§5c phase 2).** v3.8.0
added the call↔expression drill-down; v3.9.0 adds the middle layer: the **PDG** (nodes = statements,
edges = control + data dependence). Promotes the validated `research/03-pdg/` prototype. Python-only
so far (deep stdlib `ast`), on demand, advisory. Backward-compatible (no schema change, default
behavior unchanged) → MINOR.

### Added
- **`structure.pdg` / `pdg_source`** — a function's program-dependence graph: statement nodes (+ a
  synthetic `ENTRY` carrying the parameters), `C` (control: nested-under-a-header) and `D` (data: a
  def reaching a later use, a sequential reaching-def approximation) edges. The STATEMENT-layer
  companion to `fingerprint_source`/`vfg_source`, keyed identically (shared `_walk_functions`).
- **`get_matrix(..., layer="statement")`** — drills a SINGLE Python function's PDG, in the same
  Result shape (labels = statements, cells tagged `C`/`D`, dense grid when small). Refuses cleanly on
  a multi-function scope, a missing function, an oversized graph, or a **non-Python** function (the
  layer is Python-only for now — a precise "Python-only so far" refusal, no crash).

### Notes
- The PDG is **complementary**, not a strict upgrade to the expression/token fingerprints: it is
  order-invariant by construction (reordering independent statements leaves it unchanged) and the
  only layer that models statement-level data flow, but a naive reaching-def over-penalises
  temp-variable factoring (see `research/03-pdg/FINDINGS.md`). Exposed as a drill-down + public
  `pdg_source`; `find_similar`/`graph_diff` remain on the expression layer.

### Guarantees
- On-demand only (no store schema change); advisory — the STATEMENT layer never feeds
  `find_stale`/liveness (a test pins that a drill-down cannot change `find_stale`).
- **Deterministic output** — `pdg`'s edge list and `get_matrix`'s `cells` are byte-reproducible
  across processes (edges emitted in sorted order, not `set`/`PYTHONHASHSEED` order), matching the
  guarantee the CALL layer already upheld.

### Quality gate
- ruff + mypy clean; full suite passing. New: `tests/oracles/test_pdg_source_layer.py`
  (key-parity, well-formed C/D graph, **reorder-invariance**, dependence-change sensitivity,
  never-raises, **cross-`PYTHONHASHSEED` determinism**) + statement-layer cases in
  `tests/test_layer_matrix.py` (drill, refusals incl. non-Python, cardinal isolation).
  Full-diversity adversarial panel clean (a mid-review round caught + fixed the determinism nit).

## [3.8.0] — 2026-07-01

**The graph learns layers — drill from the call graph into a function's value-flow graph (§5c
phase 1).** v3.0.0–v3.7.0 built the intra-procedural body matrix for all 12 languages but kept it an
internal fingerprint input. v3.8.0 makes it a first-class, drill-down-able **layer** of the
code-property graph (`docs/IDEAS.md` §5c): the same `get_matrix` / `graph_diff` primitives now work
at call OR expression depth. New capability for an existing representation, backward-compatible
(schema, indexes, and every operation's default behavior unchanged) → MINOR.

### Added
- **`model.Layer`** — the granularity tag (`CALL` / `EXPRESSION`; `STATEMENT`/PDG reserved for a
  future phase), analogous to how `Relation`/`Provenance` qualify edges. One graph, picked depths.
- **`structure.vfg` / `vfg_source`** (and the same `vfg_source` on all 9 tree-sitter frontends) —
  expose the per-function value-flow graph publicly: `{qualname: (node_labels, [(src, dst, kind)])}`,
  kind `d`=data / `c`=control. The EXPRESSION-layer companion to `fingerprint_source` (guaranteed
  identical keys — they share one traversal). Computed on demand, never persisted.
- **`get_matrix(..., layer="call"|"expression")`** — `"call"` is unchanged (now tagged
  `layer="call"`); `"expression"` drills into a SINGLE function's value-flow graph, returned in the
  same shape (labels = operations, cells tagged data/control, a dense grid when small). Dispatched by
  file extension across all 12 languages; refuses cleanly on a multi-function scope, a missing
  function, an oversized graph, the reserved `statement` layer, or an unknown layer. CLI
  `--layer expression` and the MCP tool schema pick it up automatically (registry-generated).

### Changed
- `graph_diff` is documented as the two-layer diff: call-layer node/edge deltas always, plus (with
  `body`) the expression-layer `body_changed` — the same graph `get_matrix(layer="expression")`
  surfaces. API unchanged.

### Guarantees
- **On-demand only** — no store schema change, no indexer/scale impact; the CALL layer stays the sole
  persisted graph. The EXPRESSION layer is **advisory** and never feeds `find_stale`/liveness — the
  cardinal rule is a call-layer property (a test pins that a drill-down cannot change `find_stale`).

### Quality gate
- ruff + mypy clean; full suite passing.
- New tests: `tests/test_layer_matrix.py` (drill-down, layer tag, every refusal path, cardinal
  isolation) and `tests/oracles/test_vfg_source_layers.py` (vfg_source keys == fingerprint keys,
  well-formed graphs, metamorphic body-value-flow — all 12 languages).
- Two-round full-diversity adversarial panel clean on the post-fix HEAD.

## [3.7.0] — 2026-06-30

**The body matrix completes the language sweep — Ruby, PHP, and Bash.** v3.6.0 added Java and C#;
v3.7.0 adds the final three — **Ruby, PHP, and Bash** — so the body matrix now spans **all 12
languages** the extractor indexes (`docs/IDEAS.md` §5b, sweep complete). Bash is the command-oriented
outlier that closes it. New languages for an existing representation → MINOR; backward-compatible
(schema, indexes, and every existing operation unchanged; the new behavior is opt-in and advisory).

### Added
- **`core/structure_ruby.py`** — Ruby body fingerprint. Dotted module/class keying (`M.Calc.compute`,
  singleton `M.top`, bare top-level `free_fn`), expression-oriented (trailing implicit return),
  blocks opaque. Same `_VFG` + WL kernel as the other frontends.
- **`core/structure_php.py`** — PHP body fingerprint. Class-chain keying (namespace excluded,
  `Calc.compute`, `C.__construct`), statement-oriented, argument-wrapper unwrapping, closures opaque.
- **`core/structure_bash.py`** — Bash body fingerprint, the command-oriented outlier: a command is a
  CALL, command substitution carries values (incl. callee position), flat function keys, nested
  functions opaque.
- **`find_similar(mode="structure")` and `graph_diff`** now cover Ruby, PHP, and Bash, ranked/diffed
  same-language only. Sniff order …C# → Ruby → PHP → Bash → C/C++ (Bash/PHP before C/C++, whose
  grammar parses a bare `name() {…}`).

### Fixed / hardened
- The adversarial panel found **10 dropped value-flow positions**, all now fixed and oracle-pinned:
  Bash dynamic-callee (`$(resolve) arg`), Bash command-substitution array-subscript index
  (`${arr[$(helper)]}` read + `arr[$(helper)]=x` LHS), Ruby `begin/rescue/else` clause body, Ruby
  parenthesized multi-statement group (non-trailing statement), PHP anonymous-class constructor
  arguments (`new class(helper()) {}`), PHP **heredoc** interpolation holes (`<<<E…{$o->m()}…E`)
  — heredoc was wrongly bucketed with non-interpolating `nowdoc` as a constant, while double-quoted
  interpolation was already walked — the **C# constructor initializer** (`: this(helper())` /
  `: base(helper())`), whose arguments run before the body but live in a `constructor_initializer`
  sibling of the body that was never walked (the C# analogue of the C++ member-initializer-list, which
  was already handled), and the **C# indexed/dictionary-initializer key** (`new D { [Key()] = v }`),
  whose key routes through `bind()` as an `element_binding_expression` that had no branch, and the
  **JS/TS computed *method* key** in an object literal (`{ [helper()]() {} }`), whose key is evaluated
  in the enclosing scope but was dropped while the method body stayed (correctly) opaque — the data-
  property computed key `{ [helper()]: 1 }` was always walked. None were caught by the generic
  fallback — only the value-bearing metamorphic probe surfaces them.
- **Meta-pattern closure: "an expression syntactically inside a closure/class-bearing node but
  evaluated in the enclosing scope."** Rather than fix only the specific instances the panel reported,
  each was generalised across the matrix. This class now covers C++ lambda init-captures, C# element-
  binding init keys, JS/TS computed method keys in **both** object literals **and** class expressions,
  and Python nested-`def` default-argument values + nested-`class` base/keyword (`metaclass=`)
  expressions — all walked into the enclosing function's value-flow while the closure/class **body**
  stays opaque. Pinned by `tests/oracles/test_param_and_index_invariance.py` and the Python
  completeness oracle.
- Documented two Bash structural blind spots that are not fixable in-AST (advisory-only, never
  cardinal): `${var#$(cmd)}`/`${var%…}` strip patterns (lexed as one opaque `regex` token) and
  single-quoted deferred actions like `trap '$(cmd)' EXIT` (`raw_string`, expanded only at `eval`).
- **Comments are now trivia in every tree-sitter frontend (cross-cutting fix).** A confirmation-panel
  sweep found that a `comment` node leaked into the value-flow graph via each walker's generic
  fallback, so a no-op comment edit changed a body fingerprint (down-ranking commented clones and
  showing comment-only diffs as `graph_diff` body changes). This was **latent in Go, Rust, C/C++,
  Java, C# (shipped v3.3.0–v3.6.0) and JS/TS** as well as the new Ruby/PHP/Bash; only Python (its
  `ast` drops comments) was truly immune. (JS/TS first looked immune because statement-position
  comments use field access — but comments in *expression* positions, e.g. a call argument or array
  literal, still leaked; the oracle now exercises both.) All nine affected frontends skip comment
  nodes as trivia, pinned by a cross-language oracle (`tests/oracles/test_comment_invariance.py`) that
  also guards against over-pruning real flow. Advisory-only, never cardinal.
- **Default parameter-value expressions are now walked (cross-cutting fix).** A `helper()` CALL vs a
  `0` CONST in a parameter's default value (`def f(b = helper())`) produced an identical fingerprint —
  the parameter-seeding loop registered only the parameter name and never walked its default-value
  child. Found across **every language with default-argument syntax**: latent in **C++, C# (shipped)**
  and **Python, JS/TS (shipped, the original frontends)**, plus the new Ruby/PHP; Go/Rust/Java have no
  default-argument syntax. All now link the default-value expression into the parameter's node
  (incl. destructured defaults like JS `function f({a = helper()})`, AND JS/TS destructuring defaults
  in a *declaration/assignment* target — `const {x = helper()} = a` — which route through `bind()`, a
  separate path), pinned by a cross-language oracle. Advisory-only, but a true CALL-vs-CONST
  completeness violation.
- **Python lambdas are now opaque (invariant fix).** A `lambda` in expression position leaked its
  body's value flow into the enclosing function's fingerprint — `_build_vfg.ev` had no `ast.Lambda`
  branch, so it hit the generic fallback and recursed into the lambda body. This broke the documented
  "closures are opaque `NESTED`" invariant (which the Python completeness oracle already *classified*
  but never behaviorally tested) and is the one frontend that diverged — all 11 tree-sitter frontends
  already treat an expression-position closure as a single `NESTED` leaf. Now Python does too (the
  lambda's default-argument values still carry flow, since they evaluate in the enclosing scope).
- **Assignment-target subscript index is now walked (cross-cutting fix).** A `helper()` CALL vs a `0`
  CONST in the *index of an assignment target* (`d[helper()] = v`) produced an identical fingerprint:
  the read path `… = d[helper()]` always walked the index, but the write (`bind`) path linked only the
  written value and the container, never the index expression. Latent in **Python, JS/TS, Go, Rust and
  C/C++** (Java/C#/PHP/Ruby already walked it). All now walk the index on the write path, pinned by the
  same cross-language oracle. Advisory-only, never cardinal.
- **Comment trivia displacing a *positional* child pick (cross-cutting fix).** Beyond the generic-
  fallback comment leak above, a `comment` is itself a *named* tree-sitter child, so any walker site
  that selected one child by position (`named_children[0]`/`[-1]`/`[i]`, "first non-body" heuristics,
  or the inline transparent-unwrap descents `(x /*c*/)`, `await (x /*c*/)`, `f(x /*c*/)`, `d[i /*c*/]`)
  was displaced by a leading/trailing comment, dropping the real operand. Closed across all 8 tree-
  sitter frontends with comment-skipping helpers and a 64-case positional/trailing-wrapper battery.
- **Several value-bearing positions wrongly classified no-flow, closed matrix-wide.** A short
  find→fix→panel grind on the completed 12-language matrix surfaced (and a same-round audit closed
  across every applicable language) a cluster of advisory completeness drops, each oracle-pinned:
  first-only read of a **repeated** field (Ruby multi-value `rescue`/`when 1, helper()`); a
  **declaration carrying an initializer** routed to a no-flow/skip arm (PHP `static $x = helper()`,
  Rust local `const`/`static`); the **runtime-evaluated exception selector** (Ruby `rescue <expr>`,
  Python `except <expr>:` / `except*`); Python nested-def/class **decorator-call arguments**
  (`@deco(helper())`, evaluated in the enclosing scope — JS/TS already walked them); and the **C#
  interpolated-string alignment clause** (`$"{v,helper()}"`, distinct from the literal `:format`).
  All advisory-only, never cardinal.

### Quality gate
- ruff + mypy clean; full suite passing (1269 tests).
- Three new body-matrix completeness oracles — Ruby (49), PHP (51), Bash (36) — plus two new cross-
  language batteries, `test_comment_invariance.py` (64) and `test_param_and_index_invariance.py` (26),
  and `graph_diff` body tests pinning the new corpora; the Python (59), Rust (45) and C# (58) oracles
  grew with the cross-cutting fixes. **Two consecutive clean full-diversity panels** (opus + sonnet +
  haiku) on the frozen post-fix HEAD — RELEASABLE.

## [3.6.0] — 2026-06-30

**The body matrix learns Java and C#.** v3.5.0 added C/C++; v3.6.0 adds **Java and C#** — languages 5
and 6 of the multi-language sweep (`docs/IDEAS.md` §5b), the first release to add a *pair* in one
MINOR. New languages for an existing representation → MINOR; backward-compatible (schema, indexes, and
every existing operation unchanged; the new behavior is opt-in and advisory).

### Added
- **`core/structure_java.py` — Java body matrix.** A tree-sitter Java walker emitting the **same**
  `_VFG` the other frontends do, reusing the WL kernel. Methods/constructors are keyed by the dotted
  chain of enclosing TYPE names (the `package`/imports are NOT part of the key) — `Outer.compute`,
  nested `Outer.Inner.m`, interface default `Shape.area`, constructor `C.C`. Statement-oriented;
  compound-assign normalises to base-op + rebind; casts are operand-transparent; enhanced-`for`,
  `switch` (statement and arrow expression), try-with-resources, `synchronized`/labeled blocks carry
  flow; lambdas / anonymous classes are opaque `NESTED` leaves. Java has no free functions, so a
  top-level method (only seen in error-tolerant parses of non-Java source) is intentionally not keyed.
- **`core/structure_csharp.py` — C# body matrix.** A tree-sitter C# walker emitting the same `_VFG`.
  Methods/constructors **and local functions** are keyed by the dotted TYPE chain (the `namespace` is
  NOT part of the key) — `Calc.Compute`, constructor `Calc.Calc`, local function `Calc.Local.Inner` —
  matching the extractor. Call/index arguments are unwrapped from their `argument` nodes;
  expression-bodied members (`int M() => e;`), `foreach`, `switch` statement/expression, `using`/`lock`
  blocks carry flow; lambdas / anonymous methods are opaque. Properties, operators, and destructors are
  not method nodes in the extractor, so they are not keyed.
- **`find_similar(mode="structure")` and `graph_diff`** now cover Java and C#, ranked/diffed
  same-language only (a node id maps to exactly one file → one language).

### Quality gate
- ruff + mypy clean; full suite **1029** passing; differential oracle suite **393** — incl. two new
  body-matrix completeness oracles: **Java** (46 metamorphic cases + invariants) and **C#** (47
  metamorphic cases + invariants), each requiring a `helper()` CALL vs a `0` CONST in every
  value-bearing position to change the fingerprint.
- Adversarial rounds found and fixed two more dropped positions in **both** frontends: a comma-separated
  `for` loop's 2nd-and-later **init/update** expressions (`for (…; …; i++, sink(x))`) were dropped
  because the grammar models them as *repeated* `update`/`init` field children and the walker read only
  the first via `child_by_field_name`; and a C# `catch (E e) when (filter)` **exception-filter**
  predicate was never walked. Both now iterate the repeated field children / walk the filter.
- **Hardened the completeness-oracle predicate across all seven languages.** The metamorphic check was
  `similarity(a, b) < 1.0`, but cosine self-similarity of a large WL vector rounds to `0.999…98 < 1.0`,
  so it could *pass on byte-identical fingerprints* — masking a fully-dropped construct. It now returns
  `1.0` (fails) when the two fingerprints are exactly equal. This caught three C# drops the weak
  predicate had hidden — `using (var r = e)` paren-form resource (a positional, unnamed-field child),
  `$"{…}"` interpolated-string holes (was one CONST), and the `new int[]{…}` element initializer (a
  positional `initializer_expression`) — all now walked. The other six languages re-verified clean.
- Mutation meta-oracle: `structure.py` 15/15,
  `graphdiff` 9/9, `similar.py` **53/61** (the new Java/C# fingerprint corpora are now mutation-pinned
  by `graph_diff` body tests; the 8 survivors are justified-equivalent — the `not sep or … is None`
  short-circuit guards, unreachable because every node id contains `::`, plus the `_cosine`/`_dot_cos`
  defensive guards). Two-round full-diversity adversarial panel (opus/sonnet/haiku), clean.

## [3.5.0] — 2026-06-30

**The body matrix learns C and C++.** v3.4.0 added Rust; v3.5.0 adds C/C++ — language 4 of the
multi-language sweep (`docs/IDEAS.md` §5b), and the predicted "harder" one (pointers, the
preprocessor, out-of-line methods, templates). New language for an existing representation → MINOR;
backward-compatible (schema/indexes/existing ops unchanged, opt-in, advisory).

### Added

- **`core/structure_cpp.py`** — one tree-sitter walker covering **both C and C++** (the `cpp` grammar
  is a superset that parses C cleanly), emitting the **same** `_VFG` vocabulary as the other frontends
  and reusing the WL kernel. The function name is dug out of the declarator chain (unwrapping
  `pointer_declarator`/`reference_declarator` for `int* f()`; an out-of-line `int Foo::m()` keys to
  the bare last component `m`, matching the extractor). Statement-oriented (explicit `return`).
  Handles compound assignment, `?:`, casts (operand flows, type doesn't), `*p`/`&x`, `a[i]`
  (the cpp grammar's `indices`/`subscript_argument_list` shape), range-for, switch (case value walked
  once), `new`/`delete`, initializer lists, lambdas (opaque `NESTED`). `sizeof(expr)` collapses to a
  CONST — correct, since it never evaluates its operand. Function-like `#define` macros are
  preprocessor constructs (not `function_definition`s) and out of scope; a *call* to a macro is a
  normal `call_expression`.
- **`find_similar(mode="structure")`** auto-detects C/C++ (after Python, JS/TS, Go, Rust) and ranks
  same-language only.
- **`graph_diff`** body layer now covers C/C++ functions/methods too.

Qualname scheme matches the extractor: free/namespace/template functions bare, inline methods
`Class.method`, out-of-line `Foo::m` definitions bare `m`. Requires the optional **tree-sitter
extra** (advisory degrade without it); the Python body matrix stays stdlib-only.

### Quality gate

- ruff + mypy clean; full suite **921** passing; differential oracle suite **287** — incl. a new
  **C/C++ body-matrix completeness oracle** (45 metamorphic cases + invariants: compound-assign /
  cast / out-of-line-vs-inline qualnames / `sizeof`-is-constant / nested-lambda-opaque / reference-return captured / constructor-member-init-list walked / array-new-size captured / C++17-C++20 init-statement walked / placement-new address captured / stack-VLA-size captured / lambda-init-capture captured / ctor-dtor-function-try-block walked). The oracle
  caught a real drop during development (the cpp grammar keeps a subscript index under `indices`, not
  C's `index` field). Mutation meta-oracle unchanged (`structure.py` 15/15, `graphdiff` 9/9,
  `similar.py` 29/32). Two-round full-diversity adversarial panel (opus/sonnet/haiku), clean.

## [3.4.0] — 2026-06-30

**The body matrix learns Rust.** v3.3.0 added Go; v3.4.0 adds Rust — language 3 of the
multi-language sweep (`docs/IDEAS.md` §5b). New language for an existing representation → MINOR;
backward-compatible (schema/indexes/existing ops unchanged, opt-in, advisory).

### Added

- **`core/structure_rust.py`** — a tree-sitter value-flow walker for Rust that emits the **same**
  `_VFG` vocabulary as the Python, JS, and Go frontends and reuses the language-neutral
  Weisfeiler-Lehman kernel, so Rust↔Rust bodies compare the way Python↔Python ones do. Handles Rust's
  expression-oriented shape — a block's **trailing expression** is its value, so `{ x }` fingerprints
  like `{ return x; }`; `if`/`match`/`loop`/`while`/`for` are expressions; the `?` operator,
  references (`&x`), `as` casts, ranges, tuples, arrays, and struct literals carry their operand's
  value flow (the cast/asserted *type* carries none); macro invocations (`vec![…]`, `println!(…)`)
  expose args as a raw token tree, walked best-effort so a variable passed to a macro still threads
  flow; closures (`|x| …`) are opaque `NESTED` leaves; `self` and named results seed like parameters.
- **`find_similar(mode="structure")`** now auto-detects Rust (after Python, the JS/TS family, Go) and
  ranks it against stored functions of the **same** language only.
- **`graph_diff`** body layer now covers Rust functions/methods too.

Qualname scheme matches the Rust extractor: free functions are bare (`free_fn`), impl methods are
`Type.method`. Requires the optional **tree-sitter extra** for the Rust layer (advisory degrade
without it); the Python body matrix stays stdlib-only.

### Quality gate

- ruff + mypy clean; full suite **863** passing; differential oracle suite **233** — incl. a new
  **Rust body-matrix completeness oracle** (38 metamorphic cases: `helper()` vs `0` in each
  value-bearing statement/expression position must change the fingerprint, + trailing-expr-equals-
  return / compound-assign / cast / receiver / nested-closure invariants). Mutation meta-oracle
  unchanged (`structure.py` 15/15, `graphdiff` 9/9, `similar.py` 29/32). Two-round full-diversity
  adversarial panel (opus/sonnet/haiku), clean.

## [3.3.0] — 2026-06-30

**The body matrix learns Go.** v3.2.0 ported the intra-procedural value-flow matrix to the JS family;
v3.3.0 adds Go — language 2 of the multi-language sweep (`docs/IDEAS.md` §5b). New language for an
existing representation → MINOR; backward-compatible (schema/indexes/existing ops unchanged, opt-in,
advisory).

### Added

- **`core/structure_go.py`** — a tree-sitter value-flow walker for Go that emits the **same** `_VFG`
  vocabulary as the Python and JS frontends (operations + control points, data + control edges, copy
  propagation) and reuses the language-neutral Weisfeiler-Lehman kernel, so Go↔Go bodies compare the
  way Python↔Python ones do. Covers Go's statement/expression set — short-var/`var`/`const` and
  multi-value assignment, compound assignment + `++`/`--` rebinds, `if`/`for`/`for…range`/`switch`/
  `type switch`/`select`, channel send/receive, `go`/`defer`, slices, composite literals, type
  assertions/conversions — seeds the method **receiver** and named results like parameters, and
  treats nested `func` literals as opaque leaves (matching the Go extractor, which keys a method by
  its bare field name and does not mint closures as nodes).
- **`find_similar(mode="structure")`** now auto-detects Go (after Python and the JS/TS family) and
  ranks it against stored functions of the **same** language only.
- **`graph_diff`** body layer now covers Go functions/methods too: a data-flow change that leaves the
  call graph identical is caught in Go just as in Python and JS (same-language by construction).

Requires the optional **tree-sitter extra** for the Go layer; without it those paths return nothing
(advisory degrade) — the Python body matrix stays stdlib-only.

### Quality gate

- ruff + mypy clean; full suite **816** passing; differential oracle suite **190** — incl. a new
  **Go body-matrix completeness oracle** (45 metamorphic cases: `helper()` vs `0` in each
  value-bearing statement/expression position must change the fingerprint, + receiver/aug-assign/
  type-assertion/nested-closure invariants). Mutation meta-oracle unchanged (`structure.py` 15/15,
  `graphdiff` 9/9, `similar.py` 29/32). Two-round full-diversity adversarial panel (opus/sonnet/
  haiku), clean.

## [3.2.0] — 2026-06-29

**The body matrix learns JavaScript / TypeScript / TSX.** v3.0.0 shipped the intra-procedural
value-flow matrix for Python; v3.2.0 ports it to the JS family — the first step of the
multi-language roadmap (`docs/IDEAS.md` §5b). New representation for new languages → MINOR;
backward-compatible (schema/indexes/existing ops unchanged, opt-in, advisory).

### Added

- **`core/structure_js.py`** — a tree-sitter value-flow walker for JS/TS/TSX that emits the **same**
  `_VFG` vocabulary as the Python frontend (operations + control points, data + control edges, copy
  propagation) and reuses the language-neutral Weisfeiler-Lehman kernel, so JS↔JS bodies compare the
  way Python↔Python ones do. Captures every idiomatic function form — declarations, methods,
  `const f = () =>…` / `= function(){…}` bindings, object methods, class-field arrows, nested
  functions — and walks TS type annotations through as no-value-flow (so `TS ≡ JS`).
- **`find_similar(mode="structure")`** now auto-detects the snippet's language (Python, else the
  JS/TS family) and ranks it against stored functions of the **same** language (a fingerprint's
  topology tracks its extractor — cross-language scores aren't comparable).
- **`graph_diff`** body layer now covers JS/TS/TSX functions too: a data-flow change that leaves the
  call graph identical is caught in JS just as in Python (same-language by construction).

Requires the optional **tree-sitter extra** for the JS layer; without it those paths return nothing
(advisory degrade) — the Python body matrix stays stdlib-only.

### Quality gate

- ruff + mypy clean; full suite **762** passing; differential oracle suite **140** — incl. a
  **JS/TS body-matrix completeness oracle** (51 metamorphic cases: `helper()` vs `0` in each
  value-bearing position must change the fingerprint), which caught a real template-literal
  substitution drop during development and a TS `as`/`satisfies` cast that dropped its operand.
  Mutation meta-oracle unchanged (`structure.py` 15/15, `graphdiff` 9/9, `similar.py` 29/32).
  Two-round full-diversity adversarial panel (opus/sonnet/haiku), clean.

## [3.1.0] — 2026-06-29

**Test-coverage hardening + docs.** No source, API, or schema change — indexes don't need
rebuilding and every operation behaves exactly as in 3.0.0. This release closes a mutation-coverage
gap in `find_similar`'s semantic/dense path and makes the README state plainly what the package
delivers.

### Hardened

- **`core/similar.py` mutation coverage** — the differential mutation meta-oracle
  (`scripts/mutate.py`) previously left ~15 survivors in the optional dense/`model2vec` retrieval
  path (the body matrix's own core was already 15/15 + 9/9). Added unit tests that pin: the **token
  and dense ranking sort direction and `> 0` filter** with strict, tie-free fixtures (the existing
  fixtures tied at the top, so a reverse-flip left the winner unchanged and the mutants survived);
  the two **zero-norm `_dot_cos` guards** (a zero-magnitude query or node embedding must degrade to
  0.0, not divide-by-zero); and the **`model2vec` auto-load** path offline via a fake module + fake
  config (success wires an embedder and picks the configured-or-default model; the load is attempted
  at most once; an import failure stays on the token path).
- **test isolation** — `find_similar`'s dense backend is module-global (`_EMBEDDER` + the
  `_M2V_TRIED` once-latch); an autouse fixture now snapshots and resets it per test, so leaked state
  can't change which retrieval path a later test takes. This made the suite *and* the mutation kills
  order-independent (a mutant had been killed only by a leaked embedder, masking a real gap).
- Result: **29/32 mutants killed, deterministically**; the 3 survivors are documented as
  justified-equivalent (`tests/test_similar.py` module docstring). Closes `docs/IDEAS.md` §5d.

### Docs

- **README** — leads with what stitchgraph delivers (the question each operation answers) before the
  internals; the v3.0.0 body matrix is the headline and the language count is consistent.

### Quality gate

- ruff + mypy clean; full suite **703** passing; differential oracle suite **85**. Mutation
  meta-oracle: `structure.py` 15/15, `graphdiff` 9/9, and `similar.py` now 29/32 (3 justified
  equivalent). Two-round full-diversity adversarial panel (opus / sonnet / haiku), clean.

## [3.0.0] — 2026-06-29

**The intra-procedural body matrix (Python).** stitchgraph's graph has always been
*inter*-procedural — definitions linked by CALLS / REFERENCES / INHERITS / IMPORTS. v3.0.0 adds the
level *below* it: a per-function **value-flow matrix** built from the body AST, and two advisory
features built on it. New representation → MAJOR version. **Backward-compatible**: existing
operations, the schema, and indexes are unchanged; the new capabilities are opt-in.

Grew out of the matrix-as-oracle research (`research/`, not packaged): the body-level signal finds
redundancy/fidelity issues the call graph is blind to (it independently re-found, then drove the
v2.3.0 `tarjan_scc` dedup).

### Added

- **`core/structure.py`** — a structural fingerprint for Python functions: a value-flow graph
  (operations + control points, data + control edges) with copy propagation, fingerprinted
  order- and name-invariantly via a Weisfeiler-Lehman kernel. So renamed locals, reordered
  independent statements, and temp-variable factoring all read as the same shape.
- **`find_similar(mode="structure")`** — rank stored Python functions by *body shape* (not name /
  docstring / callees). Finds Type-2/Type-3 clones a token differ misses. Default
  `mode="semantic"` is unchanged.
- **`graph_diff`** (new operation) — structurally diff this index against another built index
  (a `.db` path): located node/edge deltas (`mode="id"` exact, `mode="leaf"` name-tail for
  cross-codebase shape) plus, for Python functions in both, those whose *body shape* changed —
  the translation-fidelity / plan-vs-actual signal. A data-flow bug that leaves the call graph
  identical is invisible to the call-level diff but caught by the body layer. Exposed on the
  library API, CLI (`graph-diff OTHER_DB --mode/--body/--body-threshold`), and MCP. The `other_db` file is treated as strictly
  read-only — it is validated by a read-only probe and diffed over a temporary copy, so a valid but
  older-schema index is never migrated/mutated on disk.

All three are **advisory and read-only** — they never feed `find_stale`, so the cardinal rule is
structurally unaffected. **Python-only** (the deep stdlib `ast`); other languages are future work
(`docs/IDEAS.md` §5). The fingerprint is a structural approximation, not sound data flow (copy
propagation but no SSA φ-nodes / loop fixpoint / alias analysis; constants collapsed) — documented
in the module and `research/04-expr-dfg/FINDINGS.md`.

### Quality gate

- ruff + mypy clean; full suite **698** passing; differential oracle suite **85** — incl. a
  graph_diff dogfood oracle (stitchgraph's own source self-diffs to equivalent) and a **body-matrix
  completeness oracle**: a metamorphic battery that fails if any value-bearing Python statement type
  is dropped by the fingerprint, plus an introspective guard that fails when a future Python adds a
  statement type — closing the one defect class adversarial review kept surfacing (`except*` →
  control-flow defs → `match` → subscript index → dict keys), at BOTH statement and expression level. Mutation meta-oracle: the `structure.py` core 15/15
  (`pytest tests/test_structure.py tests/oracles/test_structure_completeness.py`) and `graphdiff`
  core 9/9 (`pytest tests/test_graph_diff.py`) killed by their own unit suites. Multi-round
  full-diversity adversarial panel (opus/sonnet/haiku).

## [2.3.0] — 2026-06-29

**Internal: shared Tarjan SCC core (`tarjan_scc`).** The strongly-connected-components algorithm
was duplicated verbatim in `reach.strongly_connected_components` (call/import-cycle detection,
behind `scan`) and `dataloop._tarjan` (data-feedback loops). It is now extracted once into
`core/_scc.py:tarjan_scc(adj, seeds, node_count)`; both call sites delegate to it. **No API,
schema, or behaviour change** — index output, `scan` cycles, and `find_data_loops` results are
byte-identical; this is a pure de-duplication.

Surfaced by the matrix-as-oracle research (`research/` is not packaged): `02-body-matrix` found the
byte-identical clone the call-graph detector was blind to, and `03-pdg` independently re-found it by
dependence structure.

### Changed

- Extract `tarjan_scc` into `core/_scc.py`; `reach.py` and `dataloop.py` delegate to it. Each call
  site keeps its own adjacency build, seed set (`reach`: all node ids; `dataloop`: adjacency keys)
  and post-filter, so behaviour is preserved exactly, including the temporary recursion-limit raise
  restored in a `finally`.

### Added

- `tests/test_scc.py` — 15 direct unit tests for the shared primitive: component identity
  (empty / single / self-loop / chain / cycle / two cycles / cross-edge into a finished SCC),
  destination-only and out-of-adjacency seeds, reverse-topological order, deep-chain
  no-`RecursionError`, `defaultdict` non-mutation, and recursion-limit restoration on both normal
  return and an exception mid-walk. Stdlib-only, so it runs in the core-only CI job.

### Quality gate

- ruff + mypy clean; full suite **590** passing; differential oracle suite (27) green; mutation
  meta-oracle on `tarjan_scc` — 6/6 mutants killed by `tests/test_scc.py` alone. Two-round
  full-diversity adversarial panel (opus/sonnet/haiku) clean: confirmed component/order/
  recursion-limit equivalence to the old inline copies (incl. a 650-graph random differential with
  zero divergences) and that `find_stale` liveness is structurally independent of SCC (SCC feeds
  only advisory cycle/data-loop findings, never stale rooting).

## [2.2.1] — 2026-06-29

**Bash `PROMPT_COMMAND` hook recall (#95) + contributor methodology docs.** A small patch on top of
the v2.2.0 milestone. No API or schema change.

### Fixed

- **#95** — a function registered via `PROMPT_COMMAND=fn` (also `PROMPT_COMMAND="fn1; fn2"` and
  `export PROMPT_COMMAND=fn`) is run by the interactive shell before each prompt — a runtime hook
  with no textual call site — so it was false-flagged dead. `_bash_callback_refs` now roots the
  function name(s) in a `PROMPT_COMMAND` assignment. Scoped to that well-known variable (high
  precision); cardinal-safe (only a name that resolves to a project function is rooted). The generic
  `var=fn; $var` indirection remains a documented deferred dynamic-dispatch gap.

### Docs

- `CONTRIBUTING.md` gains **"The cardinal-hardening loop (dogfood + docs)"** — the repeatable method
  behind the v2.1.x→v2.2.0 line: dogfood real repos to surface false-deads, read the language/runtime
  docs to find the exact form that's actually invoked, fix additively (an added root can't introduce
  a cardinal), gate + pin both directions, and document over-rooting boundaries rather than risk the
  invariant by tightening them.

### Issue triage

- GitHub issues #18–#22 (filed against v1.0.4) were verified already fixed in the shipped code
  (`--version`, `risk` indexed-root default, `[project.scripts]` roots, bash top-level body seeding,
  `find_holes` scope documented) and can be closed.

## [2.2.0] — 2026-06-29

**Milestone release — the cardinal sweep is complete across all ten supported languages, and the
post-sweep precision/recall follow-up backlog (#70–#89) is closed.** A consolidation of the
2.1.1–2.1.31 hardening line into one minor release. No API or schema change; indexes rebuild cleanly
and `find_stale` output is strictly more precise than 2.1.0 (fewer false-positive dead-code reports).

The guiding invariant throughout: **live code is never confidently flagged dead.** Every fix in this
line either removes a way live code could be reported dead (a "cardinal") or improves dead-code recall
without ever risking that invariant; each shipped behind the full gate (ruff + mypy + the differential
streaming oracle + a mutation meta-oracle) and two consecutive clean full-diversity multi-model
adversarial review rounds.

### Highlights since 2.1.0

- **Per-language cardinal sweep (2.1.1–2.1.26)** — one gated cardinal fix per language across Python,
  JS/TS, Go, Rust, C/C++, C#, Java, PHP, Ruby, and Bash: framework/attribute classes, runtime/FFI
  and native entry points, test-runner discovery, indirect/dynamic dispatch (Ruby `&:sym`, JS
  well-known Symbols + accessors + coercion hooks), C/C++ macro-body and function-pointer-table call
  sites, Java overload/role unions and anonymous-inner-class overrides, and more.
- **Post-sweep follow-up backlog #70–#89 (2.1.27–2.1.31)** — JS/TS shorthand members of exported
  objects incl. `as const`/`satisfies` (#74); TS `#private` via `this.#m()` and string/computed/
  numeric-keyed class methods (#76/#78); Python subscripted-`Protocol[T]`/ABC recognition and bodyless
  abstract interface methods (#70/#86); C/C++ structs used only as a type (#89); Bash `declare -fx` /
  `declare -f -x` / `typeset -fx` exports and `time { … }` targets (#73). The remaining items were
  resolved without code change as deliberate, panel-confirmed cardinal-safe boundaries, or are
  coverage-only.

The granular per-version entries below remain as the detailed development record.

### CI / tests

- Guarded 14 pre-existing tree-sitter-dependent regression tests with
  `pytest.importorskip(...)` so the **core-only (no-extras)** CI job skips them cleanly instead of
  erroring. (They had been latently unguarded since the cardinal sweep but were never surfaced
  because CI had never actually executed — Actions was blocked by a $0 spending budget until the
  repository was made public.) Test-only; no behavior change with the extras installed.

## [2.1.31] — 2026-06-28

**Bash function-export recall (#73) — and the close of the #70–#89 follow-up backlog.**
A function exported for subshells via `declare -xf` / `typeset -fx` (the ksh/bash spellings of
`export -f`), or invoked under `time { fn; }`, was flagged dead though it is live.

### Fixed

- `_bash_export_decl` now recognizes `declare -fx` / `declare -xf` / `typeset -fx` / `typeset -xf`
  (a flag combining `f` and `x` exports a function), in addition to `export -f`. A plain
  `export VAR=…`, `declare -r`, or `declare -f` (print only, no `x`) still roots nothing.
- `_bash_time_target` skips a leading brace-group token, so `time { fn; }` (which tree-sitter
  mis-parses, making `{` a word arg of `time`) roots `fn`.

### Resolved without code change (cardinal-safe boundaries / non-issues)

- **#72** (`trap SIGNAL` one-word reset over-roots the signal name): over-rooting is the
  cardinal-SAFE direction; a one-word trap arg is ambiguous (signal vs handler), so removing the
  root would risk flagging an intended handler dead. Left intentionally.
- **#84** (narrow Go selector-field references to method-kind): the current broad selector rooting
  (v2.1.12) is over-rooting; narrowing it would un-mask and risk a cardinal. Deferred.
- **#79** (`_unwrap_ts_value` `seen < 8` cap): the cap is a runaway guard; 9+ literally-nested
  TS value wrappers do not occur in real code. Theoretical, no action.
- **#82** (decorator on a `const X = class{}` expression): not valid TypeScript. No action.
- **#85**: added a regression test pinning that `nodes.file` is populated after a plain reindex.

With this, the entire post-sweep cardinal-safe follow-up backlog (#70–#89) is closed — every item
is either fixed behind the full gate or has a documented cardinal-safety reason to stay.

## [2.1.30] — 2026-06-28

**C/C++ struct used only as a type (#89).** A struct/union/enum used only as a TYPE —
`struct Config g;`, `void f(struct Config *p)`, a field or return type — is a live data-model
definition, but C/C++ has no constructor call to edge it, so it had no inbound edge and was
false-flagged dead.

### Fixed

- A new `_c_type_ref_names` collects the names of bodyless (type-use) `struct`/`union`/`enum`/`class`
  specifiers — the definition carries a `body` field and is skipped — and the post-pass roots every
  matching C/C++ class node `callback`. Project-wide (a type is defined in a header and used in a
  `.c`), scoped to C/C++. Cardinal-safe over-approximation: a struct genuinely never used as a type
  (and never instantiated) still flags dead — verified.

### Resolved without code change

- **#88** (`_module_uses` treats `#define` parameter names as references): this is over-rooting —
  the cardinal-SAFE direction. Tightening it would *un-mask*, risking a live macro-referenced
  symbol being flagged dead, so it is left as a deliberate precision boundary.
- **#87** (enum-constant-body overrides): confirmed already handled — a Java enum constant with a
  method-override body and the helpers it calls are kept live (no reproduction). The companion
  class-scope anon-class over-rooting is the cardinal-SAFE direction and is left intentionally.

## [2.1.29] — 2026-06-28

**Python abstract / Protocol interface methods (#70, #86).** A bodyless interface-method
declaration is a contract fulfilled by overrides, never called by name — so it should not be
reported as dead code.

### Fixed

- `_is_abstract_class` now resolves a SUBSCRIPTED base via `_base_name`, so `class Repo(Protocol[T])`
  / `class C(ABC, Generic[T])` is recognized as abstract (it was missed when the base was an
  `ast.Subscript`, whose `_name_of` is None) — #70.
- A bodyless abstract / Protocol method (`def m(self): ...` under `@abstractmethod` or inside a
  Protocol/ABC) is now rooted `callback`, so it is no longer false-flagged dead — #86. Cardinal-safe
  (only adds a root). A method with a real body (a concrete default in an ABC) that is genuinely
  uncalled still flags dead, as does the private helper it alone reaches — precision preserved.

### Resolved without code change

- **#71** (`_framework_classes` name-based cross-file collision over-masks): over-masking is the
  cardinal-SAFE direction — it keeps a possibly-framework-reachable class live. Tightening it would
  *un-mask*, risking a live framework-only-reachable class being flagged dead (the cardinal sin), so
  the current behavior is a deliberate precision boundary, left intentionally.

## [2.1.28] — 2026-06-28

**TS class-member resolution cardinals (#76, #78).** Two ways a class method that is genuinely
live was confidently flagged dead:

- **#76 — `#private` method via `this.#m()`.** `_name_of` and `_callee` both returned None for a
  `private_property_identifier`, so a `#m(){…}` def was dropped (its body unwalked → a helper it
  alone calls flagged dead) AND the `this.#m()` call edge was lost.
- **#78 — string / computed / numeric-keyed class methods.** `_name_of` returned None for a string
  key (`"do it"(){}`), a computed-string key (`["do it"](){}`), or a numeric key (`42(){}`), so the
  method def was silently dropped and the helper it alone calls was flagged dead. A computed
  *identifier* key (`[NAME](){}`) was named but, in a non-exported class, never rooted.

### Fixed

- `_trailing_id` now handles `private_property_identifier`, so the `#m` def name and the
  `this.#m()` call site resolve to the SAME `#m`. A `#private` method resolves by name, so an
  UNCALLED one (and its private helper) still flags dead — precision preserved.
- A JS/TS class method with a dynamic key (string / computed / numeric) is now modeled as a node
  (named from the raw key text) with its body walked, and rooted `callback` — the class-body
  analogue of the object-literal computed-key rule, since such a method is reachable only via a
  dynamic `obj["k"]()` / `obj[expr]()` subscript. Cardinal-safe (only adds a root); a plain
  by-name method that is genuinely uncalled still flags dead.

## [2.1.27] — 2026-06-28

**JS/TS shorthand member of an exported object literal — cardinal fix (#74).**
A function referenced via object-literal SHORTHAND in an exported object —
`export const handlers = { onClick, onHover }` — is public API: any importer reaches it as
`handlers.onClick`. But a shorthand member is a `shorthand_property_identifier` the call graph
never models as a reference, so the named function — and the private helpers it alone calls — was
confidently flagged dead. (The CJS/default forms `module.exports = { onClick }` and
`export default { onClick }` were already handled; the named-const-export form was the gap.)

### Fixed

- `_reexport_names` now also collects an exported declaration's object-literal member names
  (shorthand identifiers + `pair` value identifiers) for `export const/let/var X = { … }`, feeding
  them into the same reexport→`exported` rooting path the CJS/default-export object forms already
  used. Language-gated to JS/TS via the existing reexport role-application guard. Cardinal-safe:
  rooting only adds a root; a shorthand in a NON-exported object still flags dead.
- The scan unwraps TS value wrappers (`as const` / `satisfies T` / parens) on both the named-export
  object and the `module.exports = …` RHS, so the canonical TS handler-object idiom
  `export const handlers = { onClick } as const` roots its members (panel R2 finding — the most
  common real-world shape of the bug).

### Resolved without code change

- **#77** (`obj._x = fn` underscore member-assignment not rooted like the object-literal path): a
  statically-named underscore member that is actually called resolves by name (verified), so the
  member-assignment underscore gate is a deliberate, cardinal-safe precision boundary, not a defect.
- **#81 / #83** (bare-identifier reassignment / bare function-expression in expression position):
  already covered for exported modules — module-scope `_module_uses` and pass-2 def-body recursion
  root these. The no-export-module case is the same library-detection boundary as any unreferenced
  top-level function.

### Known limitations (unchanged)

The remaining cardinal-safe precision/coverage follow-ups (#70–#89, minus those above) stay deferred.

## [2.1.26] — 2026-06-28

**JS/TS implicit-dispatch class members — cardinal fix (#54).**
A class member invoked only IMPLICITLY by the JS/TS runtime — a well-known-Symbol method
(`[Symbol.iterator]`, `[Symbol.asyncIterator]`, `[Symbol.toPrimitive]`, `[Symbol.hasInstance]`, …,
run by `for…of` / spread / coercion / `instanceof`), a `get`/`set` accessor (run by a property
read/write), or a serialization/coercion hook (`toJSON` via `JSON.stringify`, `toString`/`valueOf`
via string & numeric coercion) — is never reached by a plain `obj.method()` by-name call. In a
non-exported (but instantiated) class, the member and the private helpers it alone calls were
confidently flagged dead. (Exported-class members were already rescued by
`_seed_exported_class_methods`, which masked the gap.)

### Fixed

- A new `_is_js_implicit_dispatch_method` recognizes these three forms (a `computed_property_name`
  containing `Symbol.`; a `get`/`set` accessor; the names `toJSON`/`toString`/`valueOf`) and the
  JS/TS method-extraction path roots them `callback`. Language-gated to javascript/typescript/tsx
  (a Python `toJSON` is not a JS hook). Cardinal-safe over-approximation: rooting only adds a root,
  so a genuinely-unused accessor/hook is over-rooted (bounded, one per member) but live code is
  never flagged dead. A plain by-name method that is genuinely uncalled still flags.

### Known limitations (unchanged)

A general (non-`Symbol`) computed-key class method (`[CONFIG_KEY]() {}`) is a separate dynamic-
dispatch concern (#78) and is out of this change's scope. The remaining cardinal-safe precision/
coverage follow-ups (#70–#89) stay deferred.

## [2.1.25] — 2026-06-28

**C/C++ function-pointer table / vtable promotion — cardinal fix (#69).**
A C/C++ function whose address is taken in a global function-pointer table (`int (*ops[])(int) =
{op_a, op_b}`), a plugin/vtable struct (`struct ops P = {init, teardown}`), a designated-initializer
table (`{[0]=on_start}`), or a scalar (`cb h = handler`) is invoked **indirectly** through that
global — which may be consumed in a different translation unit via `extern`. Globals aren't graph
nodes, so that cross-TU use is untrackable, and the address-taken functions were confidently flagged
dead whenever their own TU had no entry point of its own (the passive registration-unit pattern).

### Fixed

- A new `_c_global_init_fn_refs` scan walks C/C++ module scope (top-level declarations, plus
  `namespace {…}` / `extern "C" {…}` bodies; never descending into function bodies) and collects the
  function identifiers in global-variable initializers — matching the `initializer_list` node
  directly, which is the common denominator across dialects (C parses the global as a `declaration`,
  but C++ mis-parses `int (*tab[])() = {…}` as an `expression_statement`/`assignment_expression`
  whose right side is still an `initializer_list`). Matching project C/C++ F/M nodes are rooted
  `callback`, mirroring the object-literal / macro-body indirect-dispatch rooting. Project-wide (the
  table and its target routinely live in different files). Cardinal-safe over-approximation: resolves
  by name to F/M nodes only — a non-function initializer identifier (a global const, an enum value)
  merely over-roots a homonym, never flags live code dead. Local (in-function) function-pointer
  assignments are unchanged (already covered by `_direct_refs`).

### Known limitations (newly documented)

A C `struct` used only as the type of a global/extern variable is still flagged dead (#89) — a
bodyless data type, not executable code (a precision nit, like the abstract-method-declaration
case). A function over-rooted because its name happens to appear in an *unused* global initializer is
a bounded, cardinal-safe precision cost. The JS/TS implicit-dispatch surface (#54) remains
pre-existing and deferred.

## [2.1.24] — 2026-06-28

**C/C++ function called only inside a `#define` macro body — cardinal fix (#59).**
A function called or named *only* inside a preprocessor macro body — `#define LOG(m) log_impl(m)`,
a function-pointer macro `#define DEFAULT handler`, a helper-wrapping macro `#define BOTH() (a()+b())`
— was confidently flagged dead. Tree-sitter parses a macro body as a single raw-text `preproc_arg`
node, so the call/reference inside it is invisible to the AST call scan and the function loses its
only caller.

### Fixed

- A new text-scan (`_macro_body_ref_names`) collects every identifier appearing in C/C++ `#define`
  bodies; any project C/C++ function/method whose name matches is rooted `callback` (it is invoked
  indirectly wherever the macro expands). This is the direct analogue of the existing
  `EXPORT_SYMBOL` text-scan for constructs the grammar doesn't model as calls. Project-wide across
  the unified C/C++ resolution bucket (a header macro routinely wraps a function defined in a `.c`),
  byte-gated to files that contain `#define`. Cardinal-safe over-approximation: matching resolves by
  name to F/M nodes only, so a macro parameter or a keyword sharing a name merely over-roots (keeps
  a genuinely-dead function live) — it never flags live code dead. A function whose name appears in
  no macro body still flags dead; numeric/string macros yield no identifiers.

### Known limitations (unchanged)

The C cross-TU global function-table promotion (#69) and JS/TS implicit-dispatch surface (#54) remain
pre-existing and deferred to their own reviews. A function over-rooted because its name happens to
appear in an *unused* macro body is a bounded, cardinal-safe precision cost.

## [2.1.23] — 2026-06-28

**Java anonymous-inner-class override in a class-scope initializer — cardinal fix (#62).**
An anonymous inner class (`new Base() { … }`) has no name, so its overriding method can never be
resolved by a `Class.method` by-name call — it is invoked only polymorphically through the base type
(`Runnable.run`, `Comparator.compare`, a custom abstract base). When the anonymous class sits in a
**method body** the enclosing-function containment edge already keeps its override live; but in a
**field / static initializer** (class scope, no enclosing function) nothing rooted it, so a
non-`public` override — and the private helpers it alone calls — was confidently flagged dead though
live. (Public overrides were masked by the `exported` role; the gap shows on `protected`/
package-private overrides of a custom abstract base.)

### Fixed

- A def that sits directly in an anonymous class body (a `class_body` child of an
  `object_creation_expression`) is now rooted `callback` when it is at class scope
  (`enclosing_func is None`) — it is polymorphically invoked and unreachable by name. An anonymous
  class inside a method body stays reachability-gated via the existing containment edge, preserving
  its precision (an override in a genuinely-dead method still flags). Cardinal-safe: only ever adds
  a root. A normal (named) class is untouched (the check requires the `object_creation_expression`
  parent), so a genuinely-dead named-class method still flags.

### Known limitations (unchanged / newly documented)

Abstract/interface method *declarations* (no body) are still flagged dead even when concrete
implementations are reached (#86) — pre-existing, general, and not a true live-code cardinal (a
bodyless contract slot). The C/C++ macro-body call sites (#59) and cross-TU function-table promotion
(#69) remain deferred to their own reviews.

## [2.1.22] — 2026-06-28

**Same-name method-overload role clobber — cardinal fix (#61, store-level, all languages).**
Two same-name method **overloads** (`void f()` / `void f(int)` in Java/C#/C++) collapse to one node
id (`Class.f`). The node persistence used `INSERT OR REPLACE`, so the **last-written** overload's row
won outright and **clobbered the earlier overload's roles**. A public-API method (`exported`)
overloaded with a private same-name helper declared *after* it — or a framework-callback overload
(`@PostConstruct` / `@Test`) followed by a plain one — lost its only root and was confidently flagged
dead though live. The bug was declaration-order-dependent (only the last overload's roles survived).

### Fixed

- `Store.add_node` now upserts with `ON CONFLICT(id) DO UPDATE` and **unions the roles** of colliding
  nodes instead of replacing them — a rooting role is never dropped, regardless of overload order
  (cardinal-safe). Edges were never at risk (they key on the `src` id, so both overload bodies'
  call/reference edges were already retained); only the node row's roles were being lost. Non-role
  columns continue to take the newest row, matching the prior `REPLACE` semantics. Joined-role
  duplicates are harmless (every reader splits into a set) and bounded (reindex/`replace_file` clear
  before re-inserting). The fix is store-level, so it covers C#/C++ overloads too, not only Java.

### Known limitations (unchanged)

Java anonymous-inner-class JDK abstract overrides (#62) and the C/C++ macro-body call sites (#59) /
cross-TU function-table promotion (#69) remain pre-existing and deferred to their own per-language
reviews.

## [2.1.21] — 2026-06-28

**Go method value / method expression references — cardinal fix (#49, cobra dogfood).**
An **unexported** Go method reached only as a *method value* (`reg(v.run)` — a bound method passed
as a callback), a *method expression* (`use(t.run)` — the unbound `T.method` form), or a
struct-literal field value (`cfg{onRun: v.run}`) was confidently flagged dead. These are
*references*, not calls: `_direct_calls` only sees `v.run()` call sites, and `_direct_refs` skipped
the selector's `field_identifier`, so the method got no inbound edge. (Exported/capitalized methods
were already rooted as public API, which masked the gap until probed with unexported receivers.)

### Fixed

- `_direct_refs` now emits the trailing `field` name of a Go `selector_expression` as a by-name
  REFERENCES edge, so a method named as a value/expression keeps its target live. `selector_expression`
  is unique to the Go grammar, so this is scoped to Go. A plain struct-field access (`v.name`) that
  happens to share a name with a function is cardinal-safe over-rooting (resolves only to a project
  symbol). A `v.run()` CALL contains the same selector, but the edge loop dedups REFERENCES against
  the CALLS set, so it never double-counts. A genuinely-unused unexported method still flags dead.

### Known limitations (unchanged)

The same method-value-as-reference shape in other grammars — Rust `Foo::method` / `vec.iter().map(Foo::bar)`,
C# method groups, JS `arr.forEach(obj.handler)` — is pre-existing and tracked separately; each needs
its own per-language review. Bare function/arrow expressions in JS/TS expression positions (#83) and
`this.#m()` private dispatch (#76/#78) remain deferred.

Precision note (cardinal-safe): because a plain Go struct-field read (`w.run`) is syntactically
identical to a method value, its field name is emitted as a reference too — so a genuinely-dead
function/method whose name *exactly* collides with a struct field name is over-rooted (kept live).
This never produces a false-dead (it is the precision-over-recall, cardinal-safe direction, and is
strictly better than the pre-fix state where the method value itself was false-dead), and is bounded
to exact-name collisions. A follow-up (#84) will narrow selector-field references to method-kind
resolution to recover that recall.

## [2.1.20] — 2026-06-28

**JS/TS object & class literals in EXPRESSION positions — cardinal fix (#75).**
An object (or class) literal reached only through an *expression shape* — a call argument
(`register({ onInit(){ helper() } })`, `Object.freeze({…})`), an array element (`[ {…} ]`), a
ternary / `||` / `??` branch, an IIFE return (`(() => ({…}))()`), a sequence
(`(init(), {…})`), or a chained/parenthesized assignment (`const r = m.exports = {…}`) — had its
members invisible. A `variable_declarator` whose value was such a shape was SWALLOWED (no descent),
so a helper called only from a member was flagged dead; a bare statement form descended generically
and minted the member as an UNROOTED node — the live method itself flagged dead. This closes the
last broad object/class extraction gap behind the v2.1.11/2.1.18/2.1.19 line.

### Fixed

- **Expression-position object literals** are now routed through `_object_members` (the same pass
  that backs `const obj = {…}` and `module.exports = {…}`) wherever generic descent reaches one, so
  their function-valued members are extracted and their bodies walked. Members at module scope take
  the `callback` role (dispatch-table idiom — over-rooting is the precision-over-recall, cardinal-safe
  direction); members nested in a function body stay reachability-gated via a CONTAINS edge. A
  position-synthesized qual (`<obj@line_col>`) keeps an anonymous object's members from colliding
  with a same-named real module function.
- **Anonymous/expression-position class literals** (`reg(class {…})`, `[ class {…} ]`) are modeled
  as CLASS nodes with INHERITS edges and their bodies walked, mirroring the `const X = class {…}`
  (#80) and `obj.X = class {…}` paths. At module scope the class takes the `exported` role so its
  public methods are rescued; nested in a function it is reachability-gated and its methods gated to
  the class (the round-3/4 rule). A `body`-field guard skips the bare `class` *keyword token* (also
  typed `class`) inside a regular `class X {}`, so ordinary class declarations are unaffected.
- The `variable_declarator` branch now **descends** into non-arrow/object/class values (call,
  array, ternary, logical, IIFE, chained assignment) instead of swallowing them — made safe by the
  interception above, which roots any literal it finds rather than letting raw descent mint
  unrooted methods (the round-11 cardinal the old no-else guarded against).

### Known limitations (unchanged)

`this.#m()` private dispatch (#76), `#private`/computed-key methods inside a class body dropped by
`_name_of` (#78), and bare-identifier function reassignment (`g = function(){…}`, #81) remain
pre-existing and deferred. Private-method dead-eligibility on *anonymous* expression-position
classes is cardinal-safe (over-rooting only).

## [2.1.19] — 2026-06-28

**JS/TS `const X = class {…}` class-expression bound to a const — cardinal fix (#80).**
A class expression assigned to a `const` (`export const Widget = class extends Component {
render(){ helper() } }`) was never modeled: the `variable_declarator` branch handled
arrow/function/generator/object values but not `class`/`class_expression`, so the class was not a
node and a helper called only from its methods was flagged dead. (The sibling
`assignment_expression` branch already handled `obj.X = class {…}`; this closes the asymmetry.)

### Fixed

- The `variable_declarator` branch now models a `class`/`class_expression` value as a CLASS node,
  mirroring the assignment-expression class handling: it emits INHERITS edges for the heritage,
  walks the class body, and (when the const is `export`ed) takes the `exported` role so
  `_seed_exported_class_methods` rescues its public methods — private methods stay dead-eligible
  (R46A). A class nested in a function body gates its methods to the class (chain enclosing-fn →
  class → methods, the round-3/4 rule); at module scope they rely on the exported rescue / call
  resolution. Behaviour is now at **parity with a regular `class X {}`** declaration. TS value
  wrappers on the value (`class {…} as const`) are peeled via the existing `_unwrap_ts_value`.

### Known limitations (unchanged from 2.1.18)

The broad "object literal reached only via an EXPRESSION shape" family (#75 — IIFE / ternary /
`||`/`&&`/`??` / `Object.freeze` / array element / sequence / chained-or-parenthesized assignment),
`this.#m()` private dispatch (#76), and bare-identifier function reassignment (#81) remain
pre-existing and deferred to a focused follow-up.

## [2.1.18] — 2026-06-28

**JS/TS object-literal function-member bodies — cardinal fix (rxjs/lodash-style config objects).**
A top-level function called ONLY inside an object-literal member — `const obj = { run() { helper() } }`
(method shorthand), `{ run: () => helper() }` (function-valued property), or a nested object — was
false-flagged dead: the object value was never traversed, so the call to `helper` was never seen and
its sole caller was invisible to the graph.

### Fixed

- **New `_object_members` pass** extracts the function-valued members of a JS/TS object literal
  (method shorthand, `arrow`/`function`/`function_expression`-valued properties, and members of
  nested object values) as METHOD nodes, so pass 2 walks their bodies and the calls inside become
  visible. Wired into both the `variable_declarator` branch (`const obj = {…}`) and the
  `assignment_expression` branch (`module.exports = {…}`, `Foo.prototype = {…}`).
- A module-scope, non-underscore member is invoked dynamically/externally (passed as a callback,
  spread into config, looked up by a computed key), never by a plain local name, so it takes the
  `callback` role — mirroring the existing member-assignment precedent. Over-rooting a member is the
  precision-over-recall, cardinal-safe direction. A member nested inside a function body stays
  reachability-gated via a CONTAINS edge (a dead initializer must not mint live roots). An
  underscore-private member opts out of the root, matching the member-assignment gate.
- **New `_obj_key_name` helper** reads a member key's static name including STRING keys
  (`{ "do-it"() {…} }`, `{ "k": fn }`) — `_name_of` returns None for a string-keyed method, which
  would silently drop the member and leave its body unwalked (the same cardinal class). A
  COMPUTED key (`{ [k]: () => … }`) is extracted under a synthesized id (from the key text) and
  rooted too — its body is walked so a helper called only there stays live. A genuinely-uncalled
  top-level function still flags.
- Module-scope members are rooted UNCONDITIONALLY, including underscore-`_private` and
  computed-key members: object literals are the canonical dispatch-table idiom
  (`handlers["_" + name]()`), so gating an underscore member out would mint an unrooted node that
  is then confidently flagged dead while live (cardinal — caught by the adversarial panel).
- `_unwrap_ts_value` peels TypeScript value wrappers (`{…} as const`, `{…} satisfies T`, `({…})`)
  that sit between the `variable_declarator` value and the object, so the member-extraction fires
  for the pervasive `export const obj = {…} as const` form (cardinal — panel).
- Member bodies are walked via `_collect`, so a function DEFINED inside a member
  (`run() { function inner(){…} }`) is extracted as a node and reached through a CONTAINS edge —
  pass 2 skips nested defs, so without this a helper that nested fn alone calls was flagged dead
  (cardinal — panel). Members nested in a dead function stay reachability-gated.
- A member VALUE is itself unwrapped (`run: (() => h())`, `go: (fn satisfies T)`, `x: ({…} as const)`)
  and a `class`-valued member (`{ Parser: class {…} }`) is modeled as a CLASS with the `exported`
  role so its public methods (and their private callees) are rescued — `_unwrap_ts_value` was
  applied to the whole object but not to individual member values, and class members weren't
  handled, so each dropped a live member's body (cardinal — panel round 2).
- A function-scoped class-valued member (`function f(){ const o = { K: class { run(){ h() } } } }`,
  `function f(){ obj.X = class {…} }`) gates its methods to the class node (chain enclosing-fn →
  class → methods) instead of leaving them orphaned and confidently flagged dead (cardinal —
  panels round 3/4); module-scope class members keep the `exported` rescue (private methods stay
  dead-eligible, R46A).
- `generator_function` values (`{ gen: function*(){…} }`, `async function*`, `exports.h =
  function*(){…}`, `const g = function*(){…}`) are handled across all four function-value tuples,
  so a generator member's body is walked like any other function value (cardinal — panel round 8).

### Known limitations (pre-existing, deferred to a focused follow-up)

These flag identically on the pre-v2.1.18 extractor — they are NOT regressions of this release; they
are the broad "object literal reached through an EXPRESSION shape" family, which the next release
will close with a single principled "route every object literal through `_object_members`" pass:

- An object literal reached only via an expression shape — an IIFE return, a ternary/`||`/`&&`/`??`
  operand, `Object.freeze(…)`/`Object.assign(…)`, a call argument to an external function, an array
  element, a `sequence` expression, or a chained/parenthesized assignment value
  (`const routes = module.exports = {…}`) — is not member-extracted, so a helper called only from
  such a member is flagged. (A naive generic fallthrough that "fixed" this instead minted the
  members as unrooted nodes and flagged the live methods themselves — a worse cardinal — so it was
  reverted; the family stays a recall gap until the principled pass lands.)
- `const X = class {…}` (a class expression bound to a `const`) is not modeled (the
  `variable_declarator` branch handles arrow/function/generator/object values, not `class`).
- A TS `#private` method invoked via `this.#m()`, and a bare-identifier function reassignment
  (`g = function(){…}`), are likewise pre-existing and tracked.

## [2.1.17] — 2026-06-28

**Ruby `&:symbol` / `enum_for` / `&method(:m)` symbol dispatch — cardinal fix (Ruby dogfood).**
Ruby names a method via a literal symbol in idioms the name-based call graph can't see, so the named
method (and its callees) was false-flagged dead.

### Fixed

- **New `_ruby_symbol_refs` pass** roots the method named by `xs.map(&:upcase)` (`Symbol#to_proc`),
  `enum_for(:m)` / `to_enum(:m)`, and `method(:m)` / `instance_method(:m)` (commonly `&method(:m)`).
  Each literal symbol is routed through `_ref` so it is rooted only if it resolves to a project
  method (cardinal-safe). Ruby method-name suffixes handled (`:valid?`, `:save!`, `:name=`).
  `send`/`public_send` are deliberately not covered (documented dynamic-dispatch limitation). A
  genuinely-dead method still flags.

## [2.1.16] — 2026-06-28

**Bash callback/invocation argument recognition — cardinal/recall fixes (doc-driven + dogfood manual
pass).** Commands that invoke a function via an *argument* (not the command head) were missed by the
head-keyed command scan, so the function (and its callees) was false-flagged dead.

### Fixed

- **`_bash_trap_handlers` generalized to `_bash_callback_refs`**, now rooting the function named by:
  `trap HANDLER` **including inside function bodies** (was top-level only); `complete -F FUNC` /
  `compgen -F FUNC` completion callbacks; `export -f FUNC…` (subshell-invoked, parses as a
  `declaration_command`); and `time FUNC` (the `time` keyword's target, which tree-sitter parses as a
  plain word). Each is routed through `_ref` → rooted only if it resolves to a project function
  (cardinal-safe); a genuinely-dead function and a plain `export VAR=…` still flag.

## [2.1.15] — 2026-06-27

**C++ range-based-`for` `begin()`/`end()` customization points — cardinal fix (doc-driven manual
pass).** `for (x : r)` is desugared to `r.begin()`/`r.end()`, so the name-based call graph never sees
those calls and an iterable type's `begin`/`end` (and what they reach) were false-flagged dead.

### Fixed

- **`_IMPLICIT_HOOKS` gains a `"cpp"` entry rooting `begin`/`end`** as `callback`. A class defining
  `begin`/`end` is iterable by design; rooting them is semantically correct and cardinal-safe
  (only adds roots). Covers `.cpp`/`.cc`/`.cxx`/`.hpp` and any `.h` that `_header_lang`
  content-sniffs as C++ (carries `class`/`namespace`/`template`/… markers); a pure-C `.h` stays
  C. A plain method with no caller still flags dead.

## [2.1.14] — 2026-06-27

**Ruby implicit conversion / Enumerable protocol methods — cardinal fix (doc-driven manual pass).**
The interpreter/stdlib invoke a class's conversion (`to_s`/`inspect`/`to_str`/…), Enumerable
(`each`), Hash-key (`hash`/`eql?`), and marshalling (`marshal_dump`/`_dump`/…) methods *by name*, so
a live class's protocol methods (and the helpers they reach) were false-flagged dead.

### Fixed

- **`_IMPLICIT_HOOKS["ruby"]` extended with the documented implicit-invocation protocol** —
  conversion/coercion (`to_s`, `inspect`, `to_str`, `to_a`, `to_ary`, `to_h`, `to_hash`, `to_i`,
  `to_int`, `to_f`, `to_r`, `to_proc`, `to_io`, `to_path`, `to_sym`), Enumerable (`each`, `each_pair`), Hash-key /
  ordering (`hash`, `eql?`, `succ`), and marshalling (`marshal_dump`, `marshal_load`, `_dump`,
  `_load`). Each such method is rooted `callback` (the Ruby analogue of Python dunder rooting). Only
  ever adds roots (cardinal-safe): a plain method with no caller still flags dead.

## [2.1.13] — 2026-06-27

**Runtime/native entry-point attributes: C ISR, Rust `#[ctor]`, Java `native` — three narrow cardinal
fixes extending the v2.1.9 runtime/native (FFI) entry-point arc.** Each marks a function the runtime
or toolchain invokes automatically with no in-tree by-name caller, so it (and its callees) was
false-flagged dead. All three are attribute/modifier-gated and only ever *add* roots (cardinal-safe).

### Fixed

- **C interrupt service routines** — `__attribute__((interrupt))` / AVR `((signal))` /
  `interrupt_handler` are invoked by the hardware vector table. The implicit-entry attribute regex
  omitted `interrupt`/`signal`; now matched → rooted `callback`.
- **Rust `#[ctor]` / `#[dtor]`** — the `ctor` crate's before/after-`main` attributes (the Rust
  analogue of C `__attribute__((constructor))`), idiomatically private. Added `ctor`/`dtor` to
  `_RUST_RUNTIME_ENTRY_ATTRS` (path-token match: `#[ctor::ctor]` and bare `#[ctor]` hit;
  `#[constructor_helper]` does not).
- **Java `native` methods** — JNI entry points (implemented in C, invoked across the JNI boundary,
  no Java body, no in-tree caller). New `_is_java_native` helper roots a `native` method `callback`
  (the Java analogue of Go cgo `//export` / C# `[UnmanagedCallersOnly]` from v2.1.9).

  Each is cardinal-safe: a plain function with no such attribute/modifier still flags dead.

## [2.1.12] — 2026-06-27

**Transitive framework-inheritance callback rooting (tree-sitter) — one cardinal fix clearing the
same root cause across PHP, C#, Java, and C++.** A symmetry gap: the Python extractor's
`_apply_callback_roles` already did a transitive INHERITS closure, but the tree-sitter extractor
marked only the *direct* subclass of an external framework base.

### Fixed

- **Framework-class detection now closes transitively over the in-tree INHERITS tree.** A concrete
  override two-or-more hops below an external framework base (via an in-tree abstract intermediary) is
  framework-invoked but had no in-tree caller, so it was confidently flagged dead — cardinal. New
  `_framework_classes` helper: (a) every class with a direct external base, (b) same-name self-loop
  bases (`class Foo extends pkg.Foo`, base leaf binds to itself — the real base is an external
  same-named framework class), plus (c) the transitive first-party descendants of those classes
  (fixpoint closure). Only ever *adds* roots, so it is cardinal-safe; a pure first-party chain with
  no external base anywhere still flags genuinely-dead overrides. Confirmed on real
  Magento 2.4.7 (`Shipment::_getValidationRulesBeforeSave`,
  `Transaction\Collection::_renderFiltersBefore`) and on the C# explicit `IDisposable.Dispose`
  reached only via `using` through a project interface that extends the framework interface.
  `_framework_classes` helper unit test + per-language regressions + mutation pinned.

## [2.1.11] — 2026-06-27

**Python implicit-invocation surface — three cardinal fixes, found by combining real-codebase
dogfooding (sqlalchemy, werkzeug) with a doc-driven pass over the Python language/library
reference.** Each was a live symbol confidently flagged dead at confidence ≥ 0.5; all three fixes
are reachability-*adding* and therefore cardinal-safe by construction.

### Fixed

- **Subscripted generic base classes now record an INHERITS edge.** `class Sub(Base[K, V])` parses
  the base as an `ast.Subscript`, so the old `_name_of` returned `None`, dropping the edge — and with
  it the polymorphic-override path, so a live override of a base template method was flagged dead. New
  `_base_name` helper unwraps the subscript (looping for nested `Base[K][V]`). `Generic`/`Iterator`/
  `Iterable` are already plain bases, so `class Foo(Generic[T])` is not misclassified as a framework
  base. Confirmed on sqlalchemy / werkzeug `Mixin(Base[K, V])`.
- **Enum machinery hooks `_missing_` / `_generate_next_value_` are rooted to their class.** These are
  single-underscore (not dunders) but invoked by name by the enum metaclass (`Color(x)` lookup miss;
  `auto()`); added to `_is_protocol_method` so the existing class→method seed keeps them (and their
  callees) live when the enum is reachable, dead otherwise.
- **pytest plugin hooks (`pytest_*`) in test files are rooted.** pytest discovers and invokes
  `pytest_configure`, `pytest_collection_modifyitems`, … by name from `conftest.py`/test-tree modules
  with no in-tree call site. New `_is_pytest_hook` roots module-level `pytest_*` functions (callback
  role), scoped to the `is_test_file` set. Regression + helper + mutation pinned.

## [2.1.10] — 2026-06-27

**Python IPython/Jupyter rich-display protocol hooks cardinal fix — found by dogfooding `rich`.**
Indexing the real `rich` library surfaced `JupyterMixin._repr_mimebundle_` flagged dead: the
IPython display protocol (`_repr_html_`, `_repr_png_`, `_repr_mimebundle_`, `_ipython_display_`, …)
is invoked **by name** by IPython when an object is displayed, but its methods are *single*-underscore
so the `__x__` dunder pass missed them.

### Fixed

- **A class's IPython/Jupyter rich-display hooks are now rooted to the class**, exactly like the
  interpreter dunders. When the class is reachable, its `_repr_*_` / `_ipython_*_` hooks — and
  whatever they reach — are live; a dead class's hooks stay dead (cardinal-safe). The set is the
  documented IPython rich-display protocol, not an open-ended name match. `_is_protocol_method` helper
  (shared with the dunder pass); regression + mutation pinned.

## [2.1.9] — 2026-06-27

**Runtime / native (FFI) entry-point directives across Rust, C#, and Go cardinal fix** — found by
continuing the doc-driven hunt into each language's runtime-entry surface. Each is a function the
runtime or native code invokes automatically with no in-tree caller, and (unlike the already-covered
`pub`/public forms) need not be public — so it and its callees were false-flagged dead at 0.6.

### Fixed

- **Rust `#[panic_handler]` / `#[start]` / `#[alloc_error_handler]` are rooted.** The runtime calls
  these automatically (on panic / as the program entry / on allocation failure); a non-`pub` one had
  no `pub` to trigger export-rooting. (`#[proc_macro]`/`#[proc_macro_derive]`/`#[proc_macro_attribute]`
  need no handling — they require `pub`, already rooted.) `_is_rust_runtime_entry_attr` helper.
- **C# `[UnmanagedCallersOnly]` is rooted** (added to the curated callback-attribute set, with
  `[JSInvokable]`). A method exported to native (C-ABI) callers is invoked from unmanaged code, not
  by a managed caller, and is typically non-public.
- **Go cgo `//export name` is rooted.** A function with `//export` directly above it is callable from
  C. A capitalised one was already exported by Go's rule, but a **lowercase** `//export lower_entry`
  was flagged dead; the directive (the func's preceding `comment`) now roots it.

All cardinal-safe (only add roots); `_is_rust_runtime_entry_attr` / `_go_has_export_directive`
helpers, regression + mutation pinned.

## [2.1.8] — 2026-06-27

**Recall: PHP bare-string function callables** (the last queued non-cardinal gap from the
`LIMITATIONS.md` audit).

### Fixed

- **A PHP global function passed as a bare-string callback to a known callback builtin is no longer
  flagged dead.** `usort($x, 'topcmp')`, `call_user_func('handler')`, `array_map('mapper', …)` etc.
  name a project function the syntactic call scan can't see; it now emits a REFERENCES edge to the
  named function (the bare-string analogue of the v2.0.1 array-callable form). Scoped to a curated
  set of callback-taking builtins (`_PHP_CALLBACK_BUILTINS`) so an ordinary string literal that
  merely matches a function name doesn't over-root. A `'Class::method'` string needs no handling (a
  static string call requires a public target, already rooted). `_php_string_callable_names` helper,
  regression + mutation pinned.

### Docs

- Corrected the JS export-indirections note: `export * from './m'` is **not** an unrooted form — it
  re-exports symbols `m` already exports inline (so they are already rooted). Verified (panel R86).

## [2.1.7] — 2026-06-27

**Recall: third-party Rust test harnesses + ByteBuddy/Moshi annotations rooted** (the non-cardinal
recall gaps from the `LIMITATIONS.md` audit). These under-reported live code as dead — never a
false-dead's opposite, just missed roots — and are now closed.

### Fixed

- **Common third-party Rust test attributes are recognized.** `#[rstest]`, `#[test_case(...)]`,
  `#[gtest]` (googletest-rust), `#[quickcheck]` — whose attribute path doesn't end in `test`, so the
  `test`/`*::test` convention missed them — now root the free-form-named test fn (and its helpers).
  Matched on the last path segment, so the crate-qualified form (`rstest::rstest`) is covered too.
- **ByteBuddy `@Advice.OnMethodEnter`/`@OnMethodExit` and Moshi `@ToJson`/`@FromJson` are rooted.**
  These framework-invoked Java methods (bytecode instrumentation / reflection adapters) were the
  documented external-framework-annotation gap (mockito/okhttp hunt); they are now on the curated
  Java callback-annotation set (role `callback`). Cardinal-safe (only adds roots).

## [2.1.6] — 2026-06-27

**C/C++ class-level export-attribute cardinal fix** (R80 Finding 2 — the last cardinal item from the
limitation audit).

### Fixed

- **Public methods of a class carrying a *class-level* export attribute are no longer flagged
  dead.** `class __attribute__((visibility("default"))) Foo { … };` / `__declspec(dllexport)` exports
  the class's whole public interface, so every public method is public ABI even with no per-method
  attribute. Their out-of-line `.cpp` definitions carry no attribute, so they (and their callees)
  were false-flagged dead at 0.6 (cardinal). The public method names declared in an export-attributed
  class/struct body are now collected (project-wide, into the same set as the header-declaration
  fix) and root the matching definitions. Covers all three member shapes (panel R81): a declared-only
  method, an **inline-defined** method (parses as `function_definition`, not `field_declaration`),
  and a **templated** method (`template_declaration`). `public` and `protected` are collected
  (protected is the extensibility ABI — reachable by out-of-tree subclasses); `private` is internal
  and stays dead-code-eligible. `struct` defaults public, `class` private. Cardinal-safe;
  `_c_public_method_names` helper, regression + mutation pinned.

## [2.1.5] — 2026-06-27

**C/C++ header-declaration export-attribute cardinal fix, from the limitation audit.** A review of
every documented limitation (per the maintainer's "fix it, don't document it" direction) promoted
the one genuinely-cardinal C/C++ item — flagged in panel R77 (F2) — from a documented tradeoff to a
fix.

### Fixed

- **A C/C++ function/method whose export attribute is on its (header) declaration is no longer
  flagged dead.** `__attribute__((visibility("default")))` / `__declspec(dllexport)` is commonly
  placed on the **declaration** in a header (`struct W { __attribute__((visibility("default"))) int
  compute(int); };` or a top-level `… int W::compute(int);`), while the out-of-line definition in
  the `.cpp` carries no attribute. The definition therefore had no in-tree caller and was
  false-flagged dead at confidence 0.6 — and so was everything its body reached (panel R77 F2,
  cardinal). The names of export-attributed declarations are now collected project-wide (declaration
  and definition live in different files) and root the matching definition by name — the C/C++
  analogue of Python's project-wide `__all__`. Cardinal-safe (over-roots a homonym only in the safe
  direction); `visibility("hidden")` and unattributed methods still flag. `_c_export_decl_names`
  helper, regression + mutation pinned; the AST walk is byte-gated so it is skipped on files with no
  export attribute.

## [2.1.4] — 2026-06-27

**C/C++ attribute-entry-point cardinal fix, from doc-driven hunting** (continuing the method that
found the Rust FFI-export gap). The GCC/Clang/MSVC attribute reference enumerates the function
attributes that make a symbol an implicit entry point or part of the public ABI — none of which
produce an in-tree by-name caller. A minimal fixture confirmed a whole cluster was false-flagged
dead (panel R73, cardinal).

### Fixed

- **C/C++ functions carrying an entry-point / export attribute are no longer flagged dead.**
  `__attribute__((constructor))` / `((destructor))` (and the C++ `[[gnu::constructor]]` / priority
  `((constructor(101)))` forms) run automatically before/after `main` — the C analogue of a static
  initializer or Go `init` — so the function definitely executes; `__attribute__((used))` /
  `((retain))` explicitly tells the compiler the symbol is used (a use it can't see by name —
  inline asm, a linker section); `__attribute__((visibility("default")))` and MSVC
  `__declspec(dllexport)` mark the public-ABI surface (the analogue of Rust `#[no_mangle]`). None
  has an in-tree caller, so each — and everything its body reached — was false-flagged dead. They
  are now rooted (`callback` for the runtime/used forms, `exported` for the visibility/dllexport
  forms). Also covered (panel R74): the GCC `__name__` synonyms (`__constructor__`, `__used__`,
  `__visibility__`, …) used in system headers; `__attribute__((section("…")))` (linker-collected
  init tables → callback) and `((weak))` (linker-visible overridable symbol → exported); and the
  **target** of `((alias("t")))` / `((ifunc("r")))`, which is kept live by name (the attributed
  symbol is itself a body-less declaration). `visibility("hidden")` and plain uncalled statics
  correctly stay dead-eligible. Cardinal-safe (only adds roots). `_c_attr_roots` /
  `_c_alias_target_names` helpers isolate the mapping; owned by regression tests and pinned by
  mutation.
- **An attribute absorbed by a preceding empty-body inline C++ method is recovered.** The
  tree-sitter C++ grammar parses an empty-body method (`void f() {}`) as a `field_declaration` and
  swallows the *following* method's leading attribute as a trailing one — so a
  `__attribute__((visibility("default")))` method right after an empty-body one lost its attribute
  and was false-flagged dead (panel R75, cardinal). The extractor now reattaches an attribute that
  sits after the prior `field_declaration`'s declarator to the current function. Cardinal-safe;
  `_c_dangling_attr_texts` helper, regression + mutation pinned.

## [2.1.3] — 2026-06-27

**Rust FFI/linker-export cardinal fix, from doc-driven hunting.** Reading each language's
reference enumerates its *complete* implicit-invocation surface (magic methods, operator
overloads, FFI exports, reflection hooks) — gaps a repo only surfaces if it happens to exercise
them. The Rust reference's "the `no_mangle` attribute" / `export_name` made a gap visible that no
scanned repo had hit: a non-`pub` `#[no_mangle]` export. A minimal fixture confirmed it was a
cardinal false-positive (panel R69).

### Fixed

- **Rust `#[no_mangle]` / `#[export_name]` functions are no longer flagged dead.** These
  attributes export the function's symbol to the linker / foreign (C) code regardless of `pub`
  visibility — the function is a public-ABI entry point with no in-tree caller (the Rust analogue
  of C's `EXPORT_SYMBOL`). A `pub fn` was already export-rooted, but a non-`pub`
  `#[no_mangle] extern "C" fn` (valid Rust — the symbol is still exported) had no `pub` to trigger
  that rooting, so it *and everything its body reached* were false-flagged dead (cardinal). Such
  functions are now rooted `exported`. Covers the bare form *and* the wrapped forms —
  `#[unsafe(no_mangle)]` / `#[unsafe(export_name = "…")]` (the **required** spelling in the Rust
  2024 edition; panel R70) and `#[cfg_attr(<pred>, no_mangle)]` (conditionally applied). Cardinal-safe
  (only adds roots). A `_is_rust_export_attr` helper isolates the attribute test; owned by
  regression tests and pinned by mutation.

## [2.1.2] — 2026-06-26

**C# custom-attribute cardinal fix, from continuing the cross-language hunt** (jackson-core,
mockito, okhttp, serilog). Most findings were the documented external-framework-annotation
limitation (ByteBuddy `@Advice.*` on mockito's advice methods, Moshi `@ToJson`/`@FromJson` on
okhttp's sample adapters — unrecognised framework annotations, now cited in LIMITATIONS).
serilog surfaced a clean, general extraction bug.

### Fixed

- **C# in-tree attribute classes used via `[Foo]` are no longer flagged dead.** C# applies an
  attribute with the `Attribute` suffix omitted — `[NoEnumeration]` names class
  `NoEnumerationAttribute` — so the bare reference never resolved, and a custom attribute class
  used only via `[Foo]` was false-flagged dead (serilog `NoEnumerationAttribute`, applied in
  `Guard.AgainstNull`; panel R64, cardinal). An `attribute` usage now also emits the suffixed
  reference (`Foo` → `FooAttribute`), so the attribute class is kept live. Cardinal-safe (adds a
  reference only; the suffixed name resolves only if such a class exists). Covered in both
  `_direct_refs` (attributes on methods/classes/structs) and `_module_uses` (attributes on
  `enum`/`delegate` declarations, which aren't in the C# `defs` set; panel R66). Owned by
  regression tests.

## [2.1.1] — 2026-06-26

**Ruby operator-method cardinal fix, from dogfooding across a Rust/Go/Ruby hunt** (serde, clap,
gorm, cobra, gin, logrus, grape). Go and Rust came back clean (0 cardinal false-positives;
serde's library is fully live, only test-suite/trybuild fixtures flag). Ruby surfaced a real
one.

### Fixed

- **Ruby operator methods (`def []`, `def []=`, `def <=>`, `def ==`, `def <<`, …) are now
  captured and rooted.** Their name node is tree-sitter type `operator`, which `_trailing_id`
  didn't recognize — so the method was dropped from the graph entirely. Two consequences, both
  fixed: (1) the operator method was un-navigable / un-analyzable; (2) anything used *only*
  inside its body was false-flagged dead — e.g. in grape, `def []=` does `ValueArray.new(value)`,
  so `ValueArray`'s constructor was flagged dead though the class is instantiated (cardinal,
  panel R61). Operator methods are invoked through operator/index **syntax** (`a[k]`, `a[k]=v`,
  `a <=> b`, `sort`), never a by-name call, so they're rooted as `callback` — the Ruby analogue
  of the existing C++ special-member pass. Cardinal-safe (only adds roots). Owned by a
  regression test.

## [2.1.0] — 2026-06-26

**Constant-memory *queries*, from dogfooding v2.0.1 across a multi-repo Python hunt** (Django,
Salt, Ansible, CPython stdlib, Home Assistant). v2.0.0 made *indexing* memory-bounded; this
release extends that to the *reachability* sweeps so `find_stale` / `impact_of` scale to the
graphs the indexer can now build.

### Changed

- **Reachability streams its adjacency (the find_stale scalability ceiling).** Home Assistant
  indexed fine — 6,728 files, **~16M edges**, ~4 GB — but `find_stale` then OOM'd: every
  reachability/centrality sweep funnelled through `Store.resolved_edges()`, which does
  `SELECT * … fetchall()` and builds **all 16M `Edge` objects at once**. A new lean
  `Store.iter_resolved()` streams `(src, relation, dst_id, weight)` tuples cursor-by-cursor;
  `algebra._Adjacency` and the `reach.py` sweeps (`reachable_from`, `reverse_reachable_from`,
  `fan_in`/`fan_out`, `best_path`, SCC) build directly from it. Byte-identical results (the
  GraphBLAS==pure-Python and incremental/streaming oracles stay green); peak on a 6M-edge graph
  dropped to ~840 MB (was multi-GB), so a 16M-edge graph now queries in ~2 GB instead of OOM.

### Fixed

- **SQL resolver no longer treats prose as SQL.** `_sql_literals` matched any string whose
  *first word* was a SQL verb, so English docstrings — `"Create a list…"`, `"Update the…"`,
  `"Delete a…"`, `"With this…"` — were handed to sqlglot, flooding output with parse warnings
  (hundreds per file on Django/Salt) and occasionally minting phantom tables. It now requires
  real statement structure (`SELECT … FROM`, `INSERT INTO`, `UPDATE … SET`, `DELETE FROM`,
  `CREATE <table|index|view|…>`, `WITH … AS … SELECT`). Real queries still resolve; prose
  doesn't. Owned by a regression test.

### Docs

- **LIMITATIONS:** documented plugin-loader / string-name dynamic dispatch (Salt's loader,
  pluggy, entry-point registries) as a static-analysis blind spot — on Salt 3008, `find_stale`
  flags 3,907 loader-dispatched functions that are live at runtime. Escape hatch: pin the public
  surface via `[entry_points]` globs or `ingest_trace`.

## [2.0.1] — 2026-06-26

**PHP precision fix from dogfooding v2.0.0 on Magento.** Running `find_stale` on the Magento
Framework surfaced a cardinal-class false-positive: methods invoked through PHP's
**`[$this, 'method']` callable-array idiom** (the `usort` / `uasort` / `preg_replace_callback`
comparator pattern) were flagged dead, because the method name is a *string*, not a syntactic
call, so the call scan never saw it.

### Fixed

- **PHP array callables are now recognized** (tree-sitter extractor). A 2-element callable
  array `[$this|self::class|static::class|'Class'|$obj, 'method']` — the `usort` / `uasort` /
  `preg_replace_callback` / `array_map` comparator idiom — emits a REFERENCES edge to `method`.
  Cardinal-safe — only project symbols resolve, so a non-callable 2-element array that happens
  to name a method merely over-roots (masking dead code), never causing a false-dead. On the
  Magento Framework this removed 9 false-positive dead-flags (39 → 30 PHP candidates) while
  still flagging genuinely-unused private methods. Byte-identity (streaming == full) is
  preserved — the new edges flow through the shared extractor path. Owned by a regression test.
  (A `'Class::method'` *string* callable needs no handling: a string static call requires a
  PUBLIC target, which is already rooted as exported.)

## [2.0.0] — 2026-06-26

**Constant-memory streaming indexer.** The major feature of v2: `reindex` can now stream the
graph straight to SQLite instead of building it all in Python first, so peak memory tracks
one file's working set — not the size of the whole repo. Tens-of-thousands-of-file monorepos
(Magento, 24k PHP files) that used to need >12 GB and OOM now index on a laptop. The streamed
index is **byte-identical** to the in-memory one, pinned by a differential oracle across
Python + JS/TS + Go + Ruby + C/C++ + Rust + PHP.

Measured on the Magento `Framework` core (4,304 PHP files → 30,412 nodes, ~15.5M raw edges):
**reindex peak 3,183 MB → 269 MB (~12×), output identical** (3,926,345 edges / 30,412 nodes
verified row-for-row), ~40% slower.

### Added

- **`reindex(store, path, streaming=...)`** — tri-state:
  - `None` (default) — **AUTO**: streams when the store is on-disk AND the tree is large
    (≥ 2,000 indexable source files); small repos keep the slightly faster in-memory path.
  - `True` / `False` — force streaming / in-memory.
  Exposed on the CLI (`--streaming/--no-streaming`) and MCP. Streaming saves memory only with
  an on-disk `Store` (a `:memory:` DB holds rows in RAM regardless), so AUTO never picks it
  for `:memory:`.
- **Streaming differential oracle** (`tests/oracles/test_streaming_differential.py`) — the
  release gate for the streaming path: `streaming == full`, byte-for-byte, on a polyglot
  corpus plus a heavy-fan-out / cross-group stress fixture.
- **`scripts/mutate.py --only <names>`** — restrict the mutation meta-oracle to specific
  functions/classes, so the streaming-critical core is pinned by a fast, targeted kill-signal.

### Changed

- **How extraction streams (internal).** Each file's AST / tree-sitter parse-tree **and**
  source bytes are dropped after pass 1 (only a tiny per-definition record survives into pass
  2); edges are deduplicated per-source on the fly and written to SQLite in committed batches,
  so neither the parse trees nor the millions of edges are ever all resident at once. Output
  is unchanged. `extract_project` / `treesitter.extract` gained internal `cache_asts` /
  `cache_trees` / `edge_sink` parameters; their public `(nodes, edges)` return is unchanged.
- **`Node` / `Edge` use `__slots__`** — lower per-object overhead at scale.

### Fixed

- **`name_based` consistency between the in-memory and store dedups (panel R50).** The
  in-memory `_dedup_edges` now ORs the `name_based` flag onto a `(src, relation, dst_id)`
  group's survivor — matching the store's `_dedup_resolved_edges` (the R23A rule). Without
  this, `reindex(precise=True, streaming=True)` could diverge from `streaming=False` on
  `name_based` (jedi's precise edge and the extractor's name-based edge to the same target
  land in different sink groups, so the store's OR fires while the in-memory path kept the
  precise survivor's flag). Cardinal-safe: a pure-precise group still keeps `name_based=False`,
  so a precise resolution is never wrongly made re-widenable (R22A). The streaming differential
  oracle now compares `name_based` (and `weight`/`provenance`) and includes a `precise=True`
  (jedi) case.

### Notes / trade-offs

- Streaming reindex commits in batches rather than as one transaction, so a crash mid-rebuild
  can leave a partial index; a re-run rebuilds cleanly (it clears first). The default
  in-memory path remains crash-atomic. AUTO only engages streaming for large on-disk repos —
  exactly where the in-memory alternative is an OOM.
- The previous top deferred limitation ("very large monorepos indexed as one in-memory
  graph") is **resolved**; see `LIMITATIONS.md` and `docs/V2_STREAMING_DESIGN.md`.

## [1.0.7] — 2026-06-26

**Precision release from a multi-repo / multi-language false-positive hunt.** stitchgraph
was run against ~47 real-world projects across 9 languages — including code *designed* to
break parsers (IOCCC obfuscated C), and large/messy corpora (Linux kernel core, WordPress,
Magento, PrestaShop, symfony) — to ground-truth `find_stale` against actual liveness. The
hunt surfaced a family of **cardinal-class false-deads** (live code flagged dead) caused by
entry-point/liveness signals stitchgraph did not yet model. Every fix only ever *adds* roots
(precision-safe). **Robustness held: 0 crashes across all corpora** (the 1.0.6 RecursionError/
FIFO/large-file guards survive obfuscated and machine-generated C).

### Added (new entry-point / liveness signals — all cardinal-safe)

- **`setup.cfg [options.entry_points]`** parsed alongside `pyproject.toml` — `console_scripts`,
  `gui_scripts`, and plugin groups (e.g. `flake8.extension`/`flake8.report`); class targets
  root their public methods too.
- **src-layout (`src/`) absolute-import resolution** — a PyPA `src/pkg/…` project's absolute
  imports (`from pkg import …`) now resolve (incl. **PEP 420 namespace packages**, no
  `__init__.py`), so module-load-only-live code isn't flagged dead. Node ids are unchanged;
  the module lookup gains a src-stripped alias.
- **Inherited public methods of an exported class** are rooted (a base-class method like
  `Flask`'s `shell_context_processor`, used on an instance, is public API).
- **Framework callbacks across more languages:** Java/C# reflection **annotations/attributes**
  (`@PostConstruct`, `[OnSerializing]`, …); JS/TS **decorators** (NestJS/Angular/TypeORM —
  `@Controller`/`@Get`/`@Entity`/…); transitive & self-named external-base callback classes
  (`FlaskGroup→AppGroup→click.Group`; `EnvironBuilder(werkzeug…EnvironBuilder)`); Ruby
  `const_missing`/`const_added` and more implicit hooks.
- **C/C++ `EXPORT_SYMBOL(...)`** roots the named function as public kernel/module ABI (the C
  analogue of `__all__`/`module.exports`).
- **JS/TS member-assigned functions** (`app.render = function(){…}`, `X.prototype.m = …`,
  `module.exports.x = …`) are modeled and their bodies walked, so helpers they call aren't
  flagged dead; module-scope ones are rooted (function-nested ones stay reachability-gated).

### Changed

- **Skip dependency/vendored/build dirs** when indexing — one shared set across both
  extractors: `node_modules`, `vendor`, `third_party`, `bower_components`, `target`, `.gradle`,
  plus the existing `.venv`/`build`/`dist`/`.git`/… (conventionally non-first-party).
- Don't extract a **bodyless C/C++ struct/enum/union** as a phantom dead class.

### Notes

- **Documented scalability limit:** `reindex` builds one in-memory graph, so peak RAM scales
  with repo size — a tens-of-thousands-of-file monorepo (Magento, 24k files) exceeds ~12 GB;
  index sub-trees or provision RAM. (A streaming/constant-memory indexer is the next big item.)
- New documented cardinal-safe over-rooting tradeoffs (flat-name export collisions; member-
  assigned methods) — see `LIMITATIONS.md`.

**Hardening rounds (R40–R42).** A full-diversity panel campaign (opus/sonnet/haiku) over the
session's diff, with the three-layer gate (adversarial panel + `tests/oracles/` + mutation
meta-oracle). The panels found and fixed, at root cause, 4 over-rooting/recall defects the
features introduced (R40A script-class over-root; R40B/R41A comment dropping a decorator/
attribute marker across JS/TS **and** Rust; R40C member-assignment-in-dead-function rooting;
R42A namespace-package src-layout false-dead; R46A member-assigned-class methods flagged
dead) — each owned by a regression test, and the src-layout incremental defect class newly
owned by the differential oracle. Released on **two consecutive full-diversity
(opus+sonnet+haiku) clean panels (R47–R48)**; `scripts/readiness.py` confirms RELEASABLE. The
maintainer applies the tag.

## [1.0.6] — 2026-06-25

**Field-fix patch — entry-point coverage (#20/#21/#22) that grew into a robustness +
cross-language cardinal-hardening release.** Under sustained multi-model adversarial-panel
review, the entry-point work surfaced a family of cardinal-class false-deads (live code
flagged dead) across the tree-sitter languages — Python↔tree-sitter rooting asymmetries and
grammar/extension edge cases — plus crash/hang hardening (FIFO/special files, malformed
coverage JSON and `stitchgraph.toml`, deep-AST `RecursionError`) and a documentation
correction. The cardinal fixes only ever *add* roots (precision-safe). Confirmed by full
three-model panels.

**Final hardening rounds (R33–R39).** A deeper full-diversity panel campaign (opus×2 ·
sonnet×2 · haiku×2 per round) plus a three-layer release gate — adversarial panel +
deterministic oracle suite (`tests/oracles/`) + in-house mutation meta-oracle
(`scripts/mutate.py`) — drove the last veins of the cardinal/inflation classes to closure,
each owned by a regression test or oracle so future panels don't re-spend budget:

- More language execution-model rooting: Ruby/PHP module-level scripts, Go package-directory
  `var`/`init` initializers, C++ translation-unit static initializers (reachability-driven),
  and the module/symbol id-collision case (`Service.js` + `class Service`).
- `replace_file` incremental convergence: runtime-role preservation, language-aware name
  resolution (no cross-language bind), and exact cross-file `exported`-role convergence via a
  new `exported_ids` parameter.
- Provenance-demotion completed across the column (`trace_path` joined
  `impact_of`/`get_callers`); `get_matrix`/`summarize_subsystem` id-boundary scoping;
  coverage path-suffix and bool-as-int fixes; envelope non-finite clamp + `_plain` covering
  all result/meta values.

Released on a **two-consecutive-3-layer-clean** gate (rounds R38–R39: panel clean + oracle
suite green + mutation clean), RRS 93.3/100. The maintainer applies the version tag.

### Fixed

- **CARDINAL: a tree-sitter framework-subclass is no longer flagged dead while its methods
  are live.** `_seed_callback_roles` (tree-sitter) marked callback *methods* with the
  `callback` role but never marked the enclosing *class* — the Python extractor's
  `_apply_callback_roles` has a `classes_with_callbacks` second pass that the tree-sitter
  side was missing (a Python↔tree-sitter symmetry gap). So a framework subclass in any
  tree-sitter language (a Rails `ApplicationController`, a React `Component`, …) that wasn't
  otherwise exported or constructed had its **class** surface as dead code while its hook
  methods stayed live — the only release-blocking class of bug (live code flagged dead). The
  class-rooting pass is now mirrored. Tie is to *having* callback methods, so a bare unused
  subclass with no overrides still flags.
- **CARDINAL: C/C++ in-class member functions now root their class.** C/C++ map every
  `function_definition` to FUNCTION (there is no separate method node), so the five
  method-based class-rooting passes (exported / test / callback / main / constructor), which
  key on METHOD, silently skipped every C++ method — a live Qt/framework subclass and its
  methods were flagged dead. In-class member functions are now normalized to METHOD, so all
  five passes work for every language.
- **CARDINAL: a C# `internal` entry-point class is no longer flagged dead.** Idiomatic C#
  (`internal class Program { static void Main }`) has a non-public `Main`, so the class never
  gets the `exported` role, and the tree-sitter extractor had no pass to root the enclosing
  class of a `main`-role method (the Python extractor does). A new `_seed_main_classes` pass
  mirrors the Python rescue.
- **CARDINAL: public interface/trait members are no longer flagged dead.** Members of an
  exported interface/trait are public API but, being implicitly public (no visibility token),
  never got a per-method `exported` role, and the down-propagation pass was gated to JS/TS —
  so a `pub trait` (Rust), `public interface` (Java/C#), or interface/trait (PHP) member,
  including body-bearing `default` methods, was flagged dead. A new
  `_seed_exported_interface_methods` pass down-propagates `exported` from the container.
- **CARDINAL: a C++ class in a `.h` header is no longer flagged dead.** `.h` was mapped to
  the C grammar, which has no class/namespace/template — so a C++ class in a `.h` header
  (the dominant C++ header extension; header-only and split header/source layouts are
  ubiquitous) mis-parsed and surfaced as dead code. `.h` is now resolved to C or C++ by
  content.
- **CARDINAL: a module node colliding with a same-named symbol no longer loses its role.**
  A bash `run.sh` defining `run()`, or a JS test file `tests/Service.js` defining
  `class Service`, produced two nodes with the same id; the store's `INSERT OR REPLACE`
  dropped the MODULE node and with it its module-only role (`script`/`test`, which has no
  redundant assignment), flagging the whole file's code dead. A shadowed module node's roles
  are now merged into the surviving symbol node.
- **CARDINAL: a C++ class/struct in a `.h` header used from a `.cpp` is no longer flagged
  dead.** C and C++ were separate name-resolution buckets, but real projects reference symbols
  freely across `.h`/`.c`/`.cpp` (and a `.h` may be parsed under either grammar), so a header
  type used by the other dialect never resolved. C and C++ now share one resolution bucket
  (precision-safe — it only adds edges). The `.h`→C/C++ content sniff was also broadened
  (access specifiers, `virtual`/`operator`/`nullptr`) so struct-with-methods headers route to
  C++ and their members are extracted.
- **CARDINAL: Rust trait-impl methods are no longer flagged dead.** A method in an
  `impl Trait for X` block can't carry `pub` and is invoked via language sugar
  (`Display::fmt` via `{}`, `Iterator::next` via `for`, operator overloads) with no call node,
  so it got no `exported` role and no inbound edge. A new `_seed_trait_impl_methods` pass roots
  trait-impl methods (a bare inherent `impl X` still flags).
- **A JS/TS `export { X }` no longer roots a same-named symbol in another language**
  (a dead Ruby/Go/… `X` was hidden by an unrelated JS re-export). The reexport pass is now
  language-guarded — a precision (false-negative) fix, not cardinal.
- **CARDINAL: a class with any reachable member is never flagged dead.** General invariant
  (a live method implies a live class — the class must exist for the method to run) added to
  `find_stale`'s candidate filter, as the backstop for the whole "class dead while a member
  is live" family across every language/idiom (callback/main/exported/interface/trait, and
  C# `partial class` parts split across files). A class is flagged only when it AND all its
  members are unreached. (Side effect: stitchgraph's own `ConfigOnlyDetector` is now kept live
  — its `detect` is reachable via the `EntryPointDetector` protocol call — so dogfood is now
  2 advisory, was 3.)
- **CARDINAL: C# top-level statements root their local functions.** A `.cs` file using the
  default .NET 6+ top-level-program form (`Program.cs` with no explicit `Main`) is the
  program's entry point, like bash's top-level body and Python's `__main__`; its local
  functions were flagged dead. Such files are now rooted as a `script`.
- **`find_symbol`/`impact_of`/`trace_path`/`get_callers`/`get_callees` no longer crash on a
  non-UTF-8 symbol name.** A lone surrogate (invalid-UTF-8 argv decoded via `surrogateescape`)
  or embedded NUL bound into SQLite raised `UnicodeEncodeError`/`ValueError`; the store lookups
  now refuse (no match) so the op returns a `Result` instead of a traceback.
- **A resolver can no longer abort `reindex`.** Resolvers are heuristic enrichment, but a
  `DELETE TABLE …` SQL string in analyzed source made sqlglot hand the SQL resolver a `bool`
  `.this`, raising `AttributeError` and crashing the whole reindex. `run_resolvers` now skips
  a resolver that raises (the base graph + other resolvers are unaffected), and the SQL
  resolver guards on `Expression` before walking. A repo with stray SQL no longer fails to
  index.
- **`ingest_trace` no longer OOMs on a corrupt Go coverprofile.** A line with a huge end-line
  expanded `range()` into a multi-GB set; the span is now bounded (>1M lines dropped),
  matching the JSON/LCOV "empty on any problem" hardening.
- **Path-taking ops refuse instead of crashing on a hostile path.** An over-long path,
  embedded NUL, or lone surrogate passed to `reindex`/`ingest_trace`/`risk` (or read by
  `find_config`/`load_coverage`) raised `OSError`/`ValueError`/`UnicodeError` from a
  `stat()`/`resolve()`/bind. `reindex` now degrades to an empty index (like a missing path);
  the others refuse cleanly.
- **A malformed `[review] threshold` no longer silently disables review.** `threshold = "nan"`
  (or out-of-range) made `confidence < nan` always False; it now clamps to the default.
- **`reindex` no longer aborts on a pathologically deep source file.** A huge flat
  expression (`X = a + b + c + …`, realistic in generated SQL/HTML/string-builder code)
  overflows the recursive AST walk with `RecursionError`, which was not in the per-file
  `except (SyntaxError, UnicodeDecodeError, OSError)` and ran outside the `try` — so a single
  bad file aborted the **entire** reindex and left an empty DB. `RecursionError` is now
  caught per file in both extractors and the resolver `parse()` helper, honouring the
  "skip the one file, never abort the whole reindex" contract. The route resolvers
  (express/jsfetch/spring) run their **own** recursive descent over a tree-sitter tree,
  bypassing that helper, so `run_resolvers` now also guards each resolver — a deep `.js`
  expression degrades to "no extra edges" instead of aborting the reindex. (Library hygiene:
  the SCC passes that raise `sys.setrecursionlimit` now restore it; the tree-sitter
  RecursionError skip no longer leaves an orphan module node.)
- **`[project.scripts]` / `[project.entry-points]` console entry points are detected as
  roots** (issue #21). `design.md` §4 lists them as roots and `PythonLibraryDetector`
  already collected a `script` role, but nothing parsed `pyproject.toml` to set it — so a
  CLI's `main` with no internal caller was false-flagged dead (the common case, including
  stitchgraph's own `stitchgraph = "...cli:main"`). The Python extractor now reads
  `[project.scripts]`, `[project.gui-scripts]`, and `[project.entry-points.*]` and tags the
  target with role `script`, matched by object name **and** module path so a same-named
  function elsewhere isn't mis-rooted.
- **A bash script's top-level body is a root** (issue #22 — the bash analogue of #8). A
  script that runs its work as bare top-level statements (no `main()`) had zero entry-point
  seed and zero inbound edges to its functions, so `find_stale` flagged all of them. Each
  bash script's module node is now seeded as a root (bash's `__main__`) and its top-level
  calls are rooted — direct, via `$(...)` command substitution, and via `trap NAME` — so
  those functions are correctly live. A function reached by nothing (including its own top
  level) still flags, exactly as intended.
- **`reindex` no longer hangs on a FIFO / special file.** `open()` on a named pipe with no
  writer blocks forever, and the `except OSError` guards never fire (the open doesn't
  error, it blocks). Every file walk that reads bytes/text now skips non-regular files via
  `path.is_file()`: the Python and tree-sitter extractors, the resolver `parse()` helper,
  and the four route/template resolvers that do their own `rglob` walk
  (`express`, `jsfetch`, `spring`, `html`) — the last of which run on every `reindex`, so a
  FIFO named `*.js`/`*.java`/`*.html` would otherwise hang the primary entry point. The
  fixed-path `pyproject.toml` read in `_console_script_targets` (the #21 path, run on every
  reindex) was also guarded with `exists()` — which is `True` for a FIFO — and is now
  guarded with `is_file()`, so a FIFO named `pyproject.toml` no longer hangs reindex.
  `load_coverage` (reached via `ingest_trace`) was hardened the same way: a FIFO trace path
  now returns empty (its documented "empty on any problem" contract) instead of blocking.
- **`ingest_trace` no longer crashes on a structurally-malformed coverage.py JSON report.**
  `_parse_json` guarded `executed_lines` *values* but assumed the `files` object — and each
  per-file entry — was a dict, so valid JSON of the wrong *shape* (`files` a list, an entry a
  string/null, `executed_lines` a dict) raised an uncaught `AttributeError` through the public
  `ingest_trace` op/CLI. It now isinstance-gates the shape and degrades to empty, matching the
  "empty on any problem" contract and the already-tolerant LCOV/Go parsers.
- **A malformed `stitchgraph.toml` no longer crashes every CLI command.** `_load` chained
  `.get().get()` over the config's sections, so a hand-edited file with a section that is not
  a table (`entry_points = "oops"`), a non-numeric `threshold`, or a non-list `include` raised
  `AttributeError`/`ValueError` — and config is read on every command. Each section/value is
  now shape-guarded and falls back to its default (the same robustness sweep that covered the
  coverage-JSON and FIFO cases).
- **`risk` no longer silently drops unicode-named source files.** `gitrisk._commits` parsed
  `git log --name-only`, but git octal-escapes and double-quotes non-ASCII paths under the
  default `core.quotepath=true` (`"caf\303\251.py"`), so the trailing quote defeated the
  source-extension filter and unicode-named files vanished from churn / co-change / `risk`
  hotspots. It now runs git with `-c core.quotepath=false` (and strips any residual quoting),
  so those files are counted.

### Documentation

- **`find_holes` scope documented** (issue #20). `find_holes` reports references orphaned
  by edits (delete/rename), not first-index calls to undefined/stdlib names — the
  extractors deliberately drop unresolved calls (precision over noise). `LIMITATIONS.md`
  and `AGENTS.md` now say so and point to `scan`, which **does** deliver the
  `is_stub ∧ reachable` "landmine" (design §6.D) as its `live_stub` finding.

## [1.0.5] — 2026-06-24

**Field-fix patch — CLI/UX papercuts (issues #18, #19).** Three small fixes found while
running v1.0.4 against a real repo; no analysis-engine changes. Confirmed by full
three-model panels.

### Fixed

- **`risk` is scoped from the indexed root, like every other read op** (issue #18).
  `risk`'s git-history `--path` defaulted to the process **cwd**, so `risk --db <db>` from
  anywhere but the analysed repo failed with `'.' is not a git repository` — even though
  the DB already records the indexed root (and `risk` reads it for path-mapping two lines
  later). It now defaults `--path` to the indexed root stored in the DB; pass `--path` to
  override. `risk --db <db>` now works from any directory. The `report` command had the same
  bug (it passed `repo="."` to `risk`, silently skipping its risk section from a foreign cwd)
  and gets the same fix — `report --db <db>` now includes risk from anywhere.

### Added

- **`stitchgraph --version`** (issue #19). Prints the installed package version *and* the
  active `tree-sitter-language-pack` line (bundled vs. download model) — the two things a
  bug report needs, given the version-keyed install model (#12). There was previously no
  way to confirm the installed version from the CLI.

### Fixed (cont.)

- **`stitchgraph.__version__` no longer drifts stale.** The literal had been left at
  `"1.0.3"` through 1.0.4/1.0.5; it now derives from the installed distribution metadata
  (`importlib.metadata`), the same source `--version` uses, so the attribute and the CLI
  always agree and there's nothing to bump by hand.

### Changed

- **`docs/design.md` §9 reconciled with the CLI** (issue #19). The operation surface listed
  a `path?` argument on `orient`/`find_stale`/`find_holes`/`scan` that the CLI never
  accepted. Scope comes from the **indexed graph** (`--db`), not a per-call path filter, so
  the `path?` is dropped and a "On scope" note documents the actual model (index the subset
  you want; `risk`'s `--path` is the git root, not a query filter). The §9 table was also
  scrubbed of stale entries: the phantom `structure_smells()` row (never a registered op —
  its output is part of `scan`), the non-existent `relations?` argument on `trace_path`, and
  the unbuilt `type_at` primitive (a documented LSP roadmap item that lives in `STATUS.md`,
  not the shipped-operation surface). §9 now lists only registered operations.

## [1.0.4] — 2026-06-24

**Field-fix patch — confidence honesty for receiver calls and structural findings
(issues #10, #11, #15).** Three reporting/confidence fixes from the field audit, none
changing reachability: every one keeps the cardinal invariant (live code is never flagged
dead). Confirmed by full three-model panels.

### Fixed

- **Receiver-based calls resolving to a single same-named symbol are now `INFERRED`, not
  `EXTRACTED`** (issue #10). A call like `obj.save()` whose name matches exactly one
  project definition was asserted at full `EXTRACTED` confidence — but without type
  inference the receiver's type is unknown, so it may be a homonym `save` on a different
  (stdlib/third-party) class. The edge is now labelled `INFERRED` (a guess). In the
  **tree-sitter** extractor this is detected receiver-aware across all languages
  (member/field/selector/scoped access, plus Java `object` / Ruby `receiver` fields); every
  receiver call is demoted, while direct calls (`save()`) and constructors stay
  `EXTRACTED`. In the **Python `ast`** extractor, scope-aware resolution still wins first —
  `self.save()` and a locally-typed `r = Repo(); r.save()` stay `EXTRACTED`; only an
  unknown-receiver fallback (`x.save()`) is demoted, removing the Python↔tree-sitter
  asymmetry. **Weight is unchanged (1.0)** everywhere, so the edge still counts fully for
  reachability / `find_stale` — only the asserted confidence is lowered, never the liveness
  (cardinal-safe).
- **`scan` structural findings now reflect the provenance of the edges they rest on**
  (issue #11). A cycle or god-object that exists *only* because of `AMBIGUOUS`
  (over-approximated homonym) or `INFERRED` (heuristic) edges was reported at the same
  🟠 urgency as one backed by confident `EXTRACTED` edges — on a language without type
  resolution this made most structural findings indistinguishable artifacts. Each cycle /
  god-object now carries a `confidence` and `needs_review` derived from its participating
  edges, reports the confident-only degree, and is capped to 🟢 (sinking in the ranking)
  when the coupling is dominated by name-ambiguous/heuristic edges. Confidently-linked
  findings keep their 🟠 "look closer."

### Changed

- **`LIMITATIONS.md`: corrected the `--precise` escape-hatch description** (issue #15). It
  no longer implies `--precise` "disambiguates" by pruning the `AMBIGUOUS` siblings; it
  documents the actual **additive** behaviour (adds a confident go-to-definition edge,
  never removes the competing candidates) and why that is deliberate — pruning a live
  symbol's only caller on a single jedi mis-resolution would be the cardinal sin.

## [1.0.3] — 2026-06-24

**Field-fix patch — offline-by-default grammars (issue #12).** `tree-sitter-language-pack`
changed its install model at v1.0.0: it stopped bundling grammars in the wheel and
switched to downloading them from a GitHub release on first use — which breaks offline /
CI / air-gapped installs and undercuts the "local-first" promise. The 1.0.1 `<2` bound did
not fix this (the download line is `<2`). Verified the bundled cutoff on PyPI (0.1.2–0.13.0
bundle; 1.0.0+ download) and that all 12 grammars stitchgraph uses are MIT-licensed.
Confirmed by a full three-model panel (JJ).

### Fixed

- **`pip install 'stitchgraph[treesitter]'` is offline / self-contained again** (issue #12).
  The `[treesitter]` extra now pins the **bundled** grammar line
  (`tree-sitter-language-pack>=0.7,<1.0`, `tree-sitter>=0.25.2,<1`), whose wheels ship the
  compiled parsers — no network at runtime. Verified end-to-end in a clean venv: all 12
  grammars load and a full multi-language reindex runs with the network off.

### Added

- **Opt-in `[treesitter-download]` extra** — the `1.x` line (smaller wheel + newest grammars,
  fetched over the network on first use) for users who want the latest grammars and have
  runtime network.
- **Adaptive grammar loader** (`_load_grammar`): loads a grammar the easiest available way —
  bundled loads directly; if missing and the installed pack supports it, downloads once and
  retries; a genuine failure still surfaces as the issue-#7 warning (file skipped, never a
  silent empty graph). Behaviour-preserving: the extraction graph is byte-identical.
- **`stitchgraph doctor`** (+ `--strict`) — a grammar self-check reporting the pack version,
  bundled-vs-download model, cache dir, and which supported grammars load. `--strict` exits
  non-zero if any can't load (a CI gate that the polyglot graph will be complete).

## [1.0.2] — 2026-06-24

**Field-fix patch — export-rooting + test call-receivers.** After 1.0.1 shipped, the
review panel was restored to full three-model diversity (opus + sonnet + haiku). The
freshly-returned **sonnet** immediately caught two cardinal false-deads that the
opus/haiku pair had missed across the entire 1.0.1 cycle, and the follow-up panels
surfaced a third. Fixed as a batch and confirmed by two consecutive full-diversity
clean panels (HH + II → readiness RELEASABLE). All three are the cardinal class — live
code flagged dead by `find_stale`. See the [release notes](docs/RELEASE_NOTES_v1.0.2.md).

### Fixed

- **JS/TS/CJS public exports are no longer flagged dead** (precision; panels FF/GG/HH).
  Only `export { X }` and inline `export class/function` were recognized, so a symbol
  defined and then exported separately was reported stale. Now the whole export-rooting
  class is closed: `export default Foo;` (the canonical React/Angular/Vue/Node idiom —
  pre-existing since 1.0.0), CommonJS `module.exports = Foo` / `module.exports = { A, B }`
  / `exports.x = Foo`, and TypeScript `export = Foo`. Matched precisely — anonymous
  defaults (`export default () => {}`) and locals named `exports` are not over-rooted, so
  genuinely-dead code still flags.
- **A class used only as a call receiver in a test block stays live** (precision; panel
  FF — a regression the 1.0.1 polyglot work introduced). In a call-based suite a class
  referenced as `Service.run` inside an RSpec/Jest `describe`/`it` block got a `CALLS`
  edge to the method but no reference to the class, so the live class was flagged dead.
  The test-file module scan (`_module_uses`) now collects name-references like the per-
  function scan does, and no longer descends into uncalled function-expression bodies
  (so a dead class referenced only inside an uncalled helper still flags).

## [1.0.1] — 2026-06-24

**Field-fix patch.** Three issues raised against 1.0.0 in real use on a Rust
crate (#7/#8/#9), plus — once #8 revealed the same test-detection gap in every
other language — a **polyglot generalization of test detection**. Fixed as a
batch and confirmed by nine review panels (W–EE). The cardinal invariant — live
code is never flagged dead — is preserved throughout; the panels found and closed
four successive cardinal gaps in cross-language test-class liveness (direct →
inherited/nested → combined fixed point → Python/tree-sitter `is_test_file`
asymmetry), with Panel DD declaring the class **closed** and **DD + EE giving two
consecutive clean panels at full diversity (the release gate)**. See the
[release notes](docs/RELEASE_NOTES_v1.0.1.md).

### Fixed

- **Idiomatic tests are recognized across all languages** (issue #8 generalized,
  precision; panels Y–DD). The root cause behind the Rust flood was universal:
  file-level test context never seeded the `test` role, so only the
  `test*`/`Test*` **name** convention did — flagging live tests (and the helpers
  they reach) dead in every language whose tests aren't name-convention. Now:
  **annotation/attribute** tests — Java `@Test`/`@BeforeEach`/… (JUnit/TestNG),
  C# `[Fact]`/`[Theory]`/`[Test]`/… (xUnit/NUnit/MSTest), PHP `#[Test]` (PHPUnit)
  — seed the `test` role; **call-based** suites with no named test function —
  JS/TS Jest/Mocha/Vitest, Ruby RSpec — root their `test()`/`it()`/`describe()`
  module-level call sites; and **test classes** are seeded transitively across
  nesting and inheritance (the JUnit abstract-base + thin-subclass idiom; pytest
  `class TestWidget:` / `unittest.TestCase`). `is_test_file` is now a single
  directory-aware heuristic shared by both extractors (a prior Python-vs-tree-sitter
  drift was itself a cardinal gap). A test helper/class reached by no test, and
  unused production code, still flag.
- **Rust inline unit tests no longer flood `find_stale`** (issue #8, precision).
  Idiomatic Rust tests live in `#[cfg(test)] mod tests { … }` with free-form
  names, so the `test*`/`Benchmark*`/`Example*` name convention never fired — the
  `#[test]` functions and every helper they reached were reported stale. The
  `#[test]` / `#[tokio::test]` (any `*::test`) attribute and the `#[cfg(test)]`
  module gate now seed the `test` role (a root). Matching is on the attribute
  **path**, not a raw `"test"` substring, so `#[cfg(feature="testing")]` and
  `#[doc="…test…"]` do **not** wrongly mark production code (which would *hide*
  dead code). A test helper reached by no test, and unused production code, still
  flag — consistent with a dead helper in any test file. Third-party runner macros
  (`#[rstest]`, `#[test_case]`) are a documented limitation (`LIMITATIONS.md`).
- **Grammar-load failures are surfaced, not swallowed** (issue #7). A tree-sitter
  grammar that can't load (offline/proxied environments, version drift) collapsed
  into a silent empty graph with a success exit. `treesitter.extract` now records
  the failure and emits a `RuntimeWarning` naming the affected languages and the
  number of skipped files; `extract_project` warns instead of a blanket swallow.
  Python extraction is unaffected either way; a normal run with grammars present
  emits no warning.
- **`impact_of` on an ambiguous name surfaces candidates and can be scoped**
  (issue #9). A bare common name (e.g. `get`) now lists the matching symbols in
  `alternatives` instead of a blank refusal, and the resolver accepts a fully
  qualified `Type.method` or a full `path::qual` id to scope to exactly one. The
  upgraded resolver also gives `get_callers` / `get_callees` / `trace_path` the
  same scoping; names that legitimately contain dots (`index.html`) still resolve
  directly.

### Changed

- **Dependencies bounded** (CONTRIBUTING lesson, prompted by issue #7):
  `tree-sitter>=0.22,<1` and `tree-sitter-language-pack>=0.1,<2`, so a future
  breaking major can't silently break extraction on a fresh install.

## [1.0.0] — 2026-06-23

**First stable release — precision, certified.** 1.0.0 is not a feature release;
it is the point at which the **release-readiness gate** is met: all hard gates
green (pytest / ruff / mypy / no-open-defects), **RRS 93.3 / 100**, and **two
consecutive full-diversity clean review panels**. The full trajectory is in
[`REVIEW_HISTORY.md`](REVIEW_HISTORY.md); the rubric in
[`RELEASE_READINESS.md`](RELEASE_READINESS.md). See the
[release notes](docs/RELEASE_NOTES_v1.0.0.md).

### Hardened

- **The cardinal invariant — live code is never flagged dead — is closed across
  every scope.** The dominant defect class through 22 review panels was a
  Python↔tree-sitter asymmetry where a live symbol used in some unmodeled way was
  reported stale. It is now resolved for by-name references, constructors (every
  language), type annotations, parameter default values, metaclass keywords, class
  bodies, public re-exports, and — closing the class — **defs nested in any host**:
  function bodies, class bodies, **control-flow blocks** (`if`/`for`/`while`/`try`/
  `with`/`match`), and **function-expression/arrow functions**. A shared
  `_scope_defs` traversal (Python) and full arrow-body recursion (tree-sitter) cover
  the complete, finite set of nesting hosts.
- **Metric integrity** — `_dedup_edges` collapses parallel and CALLS-subsumes-
  REFERENCES edges language-agnostically; `LIVENESS_RELATIONS` excludes
  query/read/write/ORM relations so cross-language edges don't inflate `fan_in` /
  PageRank; the GraphBLAS sweep and the pure-Python reference agree on thousands of
  random graphs (0 mismatches).
- **Envelope contract** — provenance gates the urgency ceiling on every operation;
  `find_stale` stays advisory (`needs_review`, name-based confidence); `ingest_trace`
  refuses when it grounds nothing; no operation returns `ok=True` with a vacuous
  result.
- **Regression suite** grew to **148 tests** (from 134), every review-panel finding
  pinned by a test confirmed to fail on the pre-fix code (non-vacuous).

### Notes

- No API changes from 0.4.0 — the 14 operations and three surfaces (library / CLI /
  MCP) are unchanged. Deferred, non-blocking polish tracked for a future release: SQL
  `MERGE` WRITES labelling; `find_holes` empty-list urgency. The **LSP backend** and
  **variable-granularity data flow** remain the two largest roadmap items
  ([`docs/STATUS.md`](docs/STATUS.md)).

[1.0.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v1.0.0

## [0.3.0] — 2026-06-23

**Depth & breadth.** Polyglot languages get first-class structure, the
cross-language web of frameworks widens, runtime fusion goes multi-language, and
the toolchain gets CI + packaging. See [`docs/STATUS.md`](docs/STATUS.md) for the
full table and the **Roadmap** of what's left.

### Added

- **Framework routes:** Django URLconf, Express (JS), and Spring (Java `@*Mapping`)
  join Flask/FastAPI — all produce `Route` nodes linked to handlers.
- **JS `fetch` → backend route:** client-side calls link to the matching route, so
  `trace_path` runs from a JS function through the backend to the DB table.
- **Events (EMITS/HANDLES):** `emit`/`publish` + `on`/`subscribe` create `Event`
  nodes, so `trace_path` crosses decoupled pub/sub boundaries.
- **Polyglot depth:** imports, inheritance (`INHERITS` from class heritage), and
  per-language test entry points for the tree-sitter languages; `impact_of` now
  flows through inheritance.
- **Framework-callback handling:** methods overriding an *external* base (e.g.
  `HTMLParser.handle_starttag`) are treated as roots — the last dead-code
  false-positive class is gone.
- **Multi-language runtime traces:** `ingest_trace` now reads coverage.py JSON,
  **LCOV** (JS/nyc, C/C++ gcov), and **Go coverprofiles**.
- **`summarize_subsystem`** operation; **`get_matrix`** small dense grid.
- **`watch`** command: re-index on file changes.
- **Pluggable embeddings:** `set_embedder()` for `find_similar` (token default;
  optional model2vec/sentence-transformers — no model bundled).
- **CI** (GitHub Actions) + **PyPI** publish workflow; SQLite schema migration for
  older index files.

### Notes

- The **LSP backend** (type-grade resolution) and **variable-granularity data
  flow** remain the two largest deferred items — see the Roadmap. `find_stale`
  stays advisory (`needs_review`); `--precise` (jedi) is the Python type-grade path.

### Tests

72 (up from 58): polyglot extraction, framework/event resolvers, multi-format
runtime traces, pluggable embedder, file-watching, schema migration, and the
precision/recall harness.

[0.3.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v0.3.0

## [0.2.0] — 2026-06-23

**stitchgraph goes polyglot.** The headline: a config-driven tree-sitter
extractor adds **11 more languages** alongside Python, all in one graph — so
dead-code, orientation, impact, and full-stack tracing now work across a
multi-language codebase.

### Added

- **Polyglot extraction (tree-sitter).** JavaScript, TypeScript/TSX, Rust, C,
  C++, C#, Go, Java, Ruby, PHP, and Bash — definitions (functions / methods /
  classes / structs / traits) and the call graph, in the same node/edge ontology
  as the Python `ast` extractor.
- **Config-driven languages.** Each language is a small `LangSpec` (node types →
  kinds, the call node + callee field). Adding a language is a spec, not new code.
- **Per-language resolution.** A JS call never binds to a Rust function of the
  same name; precision-biased (single match → confident, several → AMBIGUOUS to
  all, unknown → dropped as external).
- **Per-language entry-point roles.** `export` (JS/TS), `pub` (Rust), `public`
  (Java/PHP/C#), capitalised (Go), and `main` seed reachability.
- **Polyglot dispatcher.** `extract_project` merges Python + tree-sitter into one
  graph; a parse error in one language never breaks another.
- **[`docs/LANGUAGES.md`](docs/LANGUAGES.md)** — a language progress / support
  matrix (defs · calls · imports · inheritance · entry points per language).
- Packaging extras reorganised: `treesitter`, `precise` (jedi), `resolve`
  (sqlglot), `algebra` (graphblas), `all`.

### Notes / honest limits

- For the tree-sitter languages, **imports and inheritance aren't modelled yet**
  (calls still resolve cross-file by name, so dead-code/orient/trace work), and
  **entry-point detection is thin** (export/pub/public/capitalised/main) — so
  `find_stale` leans on `stitchgraph.toml` roots or honestly refuses without them.
- The hard part of any language is *type-correct* resolution (which `save` does
  `x.save()` mean?), not parsing — that's the deferred LSP upgrade.

### Tests

58 tests (up from 53), including a polyglot suite covering JS/Rust/Bash/Go/Java/
Ruby/PHP extraction, per-language call graphs, cross-language dead code, and the
no-cross-language-false-links guarantee.

[0.2.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v0.2.0

## [0.1.0] — 2026-06-23

First tagged release. A local-first, MCP-native code-intelligence graph for
Python that finds **stale code, implementation holes, orientation, and impact** —
ranked by what's actually live, every answer carrying a confidence and a reason
to double-check. One core library, three thin surfaces (library API, CLI, MCP),
plus a Markdown report. **Read-only on your code — it never edits source.**

### Highlights

- **One graph, three surfaces.** A single operation registry is the Python
  library API; the Typer CLI and the FastMCP server are generated from it, so
  they map 1:1 by construction. Optional deps are lazy-imported (core is
  stdlib-only).
- **The refuse-when-unsure envelope.** Every result carries
  `confidence / provenance / needs_review / urgency`. Confidence is load-bearing:
  it gates review and propagates through the path algebra; provenance caps the
  urgency ceiling so nothing low-confidence shouts red.
- **Dead code & its dual, holes.** `find_stale` (unreachable from entry points)
  and `find_holes` (references with no target) — advisory, never asserted.
- **Full-stack cross-language tracing** — the "gem": HTML form → route → handler
  → … → DB table/column in one `trace_path`, confidence propagated.
- **GraphBLAS algebra** for whole-graph sweeps (reachability, transitive fan-in,
  PageRank) with a pure-Python fallback that the two agree on by test.
- **Git-risk fusion, runtime-trace fusion, semantic retrieval, data loops** —
  see operations below.

### Operations (14)

`find_symbol`, `get_callers`, `get_callees`, `orient`, `find_stale`,
`find_holes`, `impact_of`, `trace_path`, `scan` (ranked issues + urgency),
`reindex` (`--precise` for jedi), `get_matrix` (bounded submatrix), `risk`
(git churn × centrality + hidden coupling), `ingest_trace` (coverage fusion),
`find_similar` (token retrieval). Plus `stitchgraph report` and `AGENTS.md`
agent rules.

### Languages & frameworks

- **Fully analysed:** Python 3.11+ (stdlib `ast`; optional `jedi`).
- **Detected at the cross-language boundary:** web routes (Flask/FastAPI/
  blueprints), HTML templates (`<form action>`), SQL (via sqlglot), ORM
  (SQLAlchemy/Django). See [`docs/LANGUAGES.md`](docs/LANGUAGES.md).

### Configuration

`stitchgraph.toml`: entry-point overrides (the trust escape hatch), index ignore
globs, review threshold, hub metric. See `stitchgraph.toml.example`.

### Quality

53 tests, including a precision/recall eval harness that asserts the
precision-over-recall stance (never flag live code as dead). Dogfoods on its own
~235-node source: clean scan, holes 0, and a short list of genuine dead-code
candidates — all `needs_review`.

### Known limitations (honest)

- `find_stale` is `needs_review` at 0.6 (0.78 with a runtime trace): resolution
  is name/scope-based, not type-grade. `--precise` (jedi) and a future LSP raise
  this.
- Framework callbacks overriding an *external* base class (e.g. an `HTMLParser`
  method) can look uncalled — the remaining stale false-positive class.
- Python-only first-class analysis for now; JS/TS/Java and a tree-sitter/LSP
  backend are the next step.

[0.1.0]: https://github.com/RexBytes/stitchgraph/releases/tag/v0.1.0
