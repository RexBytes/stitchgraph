# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 38 (Panels A–LL; FF–LL are the post-sonnet-restoration full-3-model panels for 1.0.2/1.0.3/1.0.4) |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ (dev 1.10.6 **and** pinned bundled 0.13.0) · no-open-defects ✅ |
| Tests | 177 passing, 0 skipped (full extras) |
| Coverage | ~86% |
| Convergence | 1.0.0: U→V streak 2 → released. 1.0.1: W→X clean → released. 1.0.2: HH→II streak 2 → released. 1.0.3: JJ clean → released. 1.0.4: KK (1 MEDIUM Python-asymmetry + 1 LOW C# ctor, both fixed) → **LL** (fresh confirmation in progress) |
| Dogfood (self) | find_stale 3 advisory (no false-dead) · holes 0 |
| Verdict | **1.0.0/1.0.1/1.0.2/1.0.3 RELEASED** (maintainer tags). **1.0.4** (confidence honesty #10/#11/#15) prepared; Panel KK findings fixed, awaiting the LL+ clean streak and the maintainer's manual `v1.0.4` tag |

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
| Q | opus · sonnet† · haiku | 1 CRITICAL · 1 HIGH · 1 MEDIUM | **54.0** | **the confirmation gate caught a CRITICAL** — a symbol used only inside a function-*local* class/closure flagged dead (`_def_node` never descended into function bodies → function-local defs were never nodes, yet `_walk_scope` emitted edges from phantom qualnames; opus). Now models nested defs as real nodes + a `function → nested` containment edge (fixes decorator-registered `@app.command` handlers too). Third-party "sonnet" (core-only): public re-exports from `__init__` weren't export roots → live public API flagged dead (HIGH); version still `0.3.0` (MEDIUM, deferred to manual release). haiku clean. **Streak resets.** _(†third-party review)_ |
| R | opus · haiku ‡ | 2 MEDIUM | **8.0** | **the metric/nesting twin of Q's nested-def fix, one in each extractor — both err safe (no precision violation).** opus (sonnet converged on the class-body variant in a partial pass before its slot was stopped): the five Python `_direct_*` own-scope helpers leaked into nested defs — the driver loop ran `rec()` on a top-level stmt that was *itself* a nested def, mis-attributing its calls/refs/globals to the enclosing fn (162 spurious CALLS + 40 class→symbol REFERENCES on self; 15→11 god-objects). Q made it observable (nested defs are nodes now, so the spurious parent edge double-counts instead of being a dropped phantom). Fixed by skipping a top-level body stmt that is itself a def, in all five helpers. haiku: the **tree-sitter side** of Q's nesting — function-local defs were created at *module* scope (`app.ts::helper`), merging two same-named defs into one node; now nested under the enclosing qual + an `enclosing-fn → nested` containment edge (Python parity). Reproduced as errs-safe → **downgraded from haiku's CRITICAL to MEDIUM**. **Streak stays 0.** _(‡ sonnet slot stopped — API issues this session; diversity 2/3)_ |
| S | opus · haiku | 1 CRITICAL | **40.0** | **the confirmation gate caught a CRITICAL regression Panel R itself introduced** — the textbook case for why consecutive clean panels are required. opus: R's body-skip made each `_direct_*` driver `continue` on a nested def statement, but a def's **header** (decorator args `@registry(make_validator())`, nested-class base exprs `class L(get_base())`) executes in the *enclosing* scope at def-time and was dropped — not recovered elsewhere → a symbol used only there had zero inbound edges and was flagged dead. Pre-R this erred safe (over-attributed to the enclosing fn); R flipped it to erring **unsafe** (false dead, the cardinal sin). Fixed with `_def_header_refs`: walk a nested def's header (decorators + class bases/keywords) in the enclosing scope while still skipping the body. haiku **clean** (48 adversarial tests, no findings). **Streak resets to 0.** |
| T | opus · haiku | 2 CRITICAL | **80.0** | **the last two nesting hosts of the Panel Q class — both CRITICAL false-deads, both pre-existing (not regressions).** haiku: a Python def nested in a **control-flow block** (`if`/`for`/`while`/`try`/`with`/`match`) was never modeled (`_def_node`/`_walk_scope` walked only the *direct* body) → phantom-source edges → a symbol used only there flagged dead. opus: the tree-sitter twin — a def nested in a JS/TS **arrow function** (`const h = () => { function w(){…} }`) was never modeled (the arrow branch made the node but never recursed into the arrow body). Closed by a **systematic nesting-host audit** (function/class/control-flow/arrow are the only hosts; lambdas/comprehensions can't hold defs): Python `_scope_defs` looks *through* control flow in `_def_node`/`_walk_scope`/`_iter_funcs`/module loop; tree-sitter arrow branch now recurses + emits the containment edge. Test matrix pins every host (140→148). Dogfood: find_stale still 3 advisory, 0 holes. **Streak resets to 0; the enumeration is now believed complete.** |
| U | opus · haiku | _none_ | **0.0** | **CLEAN** — both models `FINDINGS: none` at full diversity, the first clean panel since the nesting audit. Scope also included a **regression-test-quality audit** (after two test-authoring slips in R/S): both confirmed the Panel Q–T nesting tests are **non-vacuous** — opus monkeypatched `_scope_defs` back to a pre-fix variant in `/tmp` and saw **5/5** control-flow tests FAIL, then pass post-fix. No still-unmodeled def host found; body-leak (R) and header-leak (S) fixes hold; no metric inflation (`_dedup_edges` collapses the CALLS+REFERENCES pair, fan_in==1); module-level `try: import`/`TYPE_CHECKING` idioms sane. Dogfood: 3 advisory, 0 holes. **RRS crosses 90.4; clean streak 1 of 2.** |
| V | opus · haiku | _none_ | **0.0** | **CLEAN — the second consecutive clean panel; the release gate is met.** Both `FINDINGS: none`, steered onto the less-trodden surfaces so the clean is earned: SQL/ORM/event resolvers (CTE/UNION/INSERT…SELECT/MERGE, `mapped_column`/`relationship` exclusion, additive-only events), the full envelope contract (every `urgency=` assignment vs the provenance ceiling; scan-RED needs an EXTRACTED-only `certain_live` path; `ingest_trace` refuses on zero grounding; no `ok=True`-with-vacuous-result), persistence/migration, metric dedup (`LIVENESS_RELATIONS` excludes QUERIES/READS/WRITES/MAPS_TO), a **2000-graph GraphBLAS-vs-pure-Python fuzz (0 mismatches)**, and precision use-forms (walrus/comprehensions/f-strings/forward-refs/`__all__`). Two non-defect sub-observations recorded (SQL MERGE labels target READS not WRITES — err-safe, uncommon; `find_holes` urgency=ORANGE on empty list — cosmetic), neither a finding. Dogfood: 3 advisory, 0 holes. **Gates green · RRS 93.3 · clean streak 2/2 → RELEASABLE.** |
| — | _1.0.0 released (tag `v1.0.0`, 2026-06-23)_ | | | _Three issues then raised in field use on a Rust crate (#7, #8, #9); fixed as the 1.0.1 batch and confirmed by panels W–X._ |
| W | opus · haiku | 1 MEDIUM | 4.0 | **1.0.1 confirmation — found one err-safe over-match in the just-landed #8 fix.** Both models converged: the Rust test-attribute detector used `any("test" in a for a in attrs)`, a raw substring, so `#[cfg(feature="testing")]` / `#[doc="…test…"]` (and features like `latest`) wrongly seeded the `test` role on production code — *hiding* genuinely-dead code. Errs safe (over-mark → keep live → never a false-dead, so **not** a cardinal violation), but real. #7 (grammar-load surfacing) and #9 (impact_of homonym scoping incl. dotted-name and false-resolution probes) both verified **correct**. Fix: match the attribute **path** (`_is_rust_test_attr`: `test` / `*::test` / bare `cfg(test)`), + a both-directions regression test. |
| X | opus · haiku | _none_ | **0.0** | **CLEAN — 1.0.1 re-confirmation of the Panel W fix.** Both `FINDINGS: none`. The path-based matcher verified in **both directions**: false-positives gone (`#[cfg(feature="testing")]`, `#[doc]`, `#[allow]`, `#[derive]`, a feature literally named `test`), no false-negatives (`#[test]`, `#[ test ]`, `#[tokio::test]`/`*::test`, `#[cfg(test)]`, `#[cfg(all(test, feature="x"))]`, inner attrs), attribute-bleed correct, core #8 liveness holds (tests + reached helpers live; unreached helper + unused production still flagged). #7/#9 re-confirmed; dogfood 3 advisory / 0 holes. One err-safe note (third-party runner macros `#[rstest]`/`#[test_case]` under-marked → now documented in `LIMITATIONS.md`), not a finding. **1.0.1 ships on this clean dual-model confirmation.** |
| — | _maintainer asked: do the other languages have the #8 gap too?_ | | | _Yes — confirmed empirically. #8's root cause (only the name convention seeds the `test` role) flagged live tests dead in Java/C#/PHP (annotations) and JS/TS/Ruby (call-based) and Python (test classes). Folded a **polyglot generalization** into 1.0.1; panels Y–DD drove it to closure._ |
| Y | opus · haiku | 1 MEDIUM | 4.0 | **Polyglot test detection review — one err-safe over-reach.** The new generalization (Java/C# `@Test`/`[Fact]`, PHP `#[Test]`, JS/TS/Ruby call-based `test()`/`it()`, `_seed_test_classes`, module-call rooting) verified clean both directions — annotation allowlist exact, call-based helpers live + dead helpers flagged. Both models converged on one MEDIUM (Panel W class): `_is_test_file` matched `testing/`/`specs/` dirs, plausibly *production* (Go `testing`, OpenAPI `specs`) → their module-level calls rooted, *hiding* dead code. Err-safe, not cardinal. Fix: trim the dir set to `test`/`tests`/`spec`/`__tests__`. |
| Z | opus · haiku | 1 CRITICAL | **40.0** | **opus caught a CARDINAL the breadth model missed: a pytest `class TestWidget:` whose methods are all `test_*` was flagged dead.** Recognized test methods become `NodeKind.TEST` (not `METHOD`), so the callback path only rescued classes with a *non-test* override (`setUp`); the dominant all-`test_*` class got no role, no inbound liveness edge → flagged. Live, framework-collected code reported dead — the release-blocking class. Fix: `_seed_test_classes` in the Python extractor (a class with any `NodeKind.TEST`/`test`-role member is seeded `test`). Bare `class Meta(type)` still flags. **Streak resets.** |
| AA | opus · haiku | 2 CRITICAL | **80.0** | **two CARDINAL siblings of Z, same 'container live / flagged dead' shape one level removed (both pre-existing).** opus: (1) a test class that inherits all its tests from a *custom* base (idiomatic JUnit abstract-base + thin-subclass; pytest inherited tests) had no direct test member → not seeded → flagged; (2) the *outer* of a nested test class was flagged (only the inner was seeded). Fix: `_seed_test_classes` grows the seed set transitively — enclosing classes (up the qual chain) **and** subclasses of a test base — in both extractors. Java FQ/args annotations (`@org…Test`, `@Test(timeout=…)`) verified handled. |
| BB | opus · haiku | 1 CRITICAL · 1 MEDIUM | **44.0** | **opus broke the AA fix: the two growth axes ran sequentially, not to a *combined* fixed point.** A class seeded via inheritance that is *also* nested never had its enclosing chain re-walked → outer flagged (idiomatic pytest grouping `class TestApi: class TestV1(_SharedCases): pass`; Java nested-extends-abstract-base). Fixed by co-iterating both axes in one `while changed` loop (monotonic → terminates) in both extractors. MEDIUM (err-safe): tree-sitter resolved bases by name across *all* languages → a same-named test base in another language seeded production classes; fixed by keying resolution `(lang, name)`. **Streak resets.** |
| CC | opus · haiku | 1 CRITICAL · 1 MEDIUM | **44.0** | **opus: the Python/tree-sitter `is_test_file` had drifted** — Python checked only the filename, tree-sitter also directories — so a shared test base in `tests/conftest.py` got no `test` role and its inheriting subclass was flagged dead (CARDINAL). Fixed by extracting one **shared directory-aware `is_test_file`** (`_testfile.py`) used by both extractors, so they can't drift again. haiku (MEDIUM, err-safe): `#[cfg(not(test))]` *production* code was marked a test root (the bare `test` token sat inside `not(...)`); fixed by dropping `not(...)` predicates before the scan. **Streak resets.** |
| DD | opus · haiku | _none_ | **0.0** | **CLEAN — convergence verdict: the test-liveness class is CLOSED for 1.0.1.** Both `FINDINGS: none`. opus confirmed all CC fixes (conftest base + subclass live; full Rust `cfg` matrix incl. `not(test)`/`all(not(test),…)`) and ran a final convergence attack (deep nesting × inheritance, mixins, metaclasses, JUnit `@Nested`) — no remaining live→dead case. The one residual (a test base in a *non-test* directory subclassed by an own-method-less test) is realistic-but-rare, precision-safe, `needs_review`-only, has escape hatches, and closing it generically would regress the Panel Y/W over-match guard → kept as a **documented limitation**. haiku full-matrix sweep clean. Dogfood 3 advisory / 0 holes; 165 tests. |
| EE | opus · haiku | _none_ | **0.0** | **CLEAN — second consecutive clean panel; the 1.0.1 gate is met (streak 2, RRS confidence 0.86 → RELEASABLE).** Both `FINDINGS: none`, fresh independent confirmation. opus ran 11 cross-language scenarios it had not used before (Python pytest/unittest/inherited/nested/deep-chain; Java JUnit package-private abstract base + cross-file inherited base; C#/PHP/JS/Ruby call-based; Rust `#[tokio::test]`; Go cross-file; mixed suites) — only genuinely-dead helpers flagged. Over-marking bounded (bare metaclass, dead production, production `testing/` & `specs/` all flag); `#7` silent on a grammar-present run under `-W error`; `#9` candidates + qualified scoping across impact_of/get_callers/get_callees/trace_path; envelope contract intact (`find_stale` `needs_review`, conf ≤ 0.6, INFERRED). haiku full sweep clean. Dogfood 3 advisory / 0 holes; 165 tests. |
| — | _1.0.1 released (maintainer tag); sonnet's API confirmed available again → restored to the panel (full diversity = opus + sonnet + haiku once more)_ | | | _The 1.0.2 line below is the post-release confidence panel (FF) + the fixes it triggered._ |
| FF | opus · sonnet · haiku | 2 CRITICAL | **80.0** | **First full-3-model panel after restoring sonnet — diversity paid off immediately.** opus + haiku both clean, but FRESH sonnet (no memory of W–EE) caught **two CARDINAL false-deads the pair missed across the entire 1.0.1 cycle**: (1) JS/TS `export default Foo;` where `Foo` is defined earlier (canonical React/Angular/Vue/Node idiom) never set the `exported` role → flagged dead (PRE-EXISTING since 1.0.0; `_reexport_names` only handled `export { X }`); (2) Ruby a class used as a call receiver in an RSpec `describe/it` block (`Service.run`) got a CALLS edge to the method but no REFERENCES edge to the class → live class flagged dead (INTRODUCED by the 1.0.1 Bug-B `_module_calls`, which collected calls + bare idents but not `constant` receivers). Both confirmed by real-input repro. Fix: `_reexport_names` handles `export default <ident>`; `_module_calls`→`_module_uses` also collects name-references like `_direct_refs`. **Streak resets.** |
| GG | opus · sonnet · haiku | 1 CRITICAL · 1 MEDIUM | **44.0** | **Confirming the FF fixes — both correct (all three models), and opus + sonnet converged on a CARDINAL sibling of F1 (pre-existing):** the whole-module CJS/TS-interop forms `module.exports = X` / `export = X` / `module.exports = { A, B }` / `exports.x = Y` aren't rooted → live public export flagged dead (CommonJS is pervasive). Fixed by extending `_reexport_names` to those forms. sonnet also found a MEDIUM err-safe over-rooting: `_module_uses` descended into a `const helper = function(){}` (itself a def), over-rooting its body's refs when uncalled; fixed by skipping `variable_declarator`-with-function-value. Both fixed + regression-pinned. **Streak resets.** |
| HH | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN — export-rooting class declared CLOSED (streak 1 of the 1.0.2 gate).** All three `FINDINGS: none`. sonnet enumerated **all 19 JS/TS/CJS export forms** and verified each is rooted or intentionally-err-safe-unhandled (anon default, `export *`, `Object.assign`). opus: matcher tight (`obj.exports`/local `exports` not over-rooted), no metric inflation, `export =` doesn't false-fire on `export const`. F2 `_module_uses` over-rooting fix holds. Dogfood 3 advisory / 0 holes; 169 tests. |
| II | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN — second consecutive clean panel; the 1.0.2 gate is met (streak 2, RRS confidence 0.86 → RELEASABLE).** All three `FINDINGS: none`, fresh independent confirmation (~30 new export/test-receiver/nesting/cross-language shapes from sonnet; 25 export shapes + a non-export sub-agent sweep — SQL/ORM/events/envelope/metrics/persistence/CLI — from opus; full matrix from haiku). Export-rooting class confirmed closed; over-marking bounded; `#7`/`#9`, the four nesting hosts, and the 1.0.1 polyglot detection all hold; dedup/metric integrity intact. One borderline err-safe note (`module.exports = ns.Member` via a locally-built namespace not rooted) → documented limitation, advisory-only. Dogfood 3 advisory / 0 holes; 169 tests. |
| — | _1.0.2 released (maintainer tag). Then maintainer raised the packaging side of #12 (offline install)._ | | | _Investigated `tree-sitter-language-pack`'s 1.0.0 switch from bundled to download-on-first-use; chose Option 1 (offline-default pin + adaptive load + doctor). 1.0.3 below._ |
| JJ | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN — confirms the issue-#12 fix (1.0.3): offline-by-default grammars.** All three `FINDINGS: none`. Root cause: `tree-sitter-language-pack` switched at v1.0.0 from bundling grammars in the wheel to downloading them on first use (breaks offline/CI/air-gapped); the 1.0.1 `<2` bound didn't fix it. Fix (Option 1): pin `[treesitter]` to the bundled line `>=0.7,<1.0` (+ `tree-sitter>=0.25.2,<1`); opt-in `[treesitter-download]` = 1.x; adaptive `_load_grammar` (download-and-retry when the pack supports it, else #7 warn+skip); `stitchgraph doctor`/`--strict` self-check. Verified the `get_language→_load_grammar` swap is **byte-identical** (all 12 langs; success path makes zero `download()` calls), all 4 adaptive-load paths, doctor incl. not-installed, pin bounds (`[all]` stays offline-by-default). Offline operation independently verified in a throwaway venv on bundled 0.13.0 with the network OFF. All 12 grammars MIT-licensed. Dogfood 3 advisory / 0 holes; 172 tests. |
| — | _1.0.3 released (maintainer tag); #12 closed. Maintainer triaged the remaining open issues and asked to fix #10/#11/#15 as 1.0.4._ | | | _#10 single-candidate receiver calls over-claim EXTRACTED; #11 scan structural findings ignore edge provenance; #15 LIMITATIONS `--precise` wording. 1.0.4 below._ |
| KK | opus · sonnet · haiku | 1 MEDIUM · 1 LOW | **5.0** | **First 1.0.4 panel (#10/#11/#15).** opus + haiku `FINDINGS: none` (cardinal invariant verified directly — #10 keeps weight 1.0, no new false-dead; scan no crash/mislabel; jedi additive matches #15). opus raised a non-blocking NOTE that became a real consistency fix (MEDIUM): #10 was tree-sitter-only, so the **Python `ast` extractor** still over-claimed `EXTRACTED` on an unknown-receiver name-only bind (`x.save()` → lone project `save`) — a Python↔tree-sitter asymmetry; mirrored the demotion in `_call_edge`/`_ref_edges` (scope-aware `self.`/local-typed calls stay `EXTRACTED`). sonnet found a LOW 1.0.4 regression: a C# namespace-qualified constructor `new MyApp.Widget()` (callee node `qualified_name`, in `_RECEIVER_CALLEE`) was wrongly demoted; fixed generally — constructor call-fields (`"constructor"`/`"type"`) force `is_method=False` so any qualified ctor stays `EXTRACTED` (Rust `Widget::new()` / Ruby `Widget.new` are real method-call nodes, keep documented INFERRED). Both fixed + regression-pinned. **Streak resets** (fixable findings surfaced). |

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

- **Panel Q (opus · sonnet† · haiku)** — **the confirmation gate earned its keep again**,
  catching the first CRITICAL since the methodology began. The "next un-walked scope" after
  Panel P's class bodies turned out to be the *nested* scope:
  - **CRITICAL (opus)** — a symbol used only inside a **function-local class or closure**
    (`def run(): class Local: def helper(self): return Tool()`) was flagged dead. Root
    cause: `_def_node` descended into module-level and class children but **never into
    function bodies**, so function-local classes/functions were never created as nodes —
    yet `_walk_scope` *did* recurse and emitted edges from their qualnames
    (`run.Local.helper → Tool`), which, having no node at the source id, never participated
    in reachability. A shallow "leak" saved single-level nested functions but not
    function-local classes or doubly-nested closures — exactly the misfiring cases. The
    equivalent JS/TS is kept live by tree-sitter, so this was the same Python↔tree-sitter
    asymmetry, one scope deeper. Fix: `_def_node` now models nested defs as real nodes
    (quals aligned with `_walk_scope`), and `_walk_scope` adds a **`function → nested-def`
    containment edge** — a function-local def is live iff its enclosing function is
    reachable (it executes, is registered, returned, or called when the enclosing runs).
    That containment edge also fixes the case the new nodes would otherwise have *newly*
    false-flagged: function-local handlers whose liveness comes from **decorator
    registration** (`@app.command def _watch(...)` inside `build_app`), not a direct call.
    Verified the self-scan introduced no new candidates.
  - **HIGH (sonnet, core-only)** — **public re-exports from a package `__init__`**
    (`from .api import Public`) weren't treated as export roots: the `__init__`
    export-surface scan only added nodes with a `.name` (physically-defined funcs/classes),
    but `ast.ImportFrom` carries `.names` aliases, so re-exported public API — importable as
    `pkg.Public` — was flagged dead despite the code's own docstring claiming re-exports are
    roots. Now collects public `ImportFrom`/`Import` aliases (asname or leaf, skipping `_`
    and `*`) as exported names, additive with `__all__`.
  - **MEDIUM (sonnet)** — package version was still `0.3.0` in `pyproject.toml` /
    `__version__`. Bumped to **`0.4.0`** to mark the hardening progress so far; `1.0.0`
    is reserved for the actual release (RRS ≥ 90 + two clean panels + the maintainer's
    manual tag), so the version never claims release-readiness prematurely.
  - **haiku** clean (`FINDINGS: none`) after re-verifying all A–P fixes end-to-end.

  Weighted yield **54.0** (1 CRITICAL + 1 HIGH + 1 MEDIUM) — the heaviest panel since M,
  and the clean streak **resets to 0**. The lesson stands: each "clean-looking" surface has
  one scope deeper. With the nested scope now modeled, the function/class/module scope
  trichotomy is fully covered (module-level uses remain the one documented limitation).

- **Panel R (opus · haiku; sonnet stopped)** — the **metric / nesting twin** of Panel Q,
  with one finding in each extractor. Both **err in the safe direction** (over-attribution /
  recall miss), so neither is a cardinal-invariant violation — the confirmation gate is now
  catching residual *correctness* and *metric* defects, not precision violations. Each was
  reproduced with real input and fixed with a regression test:
  - **MEDIUM (opus + sonnet, converged)** — the five Python `_direct_*` "own-scope" walk
    helpers (`_direct_nodes`, `_direct_attr_reads`, `_direct_names`, `_direct_withs`,
    `_direct_calls`) promised "not crossing nested defs", but their driver loop
    `for stmt in func.body: rec(stmt)` ran `rec()` on a top-level statement that was *itself*
    a nested def. `rec()` guards def *children* but not the driver's own statement, so the
    nested def's calls/refs/global-writes leaked up and were mis-attributed to the enclosing
    function — **162** spurious parent→callee CALLS and **40** spurious class→symbol
    REFERENCES on stitchgraph's own source, surfacing **15** false god-objects in `scan()`
    (down to **11** after the fix). Latent for many panels, but **Panel Q made it
    observable**: now that function-local defs are real nodes, the spurious parent edge
    co-exists with the correct nested edge and *double-counts*, where before it was a
    phantom-source edge that got dropped. The `_dedup_edges` boundary cannot catch it (the
    spurious edge has a *different* src). Fixed by skipping a top-level body statement that
    is itself a `FunctionDef/AsyncFunctionDef/ClassDef` in all five helpers — which also
    correctly stops Panel P's class-body walk from absorbing method-body references. _(sonnet
    converged on the class-body variant in a partial pass before its slot was stopped on
    API issues this session.)_
  - **MEDIUM (haiku)** — the **tree-sitter side** of Panel Q's Python nesting fix. The
    tree-sitter `_collect` nested only *classes/containers* under their qual, never
    functions, so a function-local def was created at **module** scope (`app.ts::helper`
    instead of `app.ts::setup.helper`). Two same-named defs (a function-local one and a
    module-level or sibling one) then **collided to a single node id**, merging two distinct
    functions — corrupting `get_callers`/`get_callees`/`impact_of`/`get_matrix` for that
    name and inflating its degree. haiku rated it CRITICAL, but it was reproduced as
    **errs-safe** (the merged node stays reachable, so dead code is *under*-reported, never
    live code flagged dead — opus independently cleared the same surface for precision), so
    it was **downgraded to MEDIUM**. Fixed symmetrically with the Python extractor: nest
    *every* def's children under its own qual, and emit an **enclosing-function → nested**
    containment edge so a function-local def is live iff its enclosing function is reachable
    (Panel Q parity; subsumed by `_dedup_edges` when the nested def is also called directly).

  Weighted yield **8.0** (2 MEDIUM) — the convergence rate falls to **0.186**, the lowest of
  the series, and RRS rises to **80.2**. The clean streak **stays 0** (both findings are
  above LOW). sonnet's slot was stopped mid-review (API issues this session), so the panel
  ran at **2/3 diversity**; a full-diversity clean confirmation is still owed. The standing
  theme holds one more turn: after the *liveness* of the nested scope (Q), its *metrics* and
  its *tree-sitter twin* (R) — the symmetry audit is converging.

- **Panel S (opus · haiku)** — **the confirmation gate earned its keep again, catching a
  CRITICAL regression that Panel R *itself* introduced** (after Panel K caught Panel J's
  regression, this is the second time a fix's own side-effect was caught by the next panel —
  the strongest argument for the two-consecutive-clean rule):
  - **CRITICAL (opus)** — Panel R fixed the nested-def body leak by having each of the five
    `_direct_*` driver loops `continue` on a top-level statement that is itself a def. But a
    def's **header** expressions — decorator calls and their arguments
    (`@registry(make_validator())`), and a nested class's base/keyword expressions
    (`class Local(get_base())`) — are syntactically part of that skipped statement yet
    *execute in the enclosing function's scope at definition time*. Skipping the whole
    statement dropped them, and they aren't recovered elsewhere (`_decorator_edges` edges
    only the decorator's *name*; `_walk_scope` edges a base only via `_name_of`, which is
    `None` for a `Call`). So a symbol used only in such a header (e.g. `make_validator`,
    reachable from a live `main` through `build` → decorator-arg call) got **zero inbound
    edges and was flagged dead**. Pre-R this erred *safe* (the body-leak over-attributed the
    call to the enclosing fn, keeping the symbol live); Panel R flipped it to erring
    *unsafe* — a true false-dead, the cardinal sin. Fixed with `_def_header_refs`: when a
    driver loop hits a nested def, it walks the def's **header** (decorators + class
    bases/keywords) in the enclosing scope while still skipping the **body** — restoring the
    dropped enclosing-scope refs without re-introducing the body leak R closed. Verified:
    `make_validator`/`make_base` live again; `outer` still does *not* absorb a body-only
    callee; self-scan unchanged at 3 advisory candidates; 138 → 140 tests.
  - **haiku** — **clean** (`FINDINGS: none`) after 48 adversarial tests re-verifying the
    Panel R fixes, the cardinal invariant across Python/TS/JS, metric dedup, the containment
    edges, and cross-language parity.

  Weighted yield **40.0** (1 CRITICAL) — the streak **resets to 0** and RRS falls to **70.9**.
  The lesson, restated: an aggressive scope-narrowing fix (R) over-corrected by one syntactic
  level (statement vs. header-within-statement), the same shape as Panel J→K. The header/body
  split is now explicit. The first run of this panel stalled (both reviewers went silent ~36
  min on agent-backend flakiness and were reaped without delivering); it was relaunched with a
  time budget and completed normally — a process note, not a code signal.

- **Panel T (opus · haiku)** — **both models found a CRITICAL**, each the same nested-scope
  class (Panel Q) in one of the two remaining nesting hosts. Crucially, both were
  *pre-existing* false-deads (not regressions from R/S):
  - **CRITICAL (haiku)** — a Python def nested in a **control-flow block** (`if`/`elif`/
    `else`/`for`/`while`/`try`/`except`/`finally`/`with`/`match`) was never modeled as a
    node: `_def_node` and `_walk_scope` walked only a function's *direct* `body`, so a
    `def inner()` inside `if c:` got no node, yet `_walk_scope` still emitted edges from its
    qualname — a phantom source that can't reach, so a symbol used only there
    (`def process(): \n  if c: \n    def inner(): return helper()`) was flagged dead.
  - **CRITICAL (opus)** — the tree-sitter twin one host deeper: a def nested in a JS/TS
    **arrow function** (`const handler = () => { function worker(){…} }`, idiomatic and
    pervasive) was never modeled. The regular-def branch recurses into a def's body and
    threads the containment edge; the arrow-declarator branch created the node but **never
    recursed into the arrow body**, so nested defs (and their header expressions, the Panel S
    parity case) were lost.

  Rather than keep finding hosts one panel at a time, this was closed by a **systematic
  nesting-host audit**. A def can nest only in: a function body (Q), a class body (P), a
  control-flow block, or a function-expression/arrow — lambdas and comprehensions can't
  contain `def`s, so the set is finite and now fully covered. Fixes: a shared Python
  `_scope_defs` helper looks *through* control-flow blocks (which add no qual level) and is
  used in `_def_node`, `_walk_scope`, and the module loop; `_iter_funcs` (data-loop scan)
  likewise; the tree-sitter arrow branch now recurses into the arrow body and emits the
  `arrow → nested` containment edge. A parametrised **test matrix** pins every host in both
  extractors (140 → 148 tests). Dogfood after the fix: `find_stale` holds at the **3**
  documented advisory candidates, **0** holes — precision intact, no false-dead introduced.

  Weighted yield **80.0** (2 CRITICAL) — the heaviest panel yet, but the right kind of heavy:
  two independent models each confirming a real pre-existing false-dead, now closed
  *structurally* (by enumeration) rather than reactively. The streak **resets to 0**. If the
  enumeration is complete, the nested-scope class — the dominant defect class since Panel I —
  should at last be exhausted, and the next panels are its test.

- **Panel U (opus · haiku)** — **CLEAN, and the first real test of the systematic audit.**
  Both models returned `FINDINGS: none` after genuine probing, at full diversity. This panel
  carried an extra mandate — a **regression-test-quality audit**, added after two
  test-authoring slips in Panels R and S (an over-strict `CALLS`-vs-`REFERENCES` assertion;
  a non-reachable fixture). The worry it targets is the *silent* class: a test that passes
  without pinning its contract. Both models found the nesting regression suite **non-vacuous**;
  opus made it concrete by monkeypatching `_scope_defs` back to a pre-fix, direct-body-only
  variant (in `/tmp`, no repo edit) and observing the parametrised control-flow tests fail
  **5/5** — `helper` flagged dead, `process.inner` not modeled — then pass once restored. On
  the source side, neither could find a def host still unmodeled (the function/class/
  control-flow/arrow enumeration held), the body-leak (R) and header-leak (S) fixes still
  held, the containment edges introduced **no** metric inflation (`_dedup_edges` collapses the
  CALLS+REFERENCES pair, store `fan_in == 1`), and module-level `try: import` fallback +
  `TYPE_CHECKING` idioms behaved correctly. Dogfood on self: `find_stale` 3 advisory, 0 holes.

  Weighted yield **0.0** — the convergence rate falls to **0.000** and **RRS crosses 90.4**.
  Gates are green and RRS ≥ 90; the *only* remaining release blocker is the **second**
  consecutive clean panel. **Clean streak 1 of 2.** One more clean panel at full diversity
  clears the gate, at which point the maintainer can tag `1.0.0`.

- **Panel V (opus · haiku)** — **CLEAN, and the gate-clearing panel.** To keep a clean
  verdict from being a rubber-stamp on the same code Panel U saw (U changed no source), both
  reviewers were steered *away* from the now-closed nesting class and *toward* the
  less-trodden surfaces. They still came back `FINDINGS: none` after genuine probing:
  - **opus** — SQL/ORM/event resolvers (CTE/UNION/`INSERT…SELECT`/MERGE, `mapped_column` vs
    `relationship`/`ManyToManyField` exclusion, additive-only events); the full envelope
    contract (audited every `operations.py` `urgency=` assignment against the provenance
    ceiling; `scan`-RED requires an EXTRACTED-only `certain_live` path; `find_stale`
    0.6/INFERRED/needs_review; `ingest_trace` refuses on zero grounding; no
    `ok=True`-with-vacuous-result); persistence/migration; metric dedup
    (`LIVENESS_RELATIONS` excludes QUERIES/READS/WRITES/MAPS_TO, so resolver edges don't
    inflate fan_in); `get_matrix` N/N+1 bounds; a **2000-random-graph GraphBLAS-vs-pure-Python
    reachability fuzz with 0 mismatches**; and precision use-forms (walrus, nested
    comprehensions, f-string calls, string forward-refs, `__all__` re-exports,
    `@property`/`@staticmethod`).
  - **haiku** — per-language call graphs across the 12 languages, cross-language resolvers
    (Django routes as roots, SQL CTE not a phantom table), envelope-field validity on every
    operation, parallel-edge dedup, and robustness (unicode identifiers, malformed/empty
    files, the read-only invariant).
  - Two **non-defect sub-observations** were recorded, neither flagged as a finding: SQL
    `MERGE` labels its target READS rather than WRITES (a recall-only label gap, err-safe,
    and MERGE is uncommon), and `find_holes` sets `urgency=ORANGE` even on an empty hole list
    (cosmetic — the result is a valid `ok=True`, `count=0`, `[]`). These are noted for a
    future cleanup; they do not affect the cardinal invariant or the release decision.

  Weighted yield **0.0**. With **two consecutive full-diversity clean panels (U, V)**, all
  hard gates green, and **RRS 93.3** (coverage 86.4%), `scripts/readiness.py` returns
  **RELEASABLE**. Per the methodology the maintainer now tags/releases manually; the package
  version reads `1.0.0` only at that point (it stays `0.4.0` in-repo until the tag).

### 1.0.1 — field-fix batch (Panels W–X, 2026-06-24)

After 1.0.0 shipped, three issues were raised from real use on a Rust crate and fixed as
one batch (full reasoning in `CHANGELOG.md` / `docs/RELEASE_NOTES_v1.0.1.md`):

- **#8 (precision, the cardinal class):** idiomatic Rust unit tests in `#[cfg(test)] mod
  tests { … }` have free-form names, so the `test*`/`Benchmark*`/`Example*` convention never
  fired — every `#[test]` fn and the helpers it reached were reported stale, flooding
  `find_stale`. Fixed by seeding the `test` role from the `#[test]`/`#[tokio::test]`
  attribute and the `#[cfg(test)]` module gate.
- **#7 (silent failure + dep hygiene):** a grammar that couldn't load collapsed into a
  silent empty graph; now it emits a `RuntimeWarning` naming the languages and skipped count,
  and the tree-sitter deps are bounded (`<1` / `<2`).
- **#9 (UX):** `impact_of` on a bare homonym now lists candidates and accepts a qualified
  `Type.method` / full `path::qual` id to scope.

- **Panel W (opus · haiku)** — **the confirmation panel; converged on one err-safe defect in
  the #8 fix.** Both models independently found that the attribute detector matched a raw
  `"test"` substring (`any("test" in a …)`), so `#[cfg(feature="testing")]` and
  `#[doc="…test…"]` wrongly marked production code as a test root — *hiding* genuinely-dead
  code. It errs in the **safe** direction (over-mark keeps a symbol live; it can never flag
  live code dead), so it was **not** a cardinal-invariant violation and not release-blocking,
  but it was real. #7 and #9 were verified correct (including #9 dotted-name resolution and
  `Foo.bar` vs `XFoo.bar` false-resolution probes). **Fix:** `_is_rust_test_attr` matches the
  attribute **path** — `test`, any `*::test`, bare `cfg(test)`/`cfg(all(test,…))` with quoted
  string values stripped — plus a both-directions regression test.

- **Panel X (opus · haiku)** — **CLEAN re-confirmation of the W fix.** Both `FINDINGS: none`.
  The matcher was exercised in **both directions**: the false-positive set is gone
  (`#[cfg(feature="testing")]`, `#[doc]`, `#[allow]`, `#[derive]`, a feature literally named
  `test`) and no false-negative was introduced (`#[test]`, `#[ test ]`, `#[tokio::test]` and
  other `*::test`, `#[cfg(test)]`, `#[cfg(all(test, feature="x"))]`, inner attrs, free-form
  test-mod names). Attribute-bleed to the next item resets correctly; the core #8 liveness
  holds (a test helper reached by no test, and unused production code, still flag). #7/#9
  re-confirmed; dogfood 3 advisory / 0 holes. One err-safe note — third-party runner macros
  (`#[rstest]`, `#[test_case]`) under-mark, since the macro set is unenumerable and matching
  them would re-open the W over-match — is **documented** in `LIMITATIONS.md`, not a finding.
  Per the maintainer's call, **1.0.1 ships on this clean dual-model confirmation** (the
  agreed fix → confirmation-panel process), rather than re-establishing the full streak-2.

- **Polyglot test detection (panels Y–DD)** — the maintainer asked whether the #8
  test-detection gap existed in other languages. It did: #8's root cause (only the
  `test*`/`Test*` name convention seeds the `test` role) flagged live tests dead in every
  language whose idiomatic tests aren't name-convention. A generalization was folded into
  1.0.1 and driven to closure across six panels:
  - **Y** — the new work verified clean both directions (annotation allowlist exact;
    call-based helpers live; dead helpers flagged), except one err-safe MEDIUM both models
    converged on: `_is_test_file` matched `testing/`/`specs/` dirs — plausibly *production*
    (Go `testing`, OpenAPI `specs`) — rooting their module calls and hiding dead code. Fixed
    by trimming the dir set to `test`/`tests`/`spec`/`__tests__`.
  - **Z** — opus caught a CARDINAL haiku missed: a pytest `class TestWidget:` whose methods
    are all `test_*` was flagged dead (recognized test methods become `NodeKind.TEST`, so the
    callback path only rescued classes with a non-test override like `setUp`). Fixed with a
    Python `_seed_test_classes`.
  - **AA** — two CARDINAL siblings (same shape, one level removed): a class inheriting all its
    tests from a custom base (JUnit abstract-base + thin-subclass; pytest inherited tests), and
    the outer of a nested test class. Fixed by growing the seed set transitively (enclosing +
    inheritance) in both extractors.
  - **BB** — opus broke the AA fix: the two growth axes ran sequentially, not to a *combined*
    fixed point, so a nested+inherited class left its outer flagged. Fixed by co-iterating both
    axes in one `while changed` loop. Plus an err-safe MEDIUM: tree-sitter resolved bases by
    name across all languages → fixed by keying `(lang, name)`.
  - **CC** — opus: the Python and tree-sitter `is_test_file` had **drifted** (Python checked
    only the filename), so a shared test base in `tests/conftest.py` was unrecognized and its
    subclass flagged (CARDINAL). Fixed by a single shared directory-aware `is_test_file`
    (`_testfile.py`) used by both. Plus haiku's err-safe `#[cfg(not(test))]` over-match (the
    `test` token sat inside `not(...)`), fixed by dropping `not(...)` first.
  - **DD** — **CLEAN** on both models, with an explicit convergence verdict: the test-liveness
    class is **closed** for 1.0.1. The one residual (a test base in a *non-test* directory
    subclassed by an own-method-less test) is realistic-but-rare, precision-safe, and
    documented — closing it generically would regress the Y/W over-match guard.
  - **EE** — **CLEAN** (the second consecutive clean panel → **streak 2, gate met**). A fresh
    independent confirmation: opus ran 11 cross-language scenarios it hadn't used, haiku swept
    the full matrix; both `FINDINGS: none`. The polyglot generalization is certified to the
    same 2-consecutive-clean bar as the 1.0.0 release.

  Lesson reinforced: the dominant late-stage defect is a **symmetry/drift gap** — a guard or
  heuristic present in one extractor but not its sibling (here, `is_test_file`). The fix was
  not just to patch but to **unify** the heuristic so the two can't diverge again.

