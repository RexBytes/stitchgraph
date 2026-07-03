# External code review — 2026-07-03

A full-repo review (storage/indexing core, analysis operations + POD math, the
body-matrix language family, adapters/tests/packaging), run as four independent
review passes with empirical verification of the correctness findings. Recorded
here verbatim so each fix can be tracked; the **Status** column is updated as
fixes land on this branch.

Not re-reported: everything already covered by `LIMITATIONS.md` (checked).

Severity: **CRITICAL** (destructive / silently-wrong headline output),
**HIGH** (wrong answers or a broken shipped surface), **MEDIUM** (scale hazard,
operational trap), **LOW** (polish / doc / perf nice-to-have).

## Correctness & product-critical

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| F1 | CRITICAL | `reindex` on a path that is not a directory (typo, wrong cwd) still executes `DELETE FROM nodes/edges`, overwrites the stored root with `""`, and returns `ok`, confidence 1.0 — a one-keystroke wipe of an existing index with a success envelope. | `core/operations.py` (reindex) | **FIXED** — refuses (`ok=False`) without touching the store when the root is invalid |
| F2a | HIGH | The MCP server cannot be pointed at a DB: `main()` hardcodes `stitchgraph.db`, there is no argv/env handling and no console script, and MCP clients launch servers with an arbitrary cwd. | `adapters/mcp.py`, `pyproject.toml` | **FIXED** — `--db` argv + `STITCHGRAPH_DB` env + `stitchgraph-mcp` console script |
| F2b | HIGH | `Store(db)` *creates* an empty DB on open, so a mispointed MCP server (or CLI) answers every query from a vacuum at full confidence: `orient` → `ok: true, confidence 1.0, needs_review: false, top_hubs: []`. The agent concludes "tiny repo, nothing dead, all green" — the exact failure mode the envelope contract exists to prevent. | `adapters/mcp.py`, `adapters/cli.py` | **FIXED** — query ops refuse when the DB file doesn't exist or holds no indexed root; `reindex` may still create |
| F3 | HIGH | `intrinsic_dimensionality` truncates the spectrum to the top `kk` (≤16) singular values **before** the energy sum, so `k90` measures "modes to 90% of the top-16 energy", not of total variance — it silently saturates at 16 and understates dimensionality exactly on long-tailed suites. Verified numerically (40×60 random matrix: reported 14, true 24). `feature_map`'s per-mode energy fractions share the denominator. | `core/modes.py` | **FIXED** — total energy = full spectrum (dense) / ‖M‖²_F=nnz (sparse); per-mode fractions use the true total; sparse lower-bound flagged in meta |
| F4 | HIGH | `find_modes`/`feature_map`/`find_outlier_tests` never normalize test ids, but the shipped scaffold converter emits coverage.py phase-suffixed contexts (`test_a\|run`, `test_a\|setup`) verbatim. Verified: 6 logical tests → `meta["tests"] == 12`, 15 spurious "redundant pairs", and a `minimal_test_set` containing `…::test_0\|setup` — not a runnable test id. | `core/modes.py` vs `core/coverage_query.py` | **FIXED** — rows normalized/merged through the same phase/param stripping as coverage_query |
| F5a | HIGH | JS classic `for (let i = 0; …)` never binds its loop variable — the initializer is routed through `ev()` which has no `lexical_declaration` case (and the field order evaluates condition before initializer), so every use of `i` reads FREE. Verified: the identical hoisted-init form scores **0.52**; the same shape in Java scores 1.0. Breaks the documented temp-factoring invariance for the most common JS loop form. | `core/structure_js.py` | **FIXED** — for-init declarations bound, init evaluated before condition |
| F5b | HIGH | Bash multi-command `if cmd1; cmd2; then` silently drops `cmd2` (and everything it reads): `condition` is a repeated field, `child_by_field_name` reads only the first, and the child loop skips all condition children. Verified: a function with an extra guard command fingerprints **identical (1.0)** to one without — a silent false clone. The repeated-field hazard was fixed in Java (R197) and never propagated. | `core/structure_bash.py` (VFG + PDG) | **FIXED** — all condition-field children processed in both builders |
| F5c | MEDIUM | Python walrus (`ast.NamedExpr`) binding lost — the target has `ctx=Store` so the binding never enters `env`; `if (x := f()): return x + 1` vs the two-line equivalent scores **0.32** plus a spurious node. | `core/structure.py` | **FIXED** — NamedExpr binds its target and yields the value node |
| F5d | MEDIUM | PHP `foreach ($m as $k => $v)` binds neither variable: the key/value form parses as a `pair` node which `bind()` doesn't handle, so both loop variables read FREE in the body. | `core/structure_php.py` | **FIXED** — `bind()`/`bind_place()` handle `pair` (bind key and value) in both builders |
| F5e | MEDIUM | The Python PDG reads through lambda bodies (`header_names` uses `ast.walk`), creating data edges for lambda-captured names that no tree-sitter sibling would produce and Python's own VFG (lambda = opaque NESTED) doesn't — breaking the certified cross-frontend symmetry. | `core/structure.py` (pdg) | **FIXED** — header_names no longer descends into nested function/lambda bodies |
| F6 | HIGH | The tree-sitter extractor's reference resolver binds CALLS/REFERENCES to **MODULE** nodes (`by_lang` is built from all nodes; `_ref` has no module-kind exclusion), violating "the `_ref_edges` invariant" the Python extractor and the store both enforce. In Ruby/Go/JS repos `helper()` binds to `helper.rb`'s module node — inflating module fan_in, diluting real candidates' 1/n weights, and (because `_rewiden_resolved` rebuilds groups module-excluded) silently breaking incremental==full convergence for every tree-sitter language. | `core/extract/treesitter.py` | **FIXED** — MODULE nodes excluded from non-IMPORTS reference candidates (imports keep module resolution) |

