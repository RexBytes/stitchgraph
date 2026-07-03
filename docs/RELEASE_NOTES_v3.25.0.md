# stitchgraph v3.25.0 — release notes

**The external-review hardening release.** A full-repo external code review (2026-07-03; findings
recorded verbatim in [`docs/REVIEW_FINDINGS_2026-07-03.md`](REVIEW_FINDINGS_2026-07-03.md) with
per-finding status) ran four independent passes over the storage/indexing core, the analysis
operations + POD math, the body-matrix language family, and the adapters/tests/packaging — with
empirical verification of every correctness finding. This release fixes everything actionable:
1 CRITICAL, 6 HIGH, 8 MEDIUM, 9 LOW. No schema change; existing index DBs keep working.

## Critical / high — correctness

- **`reindex` no longer destroys an existing index on an invalid root (F1).** A typo'd or missing
  path executed `DELETE FROM nodes/edges` and returned `ok`/1.0. With content present, `reindex` now
  refuses and leaves the store untouched; an empty store keeps the historical degrade-to-empty
  contract (panels R17A/YYY/ZZZ).
- **The MCP server is launchable and honest about a missing index (F2).** `stitchgraph-mcp` console
  script with `--db` / `STITCHGRAPH_DB` (MCP clients launch servers from an arbitrary cwd — the
  hardcoded relative default resolved to the wrong directory). And since `Store(path)` creates an
  empty DB on open, a mispointed CLI/MCP/report adapter used to answer every query from a vacuum at
  full confidence (`orient` → ok/1.0, zero nodes, all green). All three adapters now **refuse** when
  the DB file doesn't exist or holds no indexed root; only `reindex` may create one.
- **`find_modes` intrinsic dimensionality is now computed against the full spectrum (F3).** The
  energy denominator was truncated to the top-16 singular values, so `intrinsic_dimensionality`
  measured "modes to 90% of the top-16 energy" and silently saturated at 16 — understating exactly
  on long-tailed suites (verified: a matrix with true k90 = 25 reported 14). The dense path is now
  exact (and independent of `k`); the sparse path uses the true total `‖M‖²_F = nnz` and, when the
  computed modes fall short of 90%, reports a **flagged lower bound**
  (`meta.intrinsic_dimensionality_is_lower_bound` + a `needs_review` reason). `feature_map`'s
  per-mode `energy` fractions use the same true denominator.
- **The POD ops normalize test ids (F4).** The turnkey converter emits coverage.py context keys
  verbatim (`test_a|run`, `test_a|setup`), and `find_modes`/`feature_map`/`find_outlier_tests` used
  raw keys — doubling the test count, manufacturing "redundant pairs" out of identical setup rows,
  and putting non-runnable ids like `…::test_0|setup` in `minimal_test_set`. All POD ops now
  normalize through the same phase/param stripping as the set-math ops (`base_test_id`/`normalize`
  moved into `modes.py`; `coverage_query` re-exports them unchanged).
- **Five body-matrix per-language drift bugs (F5, each empirically reproduced, each pinned in
  `tests/test_review_body_matrix.py`):**
  - **JS:** `for (let i = 0; …)` never bound its loop variable (and evaluated the condition before
    the initializer) — the identical hoisted-init form scored **0.52**; now > 0.95. Java already
    handled the same shape correctly: this is the cost of nine hand-synchronized frontends.
  - **Bash:** `if cmd1; cmd2; then` silently dropped every guard after the first (`condition` is a
    repeated field; `child_by_field_name` reads only the first) in **both** the VFG and the PDG —
    a function with an extra guard fingerprinted **identical (1.0)**. The R197 repeated-field class.
  - **Python:** the walrus (`ast.NamedExpr`) binding was lost — `if (x := f()):` scored **0.32**
    against its two-line equivalent; now exactly 1.0.
  - **PHP:** `foreach ($m as $k => $v)` bound neither variable (the `pair` node was unhandled) —
    closed symmetrically in the VFG `bind()` and the PDG `bind_place()`.
  - **Python PDG:** `header_names` walked through `Lambda` bodies, emitting data edges for captured
    names that no tree-sitter sibling produces and Python's own VFG (lambda = opaque NESTED) doesn't.
- **tree-sitter references no longer bind to MODULE nodes (F6).** `by_lang` included MODULE nodes,
  so `helper()` in a Ruby/JS/Go repo bound to `helper.rb`'s module node — violating the
  `_ref_edges` invariant the Python extractor and the store both enforce (panels R13B/R31A),
  inflating module fan_in, diluting real candidates' 1/n weights, and breaking incremental == full
  convergence for every tree-sitter language. Modules now live in a separate bucket offered only to
  IMPORTS resolution. Re-validated against the full incremental and streaming differential oracles.

## Medium — scale & operational

- **The sparse SVD path is actually sparse (F7):** the CSR is built directly from the coverage rows
  (the old path materialized the full dense matrix *plus* an unused mean-centred copy first — ~16 GB
  on a 100k×20k suite before `svds` started). The uncentred-solver semantics (mode 1 ≈ the mean
  profile) are now surfaced as a `needs_review` reason instead of hiding in `meta["solver"]`.
