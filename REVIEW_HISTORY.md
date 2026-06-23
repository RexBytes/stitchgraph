# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 15 (Panels A–O) |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ · no-open-defects ✅ |
| Tests | 127 passing, 1 skipped |
| Coverage | ~84% |
| Release-Readiness Score | 81.3 / 100 |
| Convergence | yield bottoming out: N (10, default values) → O (4, metaclass), both single narrow corners opus+haiku converged on; third-party core-only clean 2 straight. Streak 0 of 2 |
| Verdict | NOT RELEASABLE — sonnet now supplied by a third party (API down for agents); needs ≥2 consecutive full-diversity clean panels |

## Trajectory

Severity weights: CRITICAL=40, HIGH=10, MEDIUM=4, LOW=1, NIT=0.2.

| Panel | Models | Findings | Weighted | Theme |
|---|---|---|---|---|
| A | opus · sonnet · haiku | 3 HIGH · 3 MEDIUM · 1 LOW | 43.0 | symmetry gaps — a rule present in one sibling, missing in another |
| B | opus · sonnet · haiku | 1 HIGH · 1 MEDIUM · 1 LOW | 15.0 | the *same* gaps in the other siblings (tree-sitter, html/jsfetch, git risk) |
| C | opus · sonnet · haiku | 1 HIGH · 2 MEDIUM | 18.0 | deeper surfaces — tree-sitter callback roles, signal `.connect()`, SQL CTE phantom |
| D | opus · sonnet · haiku | 1 MEDIUM · 2 LOW | 6.0 | incremental/edge surfaces — `_resolve_worklist` ambiguity, recursion self-edge, malformed-coverage crash |
| E | opus · sonnet · haiku | 1 HIGH · 2 LOW · 1 NIT | 12.2 | untested language path — C/C++ functions silently dropped; exported-method over-seeding, INSERT…SELECT label, jsfetch guard |
| F | opus · sonnet · haiku | 1 HIGH · 1 MEDIUM · 2 LOW | 16.0 | Ruby paren-less calls (all 3 converged); TS `export{}` re-export; trace_path vacuous-ok; SQL multi-statement |
| G | opus · sonnet · haiku | 1 MEDIUM · 2 LOW | 6.0 | **no HIGH; haiku clean** — parallel-edge dedup (fan_in/get_matrix), get_matrix cells, ORM relationship() phantom column |
| H | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN** — all three returned FINDINGS: none; regression-checked every recent fix, precision invariant on adversarial input, envelope contract, GraphBLAS agreement |
| I | opus · sonnet · haiku | 1 HIGH · 1 LOW | 11.0 | **confirmation gate caught a real HIGH** — `new Foo()` not edged in JS/TS/C#/C++ (live class flagged dead); read-only `global` false data_loop. Streak resets. |
| J | opus · sonnet · haiku | 3 HIGH | 30.0 | **use-form class fully exposed** — bare-name refs (opus); PHP `new` + Ruby `.new` missed by I's fix (sonnet). One general by-name-reference pass closes the class. |
| K | opus · sonnet · haiku | 1 HIGH · 1 MEDIUM | 14.0 | tail of the class — Python signature type annotations (opus); + a self-inflicted regression: J's `_direct_refs` `id()`-skip failed → spurious REFERENCES self-loops (opus+sonnet). haiku clean. |
| L | opus · sonnet · haiku | 1 HIGH · 2 MEDIUM | 18.0 | constructing a class didn't reach its constructor → `__init__` constructions flagged dead (haiku); Python twins of K's metric fixes — CALLS+REFERENCES double-edge (opus), REFERENCES self-loop (sonnet) — fixed at the dedup boundary. |
| M | opus · sonnet† · haiku | 2 HIGH · 3 MEDIUM | 32.0 | PHP public class flagged dead (opus). Third-party "sonnet" (core-only env) found the blind spot: config from cwd not indexed root (HIGH); ingest_trace success on zero grounding; old-DB edge migration + index ordering; core-only CI red (unguarded test imports). haiku clean. _(†sonnet API down — review supplied by a third party)_ |
| N | opus · sonnet† · haiku | 1 HIGH | 10.0 | **opus + haiku converged** — a class/fn used only as a Python parameter *default value* (`def f(x=Strategy)`) flagged dead (`func.args.defaults`/`kw_defaults` not walked — 3rd "body-not-signature" gap after K's annotations). `_annotation_names` now covers defaults. Third-party "sonnet" (core-only) clean, re-confirming M's fixes. _(†third-party review)_ |
| O | opus · sonnet† · haiku | 1 MEDIUM | **4.0** | **lightest since H** — opus + haiku converged on one narrow MEDIUM: a metaclass used only via `class X(metaclass=Meta)` flagged dead (`_walk_scope` edged `child.bases` not `child.keywords`). Now walks class-def keywords. Third-party "sonnet" (core-only) clean **2nd straight panel**. _(†third-party review)_ |
| P | opus · sonnet† · haiku | 1 HIGH | 10.0 | **the last un-walked Python scope** — references in the class *body* itself (`h = Helper`, `TABLE = {"a": handle_a}`, class-level annotations) were never extracted: `_walk_scope` edged bases/keywords/ctors and recursed into method bodies, but never the class body's own statements → live symbols flagged dead (opus HIGH). Now walks class-body Load names, attributed to the class node (matching tree-sitter). haiku clean; third-party "sonnet" (core-only) clean **3rd straight panel**. _(†third-party review)_ |

## What each panel found and how it was fixed

- **Panel A (opus · sonnet · haiku)** — every finding was a *symmetry gap*: a
  guard/rule applied in one extractor or resolver but not its siblings. All seven
  were reproduced from real input and fixed with a regression test
  (`tests/test_regressions.py`):
  - **HIGH (opus)** — `EMITS`/`HANDLES` were missing from `LIVENESS_RELATIONS`, so
    an event handler registered + fired from a live entry point was flagged stale
    (a precision-invariant violation). Added both relations; liveness now crosses
    the pub/sub boundary as it already did for `ROUTES_TO`/`RENDERS`.
  - **HIGH (haiku)** — the Django-route, event, and Express resolvers each gated
    edge creation on `if len(cands) == 1:` with no `else`, silently dropping a
    handler edge whenever a name was shared — risking a live handler called dead.
    Now they link to *all* candidates as `AMBIGUOUS` (mirroring the AST extractor).
  - **HIGH (sonnet F1)** — the CLI rebuilt every parameter as `str`, so `--limit 5`
    arrived as `"5"` (a `TypeError` on the int comparison) and bool flags inverted.
    The wrapper now preserves each param's real type.
  - **MEDIUM (sonnet F2)** — `scan` assigned a live stub `RED` regardless of how it
    was reached, bypassing the provenance ceiling. RED now requires an
    EXTRACTED-only reachable path; an inferred (heuristic) path caps at ORANGE.
  - **MEDIUM (sonnet F3)** — a Rust `impl<T> Container<T>` block resolved to the
    type parameter `T`, mis-attributing every method. `_trailing_id` now names the
    base type, skipping `type_arguments`/`type_parameters`.
  - **MEDIUM (sonnet F4)** — public methods of an exported class weren't seeded, so
    they could be false-flagged dead though external callers reach them. Public
    (non-underscore) methods of an exported class now inherit the `exported` role.
  - **LOW (sonnet F5)** — `[review] threshold` was a documented `stitchgraph.toml`
    knob that nothing consumed. `config.load_config` now applies it to the envelope
    (one-directional: config → envelope, keeping envelope stdlib-only).

- **Panel B (opus · sonnet · haiku)** — yield fell 43 → 15 as Panel A's fixes
  held; every finding was the *same* symmetry gap in a sibling Panel A hadn't
  touched. All reproduced and fixed with regression tests:
  - **HIGH (opus + sonnet, converged)** — the tree-sitter twin of F4: public
    methods of an `export class` (JS/TS) weren't seeded `exported`, so they were
    listed as dead-code candidates (a precision violation on live API surface).
    Added `_seed_exported_class_methods`, mirroring the Python extractor; the two
    models independently landing on the same defect is strong confirmation.
  - **MEDIUM (haiku)** — the HTML-form and JS-fetch resolvers indexed routes by
    path alone, so when `GET /x` and `POST /x` both existed a form/fetch linked to
    only one — `trace_path` could miss the real target. Now they link to *all*
    routes sharing a path (AMBIGUOUS when several), like `routes.py`/`express.py`.
    (Re-assessed from haiku's HIGH: route nodes are themselves seeds, so no handler
    is flagged dead — the impact is trace completeness, not a precision violation.)
  - **LOW (sonnet)** — `gitrisk` hard-filtered git history to `.py`, so `risk()`
    was silently empty (and misleadingly refused) on polyglot repos. The churn /
    co-change scraper now accepts every indexed source extension.

- **Panel C (opus · sonnet · haiku)** — yield ticked up to 18 (non-monotonic, as
  the methodology expects) as the panel reached surfaces the first two hadn't.
  All reproduced and fixed with regression tests:
  - **HIGH (haiku)** — the tree-sitter extractor lacked the Python extractor's
    `_apply_callback_roles`, so methods of a framework-base subclass (e.g.
    `class MyButton extends React.Component`) were flagged dead though the
    framework invokes them. Added `_seed_callback_roles`. (The agent proposed the
    fix directly in the tree; on review its `_PLAIN_BASES` wrongly listed framework
    bases like `HTMLElement`/`EventTarget` as "plain" — the unsafe direction — so
    that set was trimmed to built-in value constructors only.)
  - **MEDIUM (opus)** — the event resolver documented and listed `.connect` but
    only matched the 2-arg string form, so single-arg signal registration
    (`signal.connect(handler)`, blinker/Django/Qt) never linked and handlers were
    flagged dead. Added receiver-keyed events so `signal.connect(h)` and a bare
    `signal.send(...)` meet on the same event node — a broken docstring promise,
    now kept.
  - **MEDIUM (sonnet)** — a `WITH x AS (...)` CTE parses as a Table when
    referenced, so the SQL resolver minted a phantom `db::x` node. CTE aliases are
    now collected and skipped.

> Process note: Panel C agents shared the working tree and one edited source
> directly. Subsequent panels run in **isolated worktrees, strictly review-only**
> — they report findings; the maintainer adjudicates and applies.

- **Panel D (opus · sonnet · haiku)** — first run in isolated worktrees,
  review-only. Yield fell to 6; opus also fuzz-confirmed the GraphBLAS-vs-pure-Python
  reachability agreement over 3000 random graphs (0 mismatches) and cleared coverage
  ingestion, `find_similar`, the report/MCP/CLI adapters, and the envelope contract.
  - **MEDIUM (opus + sonnet, converged)** — `Store._resolve_worklist`, the
    incremental re-resolution path, linked an ambiguous hole to only one candidate
    (`COUNT(*) = 1` guard) — the lone resolution site not over-approximating. Now it
    links to *all* candidates as AMBIGUOUS, mirroring the extractors. The rarer
    cross-update homonym case (a hole uniquely resolved, then a same-named def added
    by a *later* single-file update) is documented in `LIMITATIONS.md`; `replace_file`
    is experimental and `reindex` (the wired path) is authoritative.
  - **LOW (haiku)** — the tree-sitter `_ref` filtered self-references for *all*
    relations, dropping the self-CALLS edge of a recursive function that the Python
    extractor keeps. The self-filter now applies only to INHERITS/IMPORTS.
  - **LOW (sonnet)** — `runtime._parse_json` admitted non-integer `executed_lines`
    from a malformed coverage file, crashing the later range test (LCOV/Go already
    int-cast). JSON now coerces and drops non-integers too.

- **Panel E (opus · sonnet · haiku)** — the panel reached an *untested* surface and
  the headline find justifies the whole exercise:
  - **HIGH (sonnet)** — C and C++ produced **zero** function/method nodes: `_name_of`
    read the function's `type` field (the *return type*) before the `declarator`, so
    every `function_definition` resolved to `None` and was silently dropped — the
    entire C/C++ call graph was empty, though `docs/LANGUAGES.md` claimed ✅ and no
    C/C++ test existed to catch it. `_name_of` now reads the declarator first (the
    `type` field is kept only for Rust `impl` targets); a C/C++ regression test guards
    it. `Widget* create()` also now resolves to `create`, not `Widget`.
  - **LOW (opus)** — `_seed_exported_class_methods` over-marked Java/C# *private*
    methods of a public class as `exported` (its docstring claimed those languages
    were unaffected). Now gated to the JS/TS family, where visibility is inherited
    from the class; Java/C#/Go/Rust/PHP keep their correct per-method roles.
  - **LOW (sonnet)** — `INSERT INTO archive SELECT … FROM users` labelled the SELECT
    source `users` as WRITES (top-level statement type applied to every table). The
    DML target is now distinguished from read sources.
  - **NIT (haiku)** — `jsfetch.py` lacked html.py's `"METHOD /path"` route-name guard
    (zero practical impact; added for defensive symmetry).

> Process note: a Panel E review worktree ran its own `pip install -e .`, repointing
> the editable install at a stale worktree copy — caught when a verified fix appeared
> not to take. Worktrees are now pruned and the install repointed to the main tree;
> all fixes re-verified there. Review worktrees must not install the package.

- **Panel F (opus · sonnet · haiku)** — review-only on the main tree (worktree
  isolation was retired after the Panel E contamination). All three models
  **independently converged** on the same defect — the strongest confirmation in the
  series — closing the last untested language path:
  - **HIGH (opus + sonnet + haiku, converged)** — Ruby's idiomatic paren-less,
    receiver-less call (`validate`) parses as a bare `identifier`, not a `call`
    node, so those CALLS edges were dropped and a method reached only that way looked
    dead (precision violation). Added a `bare_calls` LangSpec flag and `_is_bare_call`,
    which treats a bare identifier as a call unless it is structurally a def/param/
    assignment-target/receiver; resolution goes through `_ref`, which links only to
    project-defined methods (the safe over-approximating direction).
  - **LOW (opus)** — `trace_path` returned `refuse(..., result=[])` for "no path",
    and `refuse` sets `ok = result is not None`, so a genuine no-path came back
    `ok=True` with an empty result — which had masked the Ruby bug (the polyglot test
    asserted only `.ok`). "No path" is now a clean refusal (`ok=False`).

  - **MEDIUM (sonnet)** — a TypeScript named re-export `export { Widget }` did not
    seed the `exported` role (only inline `export class` did), so the re-exported
    class and its methods were false-flagged dead. A re-export post-pass now marks
    matching nodes exported (mirroring Python's `__all__`).
  - **LOW (sonnet)** — a multi-statement SQL string (`DELETE …; SELECT …`) parsed via
    `parse_one` as a single `Block`, so the DML target was mislabelled READS. Now
    parsed with `parse()` and classified per statement.

  With C/C++ (Panel E) and Ruby (Panel F) fixed, **all 11 tree-sitter languages are
  now verified to extract defs + a call graph** (opus exercised each on real input).

- **Panel G (opus · sonnet · haiku)** — the lightest panel: **no HIGH, no precision
  violation, and haiku came back fully clean** (`FINDINGS: none` after 41 custom
  tests). The findings moved off the core onto metrics/data-side polish, and the
  panel confirmed large swaths clean (SQL UNION/INTERSECT/window/`INSERT…SELECT`/
  `ON CONFLICT`/CTE-writes, `get_matrix` bounds, `find_similar` degenerate inputs,
  trace_path confidence math, LCOV/Go coverage edges).
  - **MEDIUM (opus)** — parallel resolved edges (two call sites to the same target,
    or the jedi resolver re-confirming an AST edge under `--precise`) were stored
    separately, inflating `fan_in`/`fan_out` and the `fan_in`-fallback hubs. `reindex`
    now collapses parallel `(src, relation, dst_id)` edges (max weight), matching the
    boolean/GraphBLAS layer that already dedups.
  - **LOW (opus)** — `get_matrix` sparse `cells` and `density` double-counted repeated
    call sites (the dense grid was already idempotent). Cells are now one per (src,dst).
  - **LOW (sonnet)** — the ORM resolver emitted phantom `DBColumn` nodes for a
    SQLAlchemy `relationship()` / Django `ManyToManyField` (virtual / junction-table,
    not a column), polluting the schema view and trace_path. Those are no longer
    treated as columns; real `Column(...)`/`*Field` mappings are unchanged.
    (Sonnet also probed an LCOV all-zero-coverage case and correctly *dismissed* it —
    "the trace ran, nothing hit" is the right semantics, not a bug.)

- **Panel H (opus · sonnet · haiku)** — **the first fully-clean panel**: all three
  models returned `FINDINGS: none` at full diversity. This was not a light review —
  each independently regression-checked every recent fix (parallel-edge dedup keeps
  distinct AMBIGUOUS candidates; Ruby `_is_bare_call` in every statement position; TS
  re-export incl. aliases / `export default`; C/C++; ORM), exercised the precision
  invariant on a deliberately adversarial project (framework-callback subclass +
  exported `__all__` API + `__main__` + private helpers → only genuinely-unreferenced
  symbols flagged, advisory, never live code), and confirmed the envelope contract and
  GraphBLAS-vs-pure-Python agreement. RRS crossed **90.2**; clean streak **1 of 2**.

- **Panel I (opus · sonnet · haiku)** — **the confirmation gate did its job.** After a
  fully-clean Panel H, opus found a real precision violation on novel input, proving
  why two consecutive clean panels are required (one clean panel is not proof):
  - **HIGH (opus)** — a `new Foo()` constructor call produced no CALLS edge in
    tree-sitter JS/TS/C#/C++, so a class instantiated only via `new` inside a live
    function was flagged dead (live code flagged dead — the cardinal sin). Java's
    `object_creation_expression` and Python's `ClassName()` already model constructor
    calls, so this was a Python/Java ↔ JS/TS/C#/C++ symmetry gap. Added the
    constructor-call node type to each LangSpec (JS/TS `new_expression.constructor`,
    C# `object_creation_expression.type`, C++ `new_expression.type`; Rust unchanged).
  - **LOW (sonnet)** — `_global_state` emitted a WRITES edge for a *declared but
    never-assigned* global (`global x; return x`), faking a read+write data feedback
    loop in `scan()`. A WRITES now requires an actual `ast.Store` assignment; a genuine
    read-modify-write still forms its loop. (Sonnet judged the rest clean at
    release-blocking severity after 52 scenarios.)

  Clean streak **reset to 0**; RRS back to 79.2. The two-clean-panel rule paid for
  itself here — Panel H alone would have shipped the `new`-expression bug.

- **Panel J (opus · sonnet · haiku)** — exposed the full extent of the *use-form not
  modeled* class that Panel I cracked open. Three HIGHs, all "a symbol used only by
  name from a live entry is flagged dead":
  - **HIGH (opus)** — bare-name value references weren't modeled at all: a function
    passed as a callback (`register(handler)`), a class accessed as `Color.RED`, a
    factory `Widget.create()` — the symbol got no edge and looked dead. (`Widget`
    flagged while `Widget.create` stayed live was internally contradictory.)
  - **HIGH (sonnet)** — Panel I's `new` fix missed **PHP** (`object_creation_expression`
    has no `type` field) and **Ruby** (`Service.new` parses as method `new` on receiver
    `Service`), so those constructors still flagged the class dead.
  - **haiku** — read the bare-name cases as the documented advisory limitation
    (`FINDINGS: none`); the maintainer adjudicated them as real per opus/sonnet, since
    the extractor already models attr-reads/decorators/constructors precisely to avoid
    this, making by-name references an unintended omission.

  **The fix is general, not case-by-case:** a single `_direct_names` pass (Python) /
  `_direct_refs` pass (tree-sitter) now emits a REFERENCES edge for *every* identifier
  used by name in a function body (over-approximated through `_ref`, so only project
  symbols resolve). That closes the entire class — callbacks, enum/factory access, and
  any-grammar constructor idiom — in one mechanism. `find_stale` on stitchgraph's own
  source dropped to 3 advisory candidates (the genuinely-questionable + documented
  module-level cases), no longer false-flagging common patterns. Module-level uses
  (the `SPECS` table) remain the documented limitation. RRS 79.2 → 73.7; the next
  panels test whether the class is truly closed.

- **Panel K (opus · sonnet · haiku)** — the panel that confirmed Panel J's fix and
  caught its tail + a regression it introduced. haiku came back clean; the other two
  found:
  - **HIGH (opus)** — the *one* by-name use-form Panel J's body-only pass missed:
    **parameter/return type annotations** live in `func.args`/`func.returns`, not the
    body, so a class used only as `def f(x: Config) -> Fwd` (or `list[Gen]`, or a
    string forward-ref) was still flagged dead. Added `_annotation_names` to edge them
    (the tree-sitter extractor already covered this by walking the whole def node — a
    Python↔tree-sitter asymmetry).
  - **MEDIUM (opus + sonnet, converged)** — a regression Panel J introduced:
    `_direct_refs` skipped the def's own name via `id(name_node)`, but tree-sitter
    hands back a fresh wrapper per access so `id()` never matched — every def emitted a
    spurious REFERENCES self-loop, and callees got a CALLS+REFERENCES double-edge,
    re-inflating `fan_in`/`get_matrix`/god-object detection (the class Panel G closed).
    Fixed by skipping the name by byte-span, dropping REFERENCES self-edges in `_ref`,
    and not emitting a REFERENCES edge for a name already linked as CALLS.

  The confirmation pair did its job again: the aggressive Panel J change had both a
  missed corner and a self-inflicted side-effect, and the side-effect audit caught
  both. RRS 73.7 → 78.4.

- **Panel L (opus · sonnet · haiku)** — one more real precision corner plus the
  Python twins of Panel K's tree-sitter metric fixes:
  - **HIGH (haiku)** — **constructing a class didn't reach its constructor.** `Foo()`
    edges to the class node, but nothing linked the class to `__init__`, so a class
    built inside another's constructor (`Service.__init__` doing `self.r = Resource()`)
    left `Resource` unreachable and flagged dead. Now `class -> constructor` is modeled
    in both extractors (Python `__init__`/`__new__`/`__post_init__`; tree-sitter
    `constructor`/`initialize`/`__construct` and the class-named Java/C#/C++ ctors).
  - **MEDIUM (opus)** — the Python extractor emitted both a CALLS and a REFERENCES edge
    to a symbol it both calls and names/annotates (`def build() -> Node: return Node()`),
    double-counting fan_in/pagerank (23 such pairs on stitchgraph's own src).
  - **MEDIUM (sonnet)** — the Python extractor also left REFERENCES self-loops (a def
    naming itself), inflating its own fan_in. (sonnet's slot hit a 500 mid-write-up;
    the finding was salvaged from its transcript and fixed.)

  opus's and sonnet's were the **Python twins** of the tree-sitter metric fixes from
  Panel K — the recurring "fix one extractor, miss its sibling" theme. Both are now
  fixed **language-agnostically at the dedup boundary** (`_dedup_edges`: a CALLS edge
  subsumes a REFERENCES edge to the same target; REFERENCES self-loops are dropped),
  which should stop this twin class recurring. RRS 78.4 → 77.3.

- **Panel M (opus · sonnet† · haiku)** — the panel that reached the **core-only /
  persistence** blind spot. opus (full extras) found one more precision corner; the
  third-party "sonnet", running in a *core-only* environment (no extras installed),
  found the class of defects the all-extras agents structurally cannot see:
  - **HIGH (opus)** — a **PHP public class** with only public methods was flagged dead:
    `exported` was set on the methods but never propagated up to the class node, so the
    class looked unreferenced. `_seed_classes_from_exported_methods` now up-propagates
    `exported` from public methods to their class (tree-sitter).
  - **HIGH (sonnet, core-only)** — config (`stitchgraph.toml`: roots, ignores) was
    loaded from the **current working directory**, not the indexed project root, so
    every operation run from outside the project silently used the wrong (or no) config
    — entry-point roots vanished and live code flagged dead. `_hub_ranking` and
    `_default_detector` now read config from `store.get_meta("root")`.
  - **MEDIUM (sonnet)** — `ingest_trace` returned `ok=True` even when the trace grounded
    *nothing* (no node matched any executed line), claiming a runtime grounding that
    didn't exist. It now refuses (`ok=False`, no `has_runtime`) when `hits` is empty.
  - **MEDIUM (sonnet)** — `_migrate()` only patched the `nodes` table; an old DB missing
    the new `edges` columns crashed, and `CREATE INDEX … ON edges(file)` ran *before*
    migration. Split `_SCHEMA`/`_INDEXES` so indexes run after `_migrate`, and migrate
    both tables.
  - **MEDIUM (sonnet)** — the core-only CI job went red: `test_properties.py` /
    `test_runtime.py` imported `hypothesis` / `tree_sitter_language_pack` unguarded.
    Added `pytest.importorskip` so the core-only suite passes (32 skips, 0 failures).
  haiku clean. The lesson: **a panel is blind to whatever its environment doesn't
  install** — rotating a core-only reviewer in is how those defects surface. RRS 77.3 → ~78.

- **Panel N (opus · sonnet† · haiku)** — **opus + haiku converged** on a single HIGH, the
  3rd instance of the recurring "Python walks the body, not the signature" asymmetry
  (after Panel K's annotations):
  - **HIGH (opus + haiku)** — a class/function used only as a **parameter default value**
    (`def f(x=Strategy)`, `cb=handler`) was flagged dead: defaults live in
    `func.args.defaults`/`kw_defaults`, which neither the body pass nor the annotation
    pass walked. `_annotation_names` now also walks `a.defaults` + `a.kw_defaults`.
  Third-party "sonnet" (core-only) clean, re-confirming Panel M's fixes. Two independent
  models converging on the same omission is the signal the surface is nearly exhausted.

- **Panel O (opus · sonnet† · haiku)** — the **lightest panel since clean H**: opus +
  haiku converged on one narrow MEDIUM:
  - **MEDIUM (opus + haiku)** — a class used only as a **metaclass**
    (`class X(metaclass=Meta)`) was flagged dead: `_walk_scope` edged `child.bases` but
    not `child.keywords`, where the metaclass (and similar class-def kwargs) sit. Now
    walks class-definition keywords and edges them as REFERENCES.
  Third-party "sonnet" (core-only) clean for the **2nd straight panel**. RRS held ~79.

- **Panel P (opus · sonnet† · haiku)** — the **last un-walked Python scope**. opus found
  one HIGH; haiku and the third-party "sonnet" were clean:
  - **HIGH (opus)** — references in the **class body itself** were never extracted.
    `_walk_scope` edged a class's bases, keywords, and constructor links, and *recursed
    into its method bodies*, but never processed the class body's own statements:
    class-level attribute assignments (`h = Helper`), dispatch tables
    (`TABLE = {"a": handle_a, "b": handle_b}`), and class-level annotations. A symbol
    used only there — live iff the class is reachable — was flagged dead. The fix walks
    the class-body Load-context names via `_direct_names` and attributes the REFERENCES
    to the class node, matching tree-sitter (which walks the whole class node). Verified:
    in `class Container: h = Helper` / `class Router: TABLE = {…handle_a…}` reachable from
    a live `run()`, `Helper`/`handle_a`/`handle_b` are now live while `truly_dead` stays
    flagged.
  haiku clean; third-party "sonnet" (core-only) clean for the **3rd straight panel**. The
  clean streak holds at 0 (opus's HIGH resets it) — but the entire "live-code-flagged-dead"
  precision class is now closed along every axis the Python/tree-sitter asymmetry exposed:
  by-name refs, constructors, annotations, defaults, metaclass keywords, and class bodies.

## Standing themes

- Convergence is non-monotonic and never reaches zero — measure residual risk.
- Late-stage defects are symmetry gaps: a guard present in one language extractor
  or resolver but not its siblings. Audit by a path×behaviour matrix.
- Blind spots: tree-sitter / graphblas / sqlglot / jedi / mcp surfaces are gated
  by optional deps; a panel is blind to them unless the extras are installed.

_Maintenance: append a trajectory row + a bullet per panel; keep the TL;DR in
sync with `release_readiness.json`._