## Release status (2026-06-24)

**1.0.0 is released** (tag `v1.0.0`): gates green, RRS **93.3 / 100**, clean streak 2 at
full diversity (U + V). The dominant historical defect class — "a live symbol used in a way
not modeled → flagged dead," the Python↔tree-sitter nesting-scope asymmetry — is closed
across all four nesting hosts (function / class / control-flow / arrow).

**1.0.1 (field fixes #7/#8/#9 + polyglot test detection)** is prepared: gates green
(**165 tests** · ruff · mypy), in-repo version `1.0.1`, `CHANGELOG.md` +
`docs/RELEASE_NOTES_v1.0.1.md` written. The original three field fixes were confirmed by
W (one err-safe over-match, corrected) → **X clean**. The maintainer then asked whether the
#8 test-detection gap existed in other languages; it did (Java/C#/PHP annotations, JS/TS/Ruby
call-based, Python test classes), so a **polyglot generalization** was folded into 1.0.1.
Panels Y–EE drove it to closure: Y (err-safe dir over-reach) → Z/AA/BB/CC (four successive
CARDINAL test-class-liveness gaps: direct → inherited/nested → combined fixed point →
Python/tree-sitter `is_test_file` asymmetry, each fixed + regression-pinned) → **DD clean
with an explicit verdict that the class is CLOSED → EE clean (second consecutive) → streak 2,
RRS confidence 0.86 → RELEASABLE.** A unified directory-aware `is_test_file` (`_testfile.py`)
now backs both extractors so they can't drift. **1.0.1 is released** (maintainer tag).

