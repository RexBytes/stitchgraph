# v3.51.0 — answers to the field review

*2026-07-09 · eight requests from the first external agent field review,
shipped; then the fixes were reviewed as hard as the features — two
adversarial self-review rounds (panels R287–R288), a dogfood pass, and an
independent agent review · details: `CHANGELOG.md` · ledger:
`REVIEW_HISTORY.md`*

## Where this release came from

An external agent (Claude Opus 4.8, running stitchgraph 3.50.0 against a
~40k-line post-quantum Rust crate) filed a 15-request review. Its verdict:
the confidence-and-provenance model and the behavioural toolkit were the
best parts; the gap was "works when perfectly configured" vs "works well by
default". This release closes the eight requests that survived triage.

## What's new

**`find_hotspots` — cross-lens convergence as a first-class command.** The
review's single most valuable insight — files that rank high on static
centrality AND git churn AND runtime behaviour *at once* — previously had to
be assembled by hand from four command outputs. Percentile fusion (ties
share their average rank), a file must converge on ≥ 2 independent lenses to
be listed, and test files are excluded from every lens: the suite is hot on
every lens by construction, and that convergence is an artifact, not
centrality.

**`review_codes` — the envelope is now machine-filterable.** Every result
carries stable codes (`NAME_BASED_EDGE`, `LSP_UNAVAILABLE`,
`CYCLE_HEURISTIC`, `COVERAGE_MISMATCH`, …) alongside the prose
`review_reasons`, with `needs_review ⇒ review_codes non-empty` enforced at
construction, on assignment, and self-healed at serialization. `REFUSED`
(no usable answer) is now distinct from `HEDGED_RESULT` (a non-empty
advisory payload you should keep). Full table in `docs/design.md` §4.

**`impact_of` is tiered, ranked, and capped.** The blast radius splits into
`confident` (reachable through resolved edges alone — act on it) and
`ambiguous` (every route crosses a name-based guess — verify first), each
ranked nearest-first by hop distance; `tests_to_run` is nearest-first too,
so any truncation keeps the most relevant tests. The field case — "73% of
the crate at confidence 0.47" — becomes an actionable split (dogfooding on
stitchgraph itself: 2,536 dependents → 227 confident / 2,309 ambiguous).
Memory is bounded by node AND induced-edge caps, and any degradation
reports itself in `meta.distances_skipped` — never silently.

**Language-server failures are actionable.** The opaque "server unavailable"
now says *why* and *how to fix it*: not-on-PATH declines carry a per-server
install hint, and rustup's uninstalled rust-analyzer proxy shim — the exact
field failure, which cost the reviewer an entire low-confidence first pass —
is detected and answers with the literal `rustup component add
rust-analyzer` command lifted from the shim's own error. Under AUTO, a
present-but-broken binary is flagged loudly; machines with no servers stay
silent as before.

**`scan` stops manufacturing fake cycles.** A "dependency cycle" whose every
edge is a bare method-name collision (0/N confident — `new`/`default`/
`build` matching across unrelated types) is suppressed by default, counted
in `meta.heuristic_cycles_suppressed`, and restorable with
`--show-heuristic`. Test-owned stubs (a fake server's deliberately-empty
`run()`) are GREEN advisories, never the repo's loudest RED.

**Bounded, honest MCP output.** Every list in an MCP tool result is cut at
100 items (`STITCHGRAPH_MCP_MAX_ITEMS` overrides; 0 disables), recursively —
nested lists can't smuggle the 400 KB blob — with every cut reported in
`meta.truncated`. Matrix payloads get a correlated cut (labels and cells
trimmed together, index alignment preserved). The CLI `--json` still
carries full payloads.

**Coverage artifacts survive path-prefix drift — everywhere.** An artifact
captured from a different root (`sandbox/…`, a crate subdir) used to make
`audit_graph` refuse while `select_tests`/`co_change`/`find_gaps` emitted
confident *wrong* diagnoses of the same drift. Reconciliation now happens
once, at the shared load boundary, with a strict safety rule (unique
basename+symbol key AND whole-segment path alignment — a stale or vendored
id is never grafted onto an unrelated same-named file), and every op
annotates `meta.ids_remapped`. Mode labels are IDF-weighted (no more
keyword salad from suite-wide boilerplate tokens).

## The process is the story

The feature batch was reviewed with an 8-angle adversarial process, twice —
and round two existed because round one's fixes deserved the same scrutiny
as the features. It was right to: two of the first round's fixes had
introduced regressions (an MCP exemption that reopened the unbounded-output
hole; an envelope hook that traded away the serialization safety net). Both
rounds' findings — 21 total, plus 2 refuted claims — are fixed and pinned,
recorded as panels R287–R288. Dogfooding the branch on stitchgraph itself
found one more bug every review angle missed, and an independent agent
review contributed three pre-existing hardening items (quoted config
booleans now fall back to defaults instead of silently enabling features;
the lean-install and adapter-parity contracts gained standing regression
tests).

## Compatibility

No index schema change, no new dependency, no migration. Additions are
backward-compatible: the envelope gains `review_codes`; `impact_of` gains
`confident`/`ambiguous`/`limit` while `blast_radius` stays the full flat
list. Three behaviour changes to know about: `scan` hides 0/N-confident
cycles by default (pass `--show-heuristic` for the old view), MCP tool
results are bounded by default (raise `STITCHGRAPH_MCP_MAX_ITEMS` if you
relied on unbounded payloads), and a malformed quoted boolean in
`stitchgraph.toml` (e.g. `include_tests = "false"`) now falls back to the
default instead of silently reading as true — unquote it.