## Scale & operational

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| F7 | MEDIUM | The "sparse SVD that scales" path builds the full dense matrix anyway (plus an unused mean-centred copy) before converting to CSR — memory is O(nT·nF) dense regardless of solver; a 100k×20k suite is ~16 GB before `svds` starts. Also the solver semantics flip at the cap boundary (dense = mean-centred, scipy = uncentred, mode 1 ≈ the mean) with only `meta["solver"]` as a hint. | `core/modes.py` | **FIXED** — CSR built directly from the coverage dict on the sparse path (no dense M/Mc); uncentred semantics surfaced via a needs_review reason |
| F8 | MEDIUM | `_auto_stream` counts files with a bare `rglob("*")` — no `SKIP_DIRS`, no ignore globs, no extension filter — so any repo with a populated `.venv` is forced onto the streaming path: ~40% slower and non-crash-atomic (streaming clears the index first and commits in batches; an interrupt leaves a partial index where the in-memory path keeps the old one). | `core/operations.py` | **FIXED** — probe counts only extractable files under non-skipped dirs |
| F9 | MEDIUM | Under streaming reindex, a swallowed tree-sitter failure leaves already-committed edge batches whose nodes are never inserted → resolved edges with phantom `dst_id`s flood `find_holes`/`scan` with findings that look like real broken references. | `core/extract/__init__.py` + streaming sink | **FIXED** — streaming reindex sweeps edges whose src/dst has no node after extraction |
| F10a | MEDIUM | No WAL / `busy_timeout` pragmas and `Store()` runs DDL on open, so the advertised watch + MCP-on-same-DB workflow hits `database is locked`. (`graph_diff` already tempdir-copies the DB to dodge mutate-on-open.) | `core/store.py` | **FIXED** — WAL + busy_timeout(10s) on file-backed stores |
| F10b | LOW | `watch`'s private skip list (8 entries) has drifted from `SKIP_DIRS` (18) and ignores config globs — tox/vendor churn triggers full reindexes that cannot affect the graph. | `core/watch.py` | **FIXED** — watch shares `SKIP_DIRS` |
| F10c | LOW | CLI exits 0 on operational failure (`_exit_code` returns 1 only for urgency-RED), so `stitchgraph scan --db broken.db && deploy` passes. | `adapters/cli.py` | **FIXED** — operational refusals (missing/unopenable db) exit 2; advisory refusals stay 0; RED stays 1 |
| F10d | LOW | The `dev` extra omits `mcp`, so the natural contributor install silently skips all MCP tests; the MCP build smoke test wraps everything in `except Exception: pytest.skip`, so a completely broken MCP surface passes CI green. | `pyproject.toml`, `tests/test_mcp.py` | **FIXED** — dev extra includes mcp; build test can fail; + an end-to-end FastMCP call_tool test |
| F10e | LOW | `render.py`/`report.py` truncate lists at 50/25 items with no marker — text-mode consumers can't tell output was cut. | `adapters/render.py`, `adapters/report.py` | **FIXED** — "… N more" markers in render + report |

