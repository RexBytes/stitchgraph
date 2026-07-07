# 25 — Dogfood: v3.46.0 on stitchgraph itself

*2026-07-07 · the released PyPI wheel (not the working tree) pointed at its
own repository · every headline finding hand-verified before being written
down. Previous self-audit: v3.27.0 (task #18); since then five minor
releases and the whole compression/LSP arc happened, and the v3.45.0
data-flow slices had never seen this codebase.*

## Setup

Fresh venv, `pip install stitchgraph==3.46.0`, `reindex . --precise`
(jedi — Python is jedi's domain; the `--lsp` defaults deliberately cover
only the tree-sitter languages). 187 files, 2,878 nodes, 2 holes, 3.5 min.
Static battery: orient / scan / find_stale / find_holes / find_chokepoints
/ risk. (POD/behavioural was covered by the v3.27.0 dogfood and that
machinery hasn't changed since; not re-run.)

## Genuine catches (fixed in this commit)

1. **Dead code in shipped src/**: `treesitter.supported_languages()` —
   added with the original polyglot extractor, zero callers anywhere in
   src/tests/docs today (`doctor` grew `grammar_backend()` instead and
   nothing else ever called it). `find_stale` flagged it; removed.
2. **`_with_edges(class_qual=…)`** (extract/python.py): the parameter is
   never loaded in the body — the with-statement protocol edges never
   needed the class context, only the uniform helper call shape passed it.
   Caught by the v3.45.0 unused-parameter advisory; parameter dropped at
   both call sites.
3. **`_scan_calls(ids=…, parent=…)`** (resolve/jsfetch.py): same story —
   two parameters threaded through since the resolver's first version,
   used by neither the body nor its recursion. Dropped.

## True positives that are design, not defects

- **22 instance-attribute data loops in src/** — the v3.45.0 detector's
  first look at this codebase, and every sampled one is a correct
  feedback-through-state description of an intentional structure:
  `LspClient._pending` (the reader thread writes, `_wait` reads+pops — in
  code that shipped *yesterday*), `_StoreEdgeSink._cur_src` (a 16-member
  loop through the streaming sink's buffer), `Result.review_reasons`.
  Advisory ORANGE is the designed behaviour; nothing to fix.
- **2 holes, both `_HAVE_SQLGLOT`**: `from .resolve.sql import
  _HAVE_SQLGLOT` — sql.py binds that name at module level inside
  `try/except ImportError`, and module-constant extraction doesn't emit
  nodes for try-guarded assignments, so the import dangles. Honest from
  the graph's view, benign in code. Noted as a small extractor recall gap
  (below).
- **`tests/fake_lsp_server.py::send` stale**: the fake server runs only
  via `subprocess` — statically invisible execution, exactly the class of
  advisory `needs_review` exists for. The other 12 stale candidates are
  research-corpus fixtures (deliberately dead test material).

## Calibration findings (the actionable residue)

1. **`god_object` floods at this repo's scale**: 340 findings (252 in
   src/) at ORANGE, gated by static floors (`fan-in 6, fan-out 15`) that a
   2,878-node codebase strolls past — 12% of the graph flagged is noise,
   not signal. The v3.31.0 work capped the *output list* at huge scale but
   the *thresholds* are absolute. Follow-up: size-scaled or
   percentile-based gates (e.g. top decile of coupling with an absolute
   floor).
2. **`orient` hubs are test-polluted on a mixed index**: #1 hub is
   `Store.close` (transitive fan-in 1,097 — every one of 1,117 Test nodes
   closes a store) and #2 is a pytest *fixture*. Correct arithmetic,
   useless orientation. `find_component` already excludes tests from its
   ranking; `orient`'s hub list should offer the same discount.
3. **Unused-parameter advisories need two family suppressions**: 48 of 52
   findings are parameters dictated by an interface the checker can't see —
   (a) `@operation`-registered functions whose `(store, …)` shape is the
   registry contract (runtime-only ops genuinely ignore `store`), incl.
   the Typer callback whose params are consumed by introspection; (b) the
   per-language `structure_*.py` families where `_walk(lang)` /
   `_build_pdg(data)` keep uniform signatures across ten grammars and only
   some use every slot. Follow-up rules: skip decorator-registered
   functions (beyond the current abstract/overload set), and skip a param
   that same-named same-arity siblings elsewhere in the project DO use.
4. **Module constants bound under `try/except` don't become nodes** (the
   `_HAVE_SQLGLOT` holes). Small recall gap in `module_consts`; two-line
   fix candidate, low urgency (it costs two phantom holes on this repo).

## The rest of the battery, briefly

- **risk**: top hotspots are exactly the marathon's heavy files —
  extract/python.py (churn 60 × centrality 9,826), operations.py,
  store.py — all ORANGE, all true.
- **find_chokepoints**: 20, all small blast radii (≤10); the top src/
  entries (`_NameView.__iter__`, `globs.ignored`) are real narrow waists.
- **cycles**: 16, the known recursion/mutual-import set; no new ones from
  the marathon's code.
- **live_stub**: 1, a deliberate test double (`_S.run` in test_mcp).

## Addendum (v3.47.0): the calibrations, applied and re-measured

All four follow-ups shipped, plus one suppression the re-run taught us:

1. **God-object floors are now size-scaled**: with ≥200 coupled code nodes,
   a god object must sit strictly ABOVE the population's 95th percentile in
   both directions (p95 + 1 — the first formula used ≥ p95 and the test
   caught it flagging the crowd when >5% share one value); small graphs keep
   the historical 5/5 floors byte-identically.
2. **Orient excludes test mass**: test-owned nodes (Test kind, `test` role,
   test-path file) neither appear in the hub list nor count as dependency
   mass in the transitive metrics — excluded as closure rows / sample
   sources while still ROUTING reachability. The explicitly-chosen raw
   `fan_in`/`pagerank` metrics keep degree semantics (list-filtered only).
3. **Unused-param advisories gained three suppressions**: framework-owned
   signatures (any decorator beyond the static/class/property/abstract set),
   family-interface params (a slot a same-name same-arity sibling DOES
   load), and **value-referenced functions** (an incoming REFERENCES edge =
   passed to a dispatcher, its shape is the caller's) — the graph already
   knew the ten grammar builders are called through one shared traversal.
4. **Module constants inside try/except and if/else** are collected; the
   `_HAVE_SQLGLOT` phantom holes are gone.

And the advisory that survived all suppressions was RIGHT: `data`/`text`
in the ten `structure_*.py` builder families is dead **family-wide** (the
tree-sitter twins never needed the source bytes their `ast` sibling's call
shape once implied) — 28 parameters underscore-renamed to document it.

Re-run on the same repository:

| | v3.46.0 | v3.47.0 |
|---|---|---|
| scan findings | 435 | **45** (76 before the param renames) |
| god_object | 340 (252 src, ORANGE) | **0** (max real coupling ~50 vs floors 62/29 — honest) |
| unused_params | 52 | **5** (all genuine: modes/similar `store`, two test/research helpers) |
| holes | 2 (phantom) | **0** |
| #1 hub | `Store.close` (fan-in 1,097, test mass) | `Provenance` (320, all src) |
| test defs in hub list | yes (a pytest fixture at #2) | none |

## Verdict

The tool finds real things in its own code the morning after release: one
dead function, three dead parameters, and correct state-loop descriptions
of code written a day earlier — while the noise it produces on itself
points at four concrete, scoped calibration improvements rather than at
anything broken. Cost of the full precise self-index: 3.5 minutes.