**1.0.2 (export-rooting + test call-receivers)** is prepared: gates green (**169 tests** ·
ruff · mypy), in-repo version `1.0.2`, `CHANGELOG.md` + `docs/RELEASE_NOTES_v1.0.2.md`
written. After 1.0.1 shipped, sonnet's API came back and was restored to the panel (full
diversity = opus + sonnet + haiku again). The first full-3-model panel (FF) had opus + haiku
clean but **fresh sonnet caught two CARDINAL false-deads the pair had missed across the whole
1.0.1 cycle** (JS/TS `export default <ident>` — pre-existing; a test's class-as-call-receiver
in an RSpec/Jest block — a 1.0.1 regression). GG surfaced a third (CJS `module.exports` / TS
`export =`). Fixed as a batch, then **HH clean (export-rooting class enumerated CLOSED, 19
forms) → II clean (second consecutive) → streak 2, RRS confidence 0.86 → RELEASABLE.** The
export-rooting class is now comprehensive (`export {}`, `export default <ident>`, inline
`export default class/fn`, `export =`, `module.exports`/`exports.*`, object exports).
**Next step is the maintainer's manual `v1.0.2` tag.** This is the diversity-is-the-signal
lesson made concrete: a returning reviewer with no memory of the recent cycle saw what the
incumbent pair had gone blind to. **1.0.2 is released** (maintainer tag).