## Performance / polish (nice-to-have)

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| F11a | LOW | Several ops re-materialize the full `Edge` list (`resolved_edges()` = fetchall + per-row dataclass) that `iter_resolved` exists to avoid (the documented 16M-edge OOM): `impact_of` (every query, just to grade provenance), `scan`, `find_coupling`, `risk`, `summarize_subsystem`, `dataloop`, `graphdiff`. | `core/operations.py` etc. | **FIXED** for `impact_of` (streamed provenance tally), `find_coupling`, `summarize_subsystem`, `dataloop`; `scan`/`risk`/`graphdiff` deferred — they consume multiple Edge fields into whole-graph maps, so the win needs a lean-tuple refactor of `_confident_share`. `iter_resolved` also hardened against BLOB-corrupt src/dst (previously only Edge-materializing consumers were protected). |
| F11b | LOW | Quadratic greedy loops: the minimal-cover greedy re-computes `rowset - covered` for every candidate every round, and `greedy_order` keeps scanning after gain hits 0 — a 50k-row suite effectively hangs. | `core/modes.py`, `core/coverage_query.py` | **FIXED** — exhausted rows pruned per round; greedy_order appends the zero-gain tail in one step |
| F11c | LOW | `algebra.pagerank` zeroes dangling-node mass instead of redistributing (scores aren't a distribution; ranking mostly unaffected); `transitive_fan_in`'s "repeated squaring" comment describes a linear-in-diameter loop. | `core/algebra.py` | DEFERRED — ranking order unaffected; changing scores would churn pinned oracles for no ranking gain. The misleading "repeated squaring" comment is fixed. |
| F11d | LOW | `_wl_features` truncates md5 to 8 hex chars (32 bits) for a corpus-wide feature namespace — birthday collisions expected somewhere at ~10^5+ labels, marginally inflating unrelated similarity. | `core/structure.py` | **FIXED** — 16 hex chars (64-bit) |
| F11e | LOW | `reach.articulation_points` raises the recursion limit around a DFS that is iterative (dead code); `_scc.tarjan_scc`'s docstring claims an iterative core but the implementation recurses. | `core/reach.py`, `core/_scc.py` | **FIXED** — dead raise removed; docstring corrected |
| F11f | LOW | `find_outlier_tests`' smoke-vs-unique heuristic keys on \|U[:,0]\| as "the always-on axis" — true only on the uncentred (scipy) path; on the default mean-centred path mode 1 is the largest *variance* axis. | `core/modes.py` | **FIXED** — smoke detection keyed to row breadth (solver-independent) |
| F11g | LOW | Solver-dependent tie-breaks: `coverage_query.greedy_order` picks the lexicographically last test on gain ties while the modes cover picks first-index; `mode_drift`'s docstring promises four categories but returns two. | `core/coverage_query.py` | **FIXED** — consistent lowest-id tie-break; docstring corrected |

## Design observations (recorded, not "fixed")

- **D1 — `Store.replace_file` has no production caller.** The five-pass incremental
  convergence machinery (~300 lines, oracle-guarded) is exercised only by tests;
  `watch` full-reindexes. It is also O(whole graph) per call (global `_rewiden_resolved`
  / `_propagate_overrides` / `_set_exported_roles` sweeps + `_dedup_resolved_edges`
  without the covering index the streaming path creates), so at the Magento scale the
  README cites it would be slower than the full reindex it exists to avoid. Keep
  (the oracles are valuable and the surface is library-public), but don't wire `watch`
  to it until the cost is made incremental.
- **D2 — nine hand-synchronized body-matrix frontends.** ~7,000 lines across the 9
  tree-sitter `structure_*.py` files; Java↔C# share 60% of lines in identical blocks,
  ~300-370 lines per file appear verbatim in ≥4 files, 111 lines are byte-identical
  across all nine, and `similar.py` repeats 9 near-identical per-language iterators.
  F5a/F5b are the proof this leaks: fixes landed in Java (R197, declaration-in-for)
  that the same code shape in JS/Bash never received. Recommended: extract the
  mechanical scaffolding (parser/walk plumbing, `_nc` helper family, `_op_text`, the
  PDG boilerplate, a registry for the `similar.py` iterators) into a shared frontend
  module — ~2,000-2,500 lines removable with zero semantic risk — so the next fix
  lands once, not nine times. Deliberately **not** attempted in this pass: it is a
  large behaviour-preserving refactor that deserves its own oracle-gated change.
- **D3 — `schema_version` is written but never read**, and `_migrate` is
  add-column-only; an older stitchgraph opening a newer index gets no version guard.
- **D4 — the envelope's urgency-vs-provenance ceiling is enforced only in
  `Result.__post_init__`**; `scan`/`find_holes`/`risk` assign `res.urgency` after
  construction, bypassing the guard (no current caller violates it).
- **D5 — one-directional oracles.** The PDG-vs-VFG differentials `pytest.skip` when
  the VFG doesn't thread a read, so a VFG regression converts failures into silent
  skips; no skip-count monitoring in CI.
