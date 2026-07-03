# Panel prompt template (stitchgraph)

The brief sent to each model slot in a review panel. Send the **same** text to
every model (vary only the model itself), filling the `<…>` placeholders. Run the
panel as: one slot per available model (e.g. opus, sonnet, haiku), each optionally
spawning **same-model** sub-agents. Then adjudicate (see `CONTRIBUTING.md`):
consensus → fix; singleton → reproduce it yourself before fixing; mock-only /
documented → dismiss with a reason.

See `RELEASE_READINESS.md` for the release gate and `REVIEW_HISTORY.md` for the
panel trajectory and the running already-fixed list.

---

HEAD-TO-HEAD adversarial review (read-only; **MAKE NO CHANGES**) of **stitchgraph**
at `<repo path>`, panel `<letter>`. **"No new defects" after genuine effort is the
expected, valued result — do NOT manufacture findings; mock-only / shallow /
duplicate findings count against you.**

stitchgraph is a local-first, MCP-native code-intelligence graph (CLI + importable
library + MCP server) that finds stale/dead code, holes, orientation, and impact
across 12 languages (Python via a stdlib `ast` extractor plus 11 via tree-sitter), with a SQLite
adjacency store as source of truth, and a result envelope
(ok/confidence/provenance(EXTRACTED|INFERRED|AMBIGUOUS)/needs_review/urgency).

=== CARDINAL INVARIANT ===
**Precision over recall — LIVE code must NEVER be flagged dead.** A false "dead" is
the only release-blocking class of bug. Secondary: metric inflation (fan_in /
pagerank double-counting) and envelope-contract violations (provenance gates the
urgency ceiling).

=== REVIEW RULES (strict) ===
- Review-only on the MAIN working tree at `<repo path>`. Do NOT edit files, do NOT
  run `pip install` / `pip install -e .` (it has contaminated the editable install
  before). Write any repro scripts ONLY under `/tmp`.
- The package is installed editable; `import stitchgraph` or run with
  `PYTHONPATH=src`. **Verify your environment imports the tree under test** before
  trusting a repro. `git -C <repo path> rev-parse HEAD` must equal `<HEAD sha>`.
- A finding MUST reproduce with a REAL input (no monkeypatching/mocking internal
  methods — those paths are intentionally undefended). No executed real-input repro
  → not a finding. You MAY spawn helper sub-agents but they MUST run on the SAME
  model as you. Baseline: `<X passed, Y skipped>`; ruff + mypy clean.

=== TESTING / REVIEW PHILOSOPHY (apply rigorously) ===
Hunt inputs that FALSIFY a promise, not confirm the happy path.
1. Every docstring sentence, type, parameter, named behaviour, and threshold is a
   promise — list them and break each. If docs claim X, find the input where the
   code does not-X.
2. Boolean functions: all four corners (true, false, false-for-a-different-reason,
   true-under-adversarial-input).
3. Every parameter: empty, boundary, and the messy real-world input it exists for.
4. Pin thresholds at N (passes) and N+1 (fails).
5. Fallbacks preserve intent, not just type ("missing" vs "empty").
6. Exact stdlib exception types — bugs hide in which exception is raised vs caught.
7. Round-trip thinking for transform/serialise paths (build → run → compare).
8. A test that reveals a real source bug IS the finding.

=== WHERE TO LOOK (the standing theme for this project) ===
The dominant defect class across panels I–Q has been **"a live symbol used in a way
that isn't modeled as a reachable edge → flagged dead,"** a recurring
**Python↔tree-sitter asymmetry** (Python historically walked function/method bodies
but missed signature and class-definition and nested scopes; tree-sitter walks the
whole def/class node). Panels J–Q closed: by-name value references; constructors in
every language (`new`, `.new`, `__construct`, class-named ctors); type annotations;
constructor reachability (class→`__init__`); PHP public-class export; parameter
default values; metaclass keywords; class-body references; **function-local
classes/closures**; and public re-exports from `__init__`. Hunt the NEXT instance of
this class in either extractor, plus: metric inflation, envelope-contract
violations, the cross-language resolvers (SQL/HTML/Express/jsfetch/Spring), and the
**core-only / persistence / migration** surface (config-from-root, on-disk index
build/reopen/migrate, `ingest_trace` grounding honesty) that the all-extras
reviewers are blind to. `<plus any specific residual to probe this round>`

=== ALREADY-FIXED (do NOT re-report — see REVIEW_HISTORY.md for the full list) ===
`<paste / point to the running already-fixed list so reviewers hunt only what's left>`

READ `<repo path>/LIMITATIONS.md` FIRST; do NOT re-report documented tradeoffs
(notably: module-level uses are not attributed; advisory dead-code). You may argue
one is wrong, with reasoning.

=== DELIVERABLE ===
Numbered, severity-ranked (CRITICAL/HIGH/MEDIUM/LOW/NIT). Each: title; severity;
exact `file.py:line`; concrete real-input failure (input + what breaks); fix
direction. Mark CONFIRMED + the executed repro. NEW real defects only. If you find
nothing release-blocking after thorough probing, reply `FINDINGS: none` and list the
scenarios/commands you ran.
