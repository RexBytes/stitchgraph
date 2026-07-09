# Using stitchgraph (agent rules)

stitchgraph is a code-intelligence graph for this repository, available as MCP
tools, a CLI (`stitchgraph <op>`), and a Python library (`import stitchgraph`).
**Query the graph before grepping** — it already knows the structure, callers,
impact, and full-stack paths, with a confidence on every answer.

## First, index
Run `reindex <path>` once (and after large changes). Add `--precise` for
jedi-accurate resolution. Optionally `ingest_trace coverage.json` after running
tests to ground liveness in what actually executed.

## Situational rules

- **Landing on unfamiliar code?** Call `orient` first — it returns the entry
  points, the most-depended-on hubs to read first, the layers, and subsystems.
  Don't read files at random.
- **About to edit a function?** Call `impact_of <name>` for the blast radius and
  exactly which tests to run. Edit with that in mind. The radius is tiered:
  act on `confident` (reached through resolved edges, nearest-first), verify
  `ambiguous` (reached only through name-based guesses) before relying on it.
- **Need to understand how a request flows?** Call `trace_path <source> <sink>` —
  it returns the full-stack path (HTML form → route → handler → … → DB table)
  with a propagated confidence.
- **Looking for "the code that does X"?** Call `find_similar "<description>"`
  before grepping; fall back to `find_symbol <name>` for an exact name.
- **Who calls / what does this call?** `get_callers` / `get_callees`.
- **Tempted to delete "unused" code?** Call `find_stale` — but it is advisory.
  **Respect `needs_review`**: a result is "unreached by my analysis," not "proven
  dead." Verify (dynamic dispatch, plugins, framework callbacks) before deleting.
- **Hunting bugs / tech debt?** Call `scan` — ranked issues with urgency
  (🔴 fix now / 🟠 look closer / 🟢 cleanup): live stubs, holes, cycles, data
  loops, god objects. Triage by urgency, **then by `needs_review`**: a cycle or
  god-object whose coupling rests on name-ambiguous/heuristic edges is flagged
  `needs_review: true`, capped to 🟢, and reports its confident-only degree — it's
  likely a resolution artifact (common on languages without type resolution), so
  verify before acting. Cycles with ZERO confident edges (pure name collisions)
  are suppressed by default and counted in meta; `--show-heuristic` lists them.
- **Where's the risk?** Call `risk` — files that change often AND are depended on
  heavily, plus hidden coupling (files that co-change but share no code edge).
- **What matters most, across every lens?** Call `find_hotspots` — files that
  rank high on static centrality AND git churn AND runtime behaviour at once
  (cross-lens convergence). It fuses whatever lenses are available and refuses
  below two; convergence is what turns "probably important" into "provably
  central".
- **What's dangerous to touch structurally?** Call `find_chokepoints` — the
  articulation points (sole bridges whose removal fragments the graph), ranked by
  blast radius. Distinct from `risk`/`orient`: a chokepoint can have modest
  fan-in/out yet be the only link between two subsystems. Advisory.
- **Want the codebase's natural subsystems?** Call `find_subsystems` — spectral
  clustering of the call graph into auto-labelled clusters (the structural
  complement to `orient`/`summarize_subsystem`; it *discovers* the boundaries).
  Advisory; `[spectral]` extra (scipy) lets it scale past large graphs.
- **Want the raw structure of one module?** `get_matrix <scope> <relation>` —
  a small relation matrix for a single file/class (it refuses broad scopes).

## Reading results
Every result carries `confidence`, `provenance`, and `needs_review`. Treat
`needs_review: true` as "double-check this," and prefer high-confidence,
`extracted`-provenance answers. Issue results also carry an `urgency`.
`review_codes` is the machine-readable companion to `review_reasons` — filter
on stable codes (`NAME_BASED_EDGE`, `LSP_UNAVAILABLE`, `CYCLE_HEURISTIC`,
`COVERAGE_MISMATCH`, …; full table in docs/design.md §4) instead of
string-matching prose. `REFUSED` means no answer; `HEDGED_RESULT` means an
advisory partial answer you can still use — don't discard those.

Over MCP, long lists are bounded (default 100 items per list). **Always check
`meta.truncated`** before treating a list as complete — `impact_of` ranks
`tests_to_run` and its tiers nearest-first, so a truncated list keeps the most
relevant entries, but the full set is only in the counts / CLI `--json`.

## Don't
- Don't treat `find_stale` / `find_holes` as ground truth — they're suspicion,
  not proof, and stitchgraph never edits code itself.
- Don't read `find_holes` returning empty as "no broken wiring": it reports references
  orphaned by edits (delete/rename), not first-index calls to undefined/stdlib names. For
  reachable unimplemented stubs (the `is_stub ∧ reachable` landmine), use `scan` — they
  surface there as `live_stub`.
- Don't dump whole matrices into context; use the bounded `get_matrix` or the
  summaries from `orient`.