**1.0.3 (offline-by-default grammars, issue #12)** is prepared: gates green (**172 tests** ·
ruff · mypy), in-repo version `1.0.3`, `CHANGELOG.md` + `docs/RELEASE_NOTES_v1.0.3.md` written.
`tree-sitter-language-pack` switched at v1.0.0 from bundling grammars in the wheel to a
download-on-first-use model (breaks offline/CI/air-gapped); the 1.0.1 `<2` bound didn't fix
it. Chose Option 1: pin `[treesitter]` to the bundled line (`>=0.7,<1.0`) for an offline,
self-contained default; add an opt-in `[treesitter-download]` extra (1.x); an adaptive
`_load_grammar` that gets the grammar the easiest available way (bundled, else download-and-
retry when the pack supports it, else #7 warn+skip); and a `stitchgraph doctor`/`--strict`
self-check. **Panel JJ** (full 3-model) clean: the loader swap is byte-identical, all adaptive
paths correct, offline operation verified in a throwaway venv with the network off; all 12
grammars are MIT (so depending on the bundled wheels carries no redistribution burden).
Readiness reads RELEASABLE, though note the streak counts II (1.0.2 code) + JJ (1.0.3 code) —
JJ is the one panel that reviewed the #12 change, and it's a behaviour-preserving packaging
fix. **Next step is the maintainer's manual `v1.0.3` tag** (and #12 can then be closed).

**1.0.4 (confidence honesty for receiver calls + structural findings, issues #10/#11/#15)**
is prepared: gates green (**177 tests** · ruff · mypy clean on **both** the dev pack 1.10.6
and the pinned bundled 0.13.0 strict stub), in-repo version `1.0.4`, `CHANGELOG.md` +
`docs/RELEASE_NOTES_v1.0.4.md` written. #10: a receiver-based call (`obj.save()`) resolving
to a *single* same-named project symbol is now `INFERRED`, not `EXTRACTED` — without type
inference the receiver type is unknown, so it may be a homonym on a stdlib/third-party class;
**weight stays 1.0 so reachability/find_stale are unchanged (cardinal-safe)** — only the
asserted confidence drops (tree-sitter: every receiver call; Python `ast`: only the
unknown-receiver fallback — scope-aware `self.`/local-typed calls stay `EXTRACTED`). #11:
`scan` cycles/god_objects now propagate participating-edge provenance — each carries
`confidence`/`needs_review`, reports confident-only degree, and is capped 🟢 when dominated by
AMBIGUOUS/INFERRED edges (resolution-artifact "look closer" issues sink in the ranking). #15:
corrected the `LIMITATIONS.md` `--precise` wording (it is *additive* — adds a confident
go-to-definition edge, never prunes the AMBIGUOUS siblings). **Panel KK** (full 3-model): opus
+ haiku clean; opus's non-blocking note became the Python-extractor consistency fix (MEDIUM)
and sonnet caught a LOW C# qualified-constructor regression — both fixed + pinned, so KK is
not a clean panel and the streak resets. **Panel LL** is the fresh full-3-model confirmation
on the final tree; the gate needs two consecutive clean full-diversity panels. **The
maintainer's manual `v1.0.4` tag is the last step** (then #10/#11/#15 can be closed).

Deferred non-blockers: the `[treesitter-download]` (1.x) line still needs network on first use
(by design — that's the opt-in); obscure JS/CJS export indirections (`module.exports` inside a function
body, `export *`, `Object.assign(module.exports,…)`, namespace-member export — all err-safe,
documented); a test base in a *non-test* directory subclassed by an own-method-less test;
third-party Rust runner macros (`#[rstest]`/`#[test_case]`); SQL MERGE WRITES label;
`find_holes` empty-list urgency; the LSP backend and variable-granularity data flow.

## Standing themes

- Convergence is non-monotonic and never reaches zero — measure residual risk.
- Late-stage defects are symmetry gaps: a guard present in one language extractor
  or resolver but not its siblings. Audit by a path×behaviour matrix.
- Blind spots: tree-sitter / graphblas / sqlglot / jedi / mcp surfaces are gated
  by optional deps; a panel is blind to them unless the extras are installed.
- Model diversity is itself a defect-finding signal, not just a confidence multiplier.
  Two panels (W–EE) ran opus+haiku because sonnet was down and went 1.0.1-clean; the
  moment sonnet returned (FF) it found two cardinals — one a regression those panels had
  just shipped — that the incumbent pair had gone collectively blind to. A reviewer with
  no memory of the recent cycle is the cheapest way to break a shared blind spot; keep the
  model set diverse and rotate a fresh perspective in after any long single-pairing run.

_Maintenance: append a trajectory row + a bullet per panel; keep the TL;DR in
sync with `release_readiness.json`._