- **AUTO-streaming probes only what extraction reads (F8):** the old bare `rglob("*")` counted
  everything, so a populated `.venv` forced a 50-file project onto the ~40% slower,
  non-crash-atomic streaming path. The probe now prunes the shared `SKIP_DIRS` and counts only
  extractable extensions.
- **Streaming orphan sweep (F9):** a swallowed tree-sitter failure mid-stream left committed edge
  batches whose defining nodes were never inserted — phantom `find_holes`/`scan` findings
  indistinguishable from real broken references. The streaming path now sweeps edges whose src/dst
  has no node (a no-op on clean runs, preserving the byte-identical streaming oracle).
- **Concurrency pragmas (F10a):** file-backed stores open in WAL with a 10s `busy_timeout`, so the
  advertised `watch` + MCP-server-on-the-same-DB workflow no longer hits `database is locked`
  during multi-second reindex commits.
- **`watch` shares the extractors' `SKIP_DIRS` (F10b)** — its private 8-entry copy had drifted
  (no `.tox`/`.mypy_cache`/`vendor`/…), so tox or `composer install` churn triggered full reindexes
  of files the indexer would never read.

## Low — polish

- **CLI exit codes (F10c):** an *operational* failure (missing/unopenable `--db`) exits **2**, so
  `stitchgraph scan --db broken.db && deploy` can no longer deploy. RED findings keep exit 1; an
  op-level advisory refusal (e.g. `get_matrix`'s too-broad-scope) deliberately keeps exit 0 — it IS
  a clean answer.
- **Packaging/tests (F10d):** the `dev` extra includes `mcp` (the natural contributor install
  silently skipped every MCP test); the MCP build test can actually fail (the old blanket
  `except Exception: pytest.skip` passed a fully broken surface); one real end-to-end FastMCP
  `call_tool` round-trip now pins schema generation, kwargs dispatch, and the envelope JSON.
- **Truncation markers (F10e):** `render`/`report` say "… N more" instead of silently cutting lists.
- **Perf (F11):** `impact_of` streams its provenance tally (it fetchall+Edge-materialized the whole
  table on every query — the documented 16M-edge OOM class); `find_coupling` /
  `summarize_subsystem` / `dataloop` switch to lean tuples; `iter_resolved` now also skips
  BLOB-corrupt src/dst rows (previously only Edge-materializing consumers were protected); the
  greedy minimal-cover loops prune exhausted rows (the old O(n²·row) rescan effectively hung on
  50k-row suites of duplicate profiles); WL fingerprint features widen to 64-bit hashes (32-bit
  birthday collisions were expected at ~10⁵ corpus-wide labels); dead recursion-limit code removed;
  stale docstrings corrected.

## ⚠️ Behavioural changes to note (no schema change)

1. **`intrinsic_dimensionality` values change** — and can now legitimately exceed the number of
   reported modes (that IS the long-tail information the old clamp hid). Panel R272's zero-variance
   guard is preserved; panel R273's `k90 ≤ modes` clamp is removed *by design* — it enforced the
   saturation bug. The pinned tests now assert k-independence instead. Recorded values from earlier
   versions that read 15–16 were likely saturated. On the sparse path check
   `meta.intrinsic_dimensionality_is_lower_bound`.
2. **`find_outlier_tests` payload:** smoke-vs-unique is now keyed to **row breadth** (fraction of
   executed functions the test touches — solver-independent) rather than `|U[:,0]|` (only "the
   always-on axis" on the uncentred sparse path). The per-row field `mode1_load` is replaced by
   `breadth`.
3. **POD meta counts are normalized** — `meta["tests"]` counts logical tests, not phase/param rows.
4. **CLI exit code 2** on operational failures (was 0).
5. **Module fan_in drops on tree-sitter languages** (F6) — `scan` god-object/fan-in numbers around
   same-named modules will change; the new numbers are the ones a full Python-extractor-style
   resolution always produced.
6. **`test_order` / `greedy_order` tie-breaks** now pick the lowest test id (matching `find_modes`'
   minimal cover); orderings can differ from v3.24.0 on gain ties.

## Deferred (recorded, deliberate)

- `Store.replace_file`'s five-pass convergence machinery has no production caller and is O(whole
  graph) per call (D1) — don't wire `watch` to it until the cost is incremental.
- The nine body-matrix frontends share ~40–50% mechanically identical scaffolding; F5a/F5b prove
  the hand-sync leaks. A shared-frontend extraction (~2,000–2,500 lines, zero semantic risk) is the
  recommended next refactor (D2).
- `scan`/`risk`/`graphdiff` lean-tuple conversion; pagerank dangling-mass redistribution (ranking
  order unaffected); `schema_version` written-never-read (D3); post-construction `urgency`
  assignments bypass the envelope ceiling guard (D4, no current violator); one-directional
  PDG-vs-VFG oracle skips (D5).

## Verification

Full suite green including the heavyweight gates: the incremental differential
(`replace_file == reindex`, 13m23s), the streaming differential (byte-identical), all 12-language
completeness batteries and PDG⇄VFG differentials, ruff, and mypy. New regression pins:
`tests/test_review_body_matrix.py` (F5/F6), plus per-fix tests in `test_regressions.py`,
`test_modes.py`, `test_mcp.py`, `test_streaming_reindex.py`, `test_core.py`.
