# stitchgraph v1.0.5 — CLI/UX papercuts (issues #18, #19)

A small polish release: three CLI/usability fixes surfaced while running v1.0.4 against a
real repo (`ant-node`). No change to the analysis engine — the graph, provenance, and every
operation's results are unchanged.

## What changed

### `risk` is scoped from the indexed root, like every other read op (#18)

Every read operation takes its scope from the indexed graph (`--db`) — except `risk`, whose
git-history `--path` defaulted to the process **cwd**. So the natural `risk --db <db>` from
anywhere but the analysed repo failed with `'.' is not a git repository`, breaking the
otherwise-consistent "index once, query from anywhere" workflow. The information was already
on hand: the DB records the indexed root (`risk` itself reads it for path-mapping). `risk`
now defaults `--path` to that recorded root; `--path` remains an explicit override. `risk
--db <db>` now works from any directory.

### `stitchgraph --version` (#19)

There was no way to confirm the installed version from the CLI — which matters more here than
for most tools because the install model is **version-keyed** (the offline-bundled grammar
line vs. the runtime-download line are pinned by version, see #12). `stitchgraph --version`
now prints the package version *and* the active `tree-sitter-language-pack` line
(bundled/download model) — exactly what belongs in a bug report. (It reports the *installed*
distribution version via `importlib.metadata`, so it reflects what's actually on the system.)

### `docs/design.md` §9 reconciled with the CLI (#19)

The "Operation surface" table advertised an optional `path?` argument on
`orient`/`find_stale`/`find_holes`/`scan`/`structure_smells` that the CLI never accepted —
mildly confusing on first use. Scope comes from the **indexed graph** (`--db`), not a
per-call path filter, so the `path?` is removed and a new "On scope" note documents the real
model: index the subset you want to analyse and query that DB from any cwd. The one
exception, `risk`, is called out — its `--path` is the *git repo root* for history (now
defaulting to the indexed root, #18), not a query filter.

## Verification

`pytest` 179 passed (2 new regression tests: `risk` defaulting to the indexed root from a
foreign cwd; `--version` output + exit code) · ruff clean · mypy clean against **both** the
dev pack (1.10.6) and the pinned bundled 0.13.0 (the version CI installs). Confirmed by full
three-model panels (opus + sonnet + haiku). Full trajectory in `REVIEW_HISTORY.md`.
