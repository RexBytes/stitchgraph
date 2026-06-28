# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 2.1.17: R104–R105. **2.1.18: R106–R118** (full diversity opus/sonnet/haiku) on JS/TS object-literal function-member bodies (#48) — the campaign's deepest single surface: 13 rounds, 10 distinct cardinal classes, one over-reaching fix caught + reverted (R116), closing R117✓ R118✓ |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ · mutation (`_object_members`/`_obj_key_name`/`_unwrap_ts_value` all-killed; the 2 justified survivors are the class-member INHERITS edge + the redundant identifier-key branch) ✅ · oracles 27 ✅ · no-open-defects ✅ |
| Tests | 473 passing (full extras) |
| Coverage | ~93% |
| Convergence | 2.1.16: R102✓ R103✓. 2.1.17: R104✓ R105✓. 2.1.18: JS/TS object-literal members → **R117✓ R118✓ full-diversity (streak 2, gate met, readiness RELEASABLE)** — 10 cardinal classes fixed across R106–R116; R116 caught + reverted a fix that escalated a pre-existing recall gap into a cardinal |
| Dogfood (self) | find_stale advisory-only (no false-dead) · holes 0 |
| Verdict | **1.0.0–2.1.17 RELEASED/releasable** (maintainer tags). **2.1.18** (JS/TS object-literal function-member bodies extracted via the new `_object_members` pass — method shorthand, arrow/function/generator/class-valued members, nested objects, computed/string keys, TS-wrapper unwrap, module + function scope) **RELEASABLE** — R117+R118 full-diversity clean; awaiting the maintainer's manual `v2.1.18` tag. Objects reached only via an EXPRESSION shape (IIFE/ternary/`||`/`Object.freeze`/array/sequence/chained-assignment) and `const X = class {…}` are PRE-EXISTING (byte-identical on the 5cb47bc baseline), documented, and deferred to a focused next release. The diverse panel earned its keep repeatedly: sonnet found the fn-scoped-class and chained-assignment cardinals; opus found the member-value-wrapper, generator, and the R116 over-reach regression. |

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

| LL | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN — first clean panel of the 1.0.4 batch (streak 1 of 2), fresh full-3-model confirmation on the final tree after the KK fixes.** All three `FINDINGS: none`. opus gave an end-to-end **cardinal proof** that liveness is provenance-agnostic — a symbol reached *only* via a #10 INFERRED receiver edge is not flagged dead while a genuinely-unused sibling is — and verified the constructor call-fields (`"constructor"`/`"type"`) bind **exclusively** to constructor node types across the `LangSpec` map (no real method call wrongly kept EXTRACTED), plus `--precise` is genuinely additive (`_dedup_edges` only collapses same `(src,relation,dst_id)`; AMBIGUOUS siblings preserved). sonnet re-ran the per-language constructor matrix (C#/Java/JS/TS/C++/Rust/Ruby/PHP) and adversarial `_resolve_member` defeat attempts (chained calls, `self.x.m()`, annotated params, reassigned locals) — all correct. haiku: full suite 177, dogfood 3 advisory / 0 holes, scan on cycle/empty no crash. |

| MM | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN — second consecutive clean panel; the 1.0.4 gate is met (streak 2, RRS confidence 0.86 → RELEASABLE).** All three `FINDINGS: none`, fresh independent sweep. opus proved end-to-end that the *only* consumers of provenance are reporting/ranking (`scan` RED/ORANGE ceiling, #11 capping, `trace_path` confidence) — none feed `reachable_from`/`find_stale`, so #10's INFERRED relabel cannot drop liveness; `_CONSTRUCTOR_FIELDS` maps exclusively to constructor callee fields. sonnet ran 12 #11 boundary scenarios (mixed EXTRACTED/AMBIGUOUS cycles straddling `frac<0.5`, god-object `c_in`/`c_out` boundaries, self-loop, god-object-in-cycle, REFERENCES-only no-cycle) — no genuine EXTRACTED finding ever wrongly capped GREEN — plus 7 downstream-op checks (`trace_path`/`impact_of`/`find_stale`/`orient`/stub-ceiling all provenance-agnostic for liveness) and 9 cross-extractor parity checks (the KK asymmetry stays closed). haiku: full suite 177, dogfood 3 advisory / 0 holes, the four spec repros, empty/single-file no crash. No false-dead, no metric inflation, no envelope-contract violation, no crash. |

| — | _1.0.4 releasable; maintainer ran v1.0.4 against a real repo and filed #18/#19 (CLI/UX papercuts)._ | | | _#18 risk ignores indexed root; #19 no --version + design §9 advertises a path? the CLI rejects. 1.0.5 below._ |
| NN–TT | opus · sonnet · haiku | 1 MEDIUM · several LOW (all fixed) | — | **1.0.5 (#18/#19) iterative hardening.** Code correctness + the cardinal invariant were clean in *every* round (opus/haiku found no code defects across all of NN–VV); the chain of findings was a sequence of **LOW/MEDIUM cosmetic/envelope nits in one area — risk/report empty-result rendering — plus doc/version drift**, each a sibling of the last, surfaced mostly by the sonnet slot: NN report-cwd (#18 sibling) + phantom `structure_smells()` in §9; OO `type_at` in §9; PP `relations?` in §9 + stale `__version__`; QQ/RR risk "no git history" `refuse(result={})` → vacuous `ok=True` → blank report section (MEDIUM); SS/TT the sibling where churn is non-empty but all files have zero centrality → still `ok=True` empty → blank section. Fixes: report/risk default to the indexed root (#18); `--version` + `no_args_is_help`; `__version__` from `importlib.metadata`; §9 scrubbed to registered ops only; risk **refuses** when it can't run (`(skipped)`) and returns `ok=True` structured-empty when it ran-but-found-nothing → report renders `- (no risk hotspots…)`. The whole risk/report empty class is now closed (refuse→skipped / ran-empty→(none) / populated→hotspots), each path regression-pinned. |
| UU | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN** on the final tree (e5785fa). Full envelope-contract + cardinal + crash + risk/report empty-state sweep; class confirmed closed. The dogfood `scan` RED on `build_app._root` is a *genuine* advisory (an intentionally-empty Typer `@app.callback` body), confirmed not a false positive. Streak 1 of 2. |
| VV | opus · sonnet · haiku | _none_ | **0.0** | **CLEAN — second consecutive clean full-3-model panel; the 1.0.5 gate is met (streak 2, RELEASABLE).** Independent sweep: envelope contract across all 15 ops, MCP end-to-end (risk path-omitted schema valid), multi-language cardinal repos (metaclass / class-body / control-flow nested def / arrow-in-arrow / forward-ref annotation / cross-language route+fetch — none false-dead), adversarial risk/report (worktree / submodule / spaces / relative-db / deleted-root) all clean-refuse-or-(none), never blank, never traceback. Dogfood 3 advisory / 0 holes; 184 tests. |

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

## 1.0.6 final campaign (R33–R39) — three-layer gate

Full-diversity rounds (opus×2 · sonnet×2 · haiku×2) under a strengthened gate: a round is
"clean" only when **all three** layers pass — adversarial panel + oracle suite
(`tests/oracles/`) + mutation meta-oracle (`scripts/mutate.py`). Ship on **2 consecutive
3-layer-clean rounds**.

- **R33** (5): Ruby/PHP module-script rooting (CARD), replace_file runtime-role erase +
  has_runtime inflation (CARD), empty-ignore glob crash, impact_of inflation, scope-prefix bleed.
- **R34** (3): trace_path provenance inflation, cross-language bare-name widening (incremental),
  runtime `_by_suffix` boundary inflation. (+ broken-config-test fix; mutation green-baseline guard.)
- **R35** (4): Go package-var rooting (CARD), Express method-ref handler (CARD), coverage
  bool-as-int line, cross-root coverage suffix.
- **R36** (1): C++ TU static-init liveness (CARD, reachability fixpoint). + envelope non-finite clamp.
- **R37** (2): module/symbol id-collision rooting (CARD), cross-file re-export on incremental
  replace_file (CARD, `exported_ids` exact-convergence). + `_plain` non-finite drop.
- **R38** (0): **first 3-layer-clean round.** One finding invalid (SUBMITS_TO premise/direction);
  one latent non-blocking meta/_plain consistency item closed.
- **R39** (0): **second consecutive 3-layer-clean round — gate met.** All 6 reviewers clean.
  RRS 93.3/100, clean streak 2.

Method note: late findings were almost all *symmetry siblings* of an already-known column
(rooting / provenance-demotion / boundary-guard / incremental-convergence). Closing each whole
column with an owning oracle or matrix test — not the single instance — is what drove the
panel cadence to zero. Two `replace_file` divergences (find_holes-count and fan_in on
incremental delete) are documented in LIMITATIONS.md as library-only (the shipped CLI/MCP/watch
full-reindex). A round-39 reviewer ran a destructive `git reset` mid-run; future panel briefs
forbid any git state mutation.

## 1.0.7 — multi-repo / multi-language false-positive hunt (R40–R42)

Post-1.0.6, stitchgraph was run against ~47 real-world projects across 9 languages —
deliberately including code built to break parsers (IOCCC) and large/messy corpora (Linux
kernel core, WordPress, Magento, PrestaShop, symfony, flake8, flask, NestJS, TypeORM) —
ground-truthing `find_stale` against actual liveness. **Robustness: 0 crashes across all
corpora.** The hunt surfaced 9 cardinal-class entry-point/liveness gaps, each fixed
root-cause + owned by a regression test (see CHANGELOG 1.0.7): setup.cfg entry points,
src-layout absolute imports (incl. PEP 420 namespace packages), exported-class inherited
methods, Java/C# annotations, JS/TS decorators, transitive/self-named external-base
callbacks, Ruby implicit hooks, C/C++ `EXPORT_SYMBOL`, JS/TS member-assigned functions;
plus dependency-dir skipping and a bodyless-struct phantom fix.

Then a fix-panel campaign (opus/sonnet/haiku) over the session diff with the three-layer
gate. Nine review rounds (R40–R48); the panels caught and fixed 5 defects the new features
themselves introduced — all **over-rooting/recall**, never a shipped cardinal false-dead. The
release gate (`scripts/readiness.py`) requires **two consecutive FULL-diversity (all three
models) clean panels** — earlier 2-model clean rounds (R42/R44/R45) confirmed correctness but
did not satisfy the diversity bar, so the campaign continued to full-diversity R47–R48:

| Round | Models | Result | Findings |
|---|---|---|---|
| R40 | 3 | ✗ | R40A script-class over-root (HIGH); R40B comment dropped JS/TS decorator; R40C member-assign-in-dead-fn rooted |
| R41 | 3 | ✗ | **R41A** comment-skip missed Rust `line_comment`/`block_comment` → `#[test]` dropped (cardinal); + 2 documented cardinal-safe |
| R42 | 2 | ✓ | clean (not full-diversity) |
| R43 | 3 | ✗ | **R42A** namespace-package src-layout false-dead (cardinal) |
| R44 | 2 | ✓ | clean — R42A broadening verified cardinal-safe (not full-diversity) |
| R45 | 2 | ✓ | clean (not full-diversity) |
| R46 | 3 | ✗ | **R46A** member-assigned CLASS methods flagged dead (inverse-cardinal); fuzz clean |
| R47 | 3 | ✓ | full-diversity clean — R46A verified complete; 8 shapes; 15 fuzz |
| R48 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE** (14 real-world apps; all-features-combined + 1000-file fuzz) |

Every fix only *adds* roots (cardinal-safe). The src-layout incremental defect class is now
owned by the differential oracle (a new `src/`-layout incremental==full fixture); the
scalability ceiling (in-memory whole-graph reindex; Magento-scale exceeds ~12 GB) is
documented in LIMITATIONS as the next architectural item (streaming/constant-memory indexer).

## 2.0.0 — constant-memory streaming indexer (R49–R52)

The v2.0.0 change: `reindex(streaming=...)` streams the graph to SQLite instead of building it
all in Python first. Profiling found the real hog wasn't parse trees but the **edge list** —
name-based ambiguous fan-out yields ~15.5M edges for 30k nodes on a single Magento module
(~4 GB). The streaming path drops each file's AST/parse-tree + source after pass 1 and
deduplicates edges per-source on the fly, writing only the survivors in committed batches:
**reindex peak 3,183 MB → 269 MB (~12×) on the Magento Framework core, byte-identical output**
(3,926,345 edges / 30,412 nodes verified row-for-row). Made the default for large on-disk
repos (AUTO ≥ 2,000 files). The non-negotiable invariant is `streaming == full` byte-for-byte;
the gate is a new differential oracle (`tests/oracles/test_streaming_differential.py`,
polyglot incl. PHP/Rust/C++/TS + heavy-fan-out + cross-group + `precise=True` jedi cases).

The mutation meta-oracle gained a `--only <names>` scope filter so the streaming-critical
functions (`_dedup_edges`, `_auto_stream`) are pinned by a fast, targeted kill-signal
(**20/20 killed**). The streaming differential oracle compares every load-bearing edge field,
including `weight`, `provenance`, and the internal `name_based` flag.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R49 | 3 | ✓ | full-diversity clean — opus fuzzed _DefInfo/precompute + 59 non-adjacent same-src runs + 260 polyglot trials; sonnet 15 scripts; haiku robustness. (An earlier R49 attempt was discarded — panels had read `operations.py` mid-mutation-run; re-run clean.) |
| R50 | 3 | ✗ | **opus** found a real byte-identity break: `reindex(precise=True, streaming=True)` diverged on `name_based` (jedi's precise arm + the extractor's name-based arm to the same target land in different sink groups → store ORs while in-memory `_dedup_edges` kept the precise survivor's flag). haiku + sonnet (15 min / 80 tools) CLEAN — they didn't exercise the jedi path. **Fix:** `_dedup_edges` now ORs `name_based` onto the survivor (the store's R23A rule); pure-precise groups keep False (R22A). Pinned by a `precise=True` jedi oracle case + unit tests. |
| R51 | 3 | ✓ | full-diversity clean — verified the R50 fix under precise=True across jedi-outside-candidates, multi-homonyms, lower-weight precise, REFERENCES-vs-CALLS, declared-type, override dispatch, polyglot, full `src/` tree. |
| R52 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. AUTO threshold boundary, store residue (stream→full→stream == fresh full), CLI/MCP tri-state round-trip, resolver-heavy + ORM/route, docs-accuracy audit. |

R50 is the headline lesson: **model diversity finds what breadth misses.** The slowest, most
exhaustive reviewer (sonnet, 80 tool calls) and the fastest (haiku) both returned CLEAN on the
same code where opus constructed the one input — `precise=True` with a homonym — that broke
byte-identity. A bug invisible to two independent thorough reviews fell to a third
perspective.

## 2.0.1 — PHP array-callable recognition (R53–R57)

Dogfooding v2.0.0's streaming indexer on the **Magento Framework** (3,968 PHP files) surfaced a
cardinal-class false-positive: methods invoked via PHP's `[$this, 'method']` callable-array
idiom (`usort`/`uasort`/`preg_replace_callback` comparators) were flagged dead, because the
method name is a *string*, not a syntactic call. The fix: the tree-sitter PHP extractor emits a
REFERENCES edge to the method named by a 2-element array callable. Cardinal-safe (only project
symbols resolve; over-rooting masks dead code, never a false-dead), byte-identity preserved. On
the Framework, PHP dead-candidates dropped 39 → 30; genuinely-unused private methods still flag.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R53 | 3 | ✓ | full-diversity clean — append-only/cardinal-safe verified, PHP-only gating, byte-identity across all edge fields, edge cases (keyed/3-elem/non-string/nested) over-root-only. |
| R54 | 3 | ✗ | code unanimously clean at real-Magento scale (opus: `find_stale` only shrank 8→6; sonnet: cross-file inheritance, typo→no hole). **haiku** found a doc inaccuracy: CHANGELOG/RELEASE_NOTES still claimed a `'Class::method'` string branch that the mutation-cleanup had dropped. |
| R55 | 3 | ✗ | code clean again; the R54 doc fix was **incomplete** (the CHANGELOG *intro* + two in-code comments still claimed string-callable handling). **Fix:** grep-verified purge of every stale claim. |
| R56 | 3 | ✓ | full-diversity clean — closure confirmed: no remaining "string callable handled" claim anywhere; byte-identity re-verified. |
| R57 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. opus fresh 41-file PHP repo (every callable position, nested, multibyte, `precise=True`); surfaced two PRE-EXISTING recall gaps (bare-string function callable; module-scope array callable) — not regressions, now documented in LIMITATIONS. |

The 2.0.1 lesson mirrors 1.0.x: **the doc-accuracy invariant catches incomplete fixes.** A
partial doc correction (R54→R55) was caught twice before a comprehensive grep-driven purge
closed it — and the final round (R57) turned up two adjacent recall gaps to document rather
than silently ship. The cardinal fix itself was clean from R53; the iterations were all about
keeping the docs honest about exactly what is and isn't covered.

## 2.1.0 — constant-memory queries + SQL-prose precision (R58–R60)

Dogfooding v2.0.1's streaming indexer across a **multi-repo Python hunt** (Django, Salt,
Ansible, CPython stdlib, Home Assistant) found that *indexing* outran *querying*: Home Assistant
indexed at ~4 GB (6,728 files / ~16M edges) but `find_stale` then **OOM'd** — every reachability
sweep went through `Store.resolved_edges()` → `SELECT * … fetchall()`, building all 16M `Edge`
objects at once. v2.1.0 streams a lean `(src, relation, dst_id, weight)` tuple view
(`Store.iter_resolved()`); `algebra._Adjacency` and the `reach.py` sweeps build from it. Result
byte-identical (the GraphBLAS==pure-Python oracle proves it); 6M-edge `find_stale` peak dropped
to ~840 MB, so 16M now queries in ~2 GB. Plus a precision fix: the SQL resolver was treating
prose docstrings (`"Create a list…"`) as SQL — now a structural regex + an English function-word
`_STOP_TABLES` drop. And the Salt-loader string-name dynamic-dispatch blind spot (3,907
flagged) is documented.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R58 | 3 | ✗ | reachability streaming verified byte-identical (opus differential, all sweeps). **opus** LOW: `iter_resolved` bypassed `_row_to_edge`'s corrupt-row drop → a corrupt relation could reach `best_path(relations=None)`. **haiku** MEDIUM: the SQL structural regex still let clause-shaped prose (`"Select items from the list"`) mint a phantom `db::the`. Fixes: skip invalid relations + coerce non-finite weight; `_STOP_TABLES` drops function-word table names. (A first signal-gate attempt was reverted — it rejected real minimal `SELECT x FROM y`.) |
| R59 | 3 | ✓ | full-diversity clean — fixes confirmed byte-identical on real indexes; `_STOP_TABLES` drops no plausible real table. Tidied 2 NITs (stale test docstring, redundant import). |
| R60 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. opus full differential (GraphBLAS + pure-Python) + doc-accuracy audit (corrected a stale v2.0.0 "streaming opt-in" LIMITATIONS line to AUTO-default). |

The 2.1.0 lesson: **a memory-shape change is only as safe as its differential oracle, and the
meta-oracle must run the path that actually executes.** The streaming rewrite was correct from
the start (oracles green), but the first mutation run showed 16/16 survivors — because with
GraphBLAS installed the pure-Python sweeps it mutated are *shadowed*; pinning them required a
test that forces the core-only path. And the SQL fix needed two attempts (signal-gate →
stop-words) to cut prose phantoms *without* dropping real minimal queries.

## 2.1.1 — Ruby operator-method cardinal fix (R61–R63)

A **Rust / Go / Ruby dogfood hunt** (serde, clap, gorm, cobra, gin, logrus, grape) chasing the
higher-yield finding the Python hunt didn't produce: a *new* cardinal false-positive. Go (gin:
`find_stale=0`) and Rust (serde: library fully live, only test-suite/trybuild fixtures flag)
came back clean — confirming their rooting is mature. **Ruby (grape) produced the bug.** An
asymmetry pointed straight at it: `ValueArray#initialize` was flagged dead while the
structurally-identical `ValueHash#initialize` was live (the latter survived only by a
*coincidental* mis-resolved edge). Root cause: Ruby operator methods (`def []`, `def []=`,
`def <=>`, …) have a name node of tree-sitter type `operator` that the extractor didn't
recognize, so the whole method was dropped — making it invisible AND false-flagging anything
used only inside its body (`ValueArray.new(value)` lives inside `def []=`). Fix: capture the
`operator` name node + root operator methods as `callback` (syntax-invoked, the Ruby analogue
of the C++ special-member pass). grape: `ValueArray#initialize` now live, `[]`/`[]=` captured
(+18 nodes), `find_stale` 23→19.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R61 | — | discovery | dogfood (not a formal panel): grape `ValueArray#initialize` false-dead found + root-caused to the dropped `operator` name node. |
| R62 | 3 | ✓ | full-diversity clean — opus: shared `operator` capture never reaches a node/edge-minting path in C++/C# (no cross-language regression); sonnet: 24 operator forms + bodies' callees live, dead normals still flag, polyglot full==streaming; haiku: version/docs/no-bogus-nodes. |
| R63 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Fresh angles: backtick method, unary `-@`/`+@`, `def self.[]`, module-level, nested modules, operator-calls-operator, cross-file homonyms, Ruby+C++ byte-identical streaming. |

The 2.1.1 lesson: **diversity of *targets* finds what diversity of *reviewers* can't.** The
Python hunt (mature rooting) found a scalability ceiling; pointing the same tool at a
less-battle-tested language (Ruby) immediately surfaced a real cardinal bug. The
`ValueArray`/`ValueHash` asymmetry was the tell — when two structurally-identical things get
opposite verdicts, one of them is a bug.

## 2.1.2 — C# custom-attribute cardinal fix (R64–R68)

Continuing the hunt into Java and C# (jackson-core, mockito, okhttp, serilog). Most findings
were the **documented external-framework-annotation limitation** — mockito's `@Advice.*`
(ByteBuddy bytecode instrumentation), okhttp's `@ToJson`/`@FromJson` (Moshi reflection): methods
invoked by annotations outside the curated set, now cited in LIMITATIONS (pin via
`[entry_points]`). serilog surfaced a clean extraction bug: `NoEnumerationAttribute`, defined and
applied as `[NoEnumeration]`, was flagged dead — **C# omits the `Attribute` suffix in usage**, so
the bare reference never resolved. Fix: an attribute usage also emits the suffixed reference
(`Foo` → `FooAttribute`).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R64 | — | discovery | dogfood: serilog `NoEnumerationAttribute` false-dead, root-caused to the omitted-suffix reference. |
| R65 | 3 | ✓ | full-diversity clean on the initial fix (`_direct_refs`) — every attribute form (`[Foo(1)]`/`[A.B.Foo]`/`[assembly:]`/generics) safe, no cross-language regression. |
| R66 | 3 | ✗ | opus + haiku clean, but **sonnet** found the fix was incomplete: C# `enum`/`delegate` aren't in `spec.defs`, so their attributes are walked by `_module_uses` (not `_direct_refs`) — `[MyFlag]` on an enum left `MyFlagAttribute` dead. Fix: mirror the branch in `_module_uses`. |
| R67 | 3 | ✓ | full-diversity clean — opus's coverage table + sonnet's exhaustive 18-target sweep confirm every C# attribute target is now covered, no double-count. |
| R68 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Fresh angles: nested enum-in-class routing, local-function attrs, `using`-alias, no-live-root (correctly dead), C#+PHP polyglot streaming. |

The 2.1.2 lesson reprises 2.0.1's: **the same reviewer who is slowest is often the one who finds
the incomplete fix.** sonnet drove both the R66 enum/delegate gap and (in 2.0.1) the
partial-doc-fix catches — exhaustive enumeration of cases is its edge, exactly where a "looks
done" fix hides one uncovered branch. Also: the curated framework-annotation allowlist is a
deliberate, documented boundary (ByteBuddy/Moshi out of scope), distinct from the genuine
extraction bug (the attribute *class* reference) that was fixed.

## 2.1.3 — Rust FFI/linker-export cardinal fix, doc-driven (R69–R72)

A methodological shift: instead of waiting for a repo to surface a gap, **read the language
reference**, which enumerates the *complete* implicit-invocation surface (magic methods, operator
overloads, FFI exports, reflection hooks). The Rust reference documents `#[no_mangle]` /
`#[export_name]` independent of `pub` visibility — so a non-`pub` `#[no_mangle] extern "C" fn`
exports its symbol yet has no `pub` to trigger export-rooting, leaving it (and everything its body
reaches) false-flagged dead. No scanned crate had hit it: real crates pair `#[no_mangle]` with
`pub` (cdylib convention), masking the non-`pub` path. A minimal fixture isolates exactly that
combination. Fix: `_is_rust_export_attr` + role `exported` (the Rust analogue of C `EXPORT_SYMBOL`).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R69 | — | discovery | doc-driven: a non-`pub` `#[no_mangle] extern "C" fn` confirmed cardinal false-dead by a minimal fixture (`rust_entry dead? True`). The reference, not a repo, exposed it. |
| R70 | 3 | ✗ | sonnet + haiku clean, but **opus** found the path-only matcher missed `#[unsafe(no_mangle)]` / `#[unsafe(export_name=…)]` — the **required** spelling in the **Rust 2024 edition** (mainstream, not an edge case) — and `#[cfg_attr(<pred>, no_mangle)]`; a non-`pub` export in either form stayed false-dead (verified conf 0.6). Fix: drop string-literals, then match the export token anywhere in the attribute (covers the `unsafe(...)` / `cfg_attr` wrappers; keeps `#[doc="no_mangle"]` from reading as an export). |
| R71 | 3 | ✓ | full-diversity clean on the broadened fix — raw strings/escaped quotes, interleaved doc comments, `extern "C" {}` import blocks, macros, transitive depth >1, `pub`/`#[test]` coexistence; cross-language non-regression (the `attr_export` path is structurally Rust-only); word-boundary negatives (`#[no_mangle_extra]`/`#[xno_mangle]` False). |
| R72 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. opus dogfooded **actual serde** (`test_suite/no_std`'s non-`pub` `#[no_mangle] rust_eh_personality` now correctly rooted; no crash, no catastrophic backtracking); sonnet ran the oracle suite (27/27: streaming==full, incremental==full, graphblas==pure-python) + multi-file cross-module reachability from an export root; confidence proof shows only genuinely-dead code flagged (0.6), export callees live by reachability not threshold-filtering. |

The 2.1.3 lesson reprises and sharpens the 2.1.2 one: **the doc-driven fixture found the gap, but
the first fix was still incomplete — and again model diversity caught it.** opus (not sonnet this
time) found the edition-mandated `unsafe(...)` wrapper the path-only matcher missed. Reading the
reference tells you *which mechanism* exists; it does not tell you *every syntactic spelling* of
it (the 2024-edition `unsafe(...)` wrap is itself documented elsewhere in the reference). So
doc-driven discovery and adversarial breadth are complementary: the doc finds the missing
mechanism, the panel finds the missing spelling. Cardinal-safety made the broadening cheap — a
wider token match can only ever over-root (mask dead code), never flag live code dead.

## 2.1.4 — C/C++ entry-point & export attribute cardinal fix, doc-driven (R73–R77)

Continuing the doc-driven method into the GCC/Clang/MSVC function-attribute reference, which
enumerates the C/C++ attributes that make a symbol an implicit entry point or public ABI with no
in-tree by-name caller. A minimal fixture confirmed a cardinal cluster flagged dead at 0.6:
`__attribute__((constructor))`/`((destructor))` (run automatically around `main`), `((used))`,
`visibility("default")`, `__declspec(dllexport)` — and every helper they reach. Fix: `_c_attr_roots`
maps these to roots (callback for runtime/used, exported for visibility/dllexport). Two follow-on
helpers landed as the panels broadened coverage: `_c_alias_target_names` (alias/ifunc targets) and
`_c_dangling_attr_texts` (recovering an attribute the C++ grammar mis-attaches to a preceding
empty-body method).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R73 | — | discovery | doc-driven: the C/C++ attribute cardinal cluster confirmed by a minimal fixture (conf 0.6). |
| R74 | 3 | ✗ | opus found a CARDINAL miss — the GCC `__name__` synonyms (`__constructor__`, …, common in system headers) the `\b`-anchored match couldn't see (`_` is a word char); fixed with optional `_*`. opus also flagged Finding 2 (recall): `weak`/`section` fns + `alias`/`ifunc` **targets** still dead — the maintainer opted to fix in-release (`section`→callback, `weak`→exported, alias/ifunc target kept live by name). sonnet CLEAN (no regression, oracle 27/27, F→M normalization preserves roles); noted the empty-body-method gap as pre-existing/non-cardinal. haiku CLEAN. |
| R75 | 3 | ✓→reset | full-diversity clean on the weak/section/alias code — but the streak **reset**: responding to sonnet's R74 empty-body note, the maintainer said *fix limitations, don't document them*, so `_c_dangling_attr_texts` landed after this round and is re-gated by R76/R77. opus: cross-file alias is invalid C (gcc rejects cross-TU alias); sonnet: EXPORT_SYMBOL∪alias per-file scoping correct, oracle 27/27. |
| R76 | 3 | ✓ | full-diversity clean on the final tree incl. the empty-body recovery. opus 8-scenario attack — the recovery is structurally additive (only copies attr text onto a node) so it can't manufacture a false-dead; self-attr-steal harmless, chains/nested/malformed all no-crash. sonnet whole-file monkeypatch non-regression + determinism. |
| R77 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Fresh: K&R defs, complex declarators, header+source, 50-fn stress (exactly the 25 dead flagged), prior-release regressions 281 pass, empty-body recovery under streaming. Surfaced two non-regressions: **F1** macro-wrapped attribute (`#define EXPORT …`) is unseeable without a preprocessor — now documented (unfixable); **F2** an export attribute on a C++ *header declaration* isn't propagated to the out-of-line definition (a pre-existing, documented C recall tradeoff surfacing at 0.6) — queued as the **v2.1.5 lead fix**. |

The 2.1.4 lesson sharpens the doc-driven theme into a working rule: **the reference finds the
mechanism; the panel finds every spelling of it; the maintainer's "fix don't document" turns the
review's *documented gap* into the *next fix*.** R74 alone added three spellings the bare-keyword
match missed (`__name__` synonyms, the alias/ifunc target indirection, weak/section); R75→R76 turned
sonnet's documented empty-body limitation into a fix. The only things left documented for C/C++ are
genuinely unfixable (macro-wrapped attributes — no preprocessor) or a real refactor (header-decl
attribute propagation, now scheduled). Cardinal-safety made every broadening cheap: a wider match
can only over-root (mask dead code), never flag live code dead.

## 2.1.5 — C/C++ header-declaration export-attribute cardinal fix, from the limitation audit (R78–R80)

A full audit of `LIMITATIONS.md` (per the maintainer's *fix it, don't document it* direction) triaged
every note into fixable / fundamental / intentional and promoted the one genuinely-cardinal fixable
item — R77 Finding 2 — to a fix. The export attribute (`visibility("default")` / `dllexport`) is
commonly placed on the **header declaration** while the out-of-line `.cpp` definition carries none, so
the public-ABI method (and its callees) was false-flagged dead at 0.6. Fix: `_c_export_decl_names`
collects export-attributed declaration names **project-wide** (declaration and definition live in
different files) and roots the matching definition by name — the C/C++ analogue of Python's `__all__`.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R78 | 3 | ✗ | opus found a CARDINAL miss: the collector matched only *direct* `function_declarator` children, so a pointer/reference-returning export (`char* W::make(int)`, whose `function_declarator` nests inside a `pointer_declarator`) wasn't collected and its live def was flagged dead at 0.6. Fixed by reusing `_name_of` (which descends `_DECLARATOR_WRAPPERS`), guarded by `_has_function_declarator`; this also fixed the template-param mis-collection. sonnet/haiku reviewed the pre-fix code. |
| R79 | 3 | ✓ | full-diversity clean on the fixed tree — every return-type wrapper (`char*`/`int&`/`int&&`/`const char*`/`char**`/function-pointer-return/member/qualified-nested) roots and propagates to helpers; determinism, full==streaming, incremental==full. Two non-cardinal informational notes (both safe over-root direction, pre-existing): a function-pointer *variable* over-rooting its name, and a deeply-namespace-nested class *shell* flagged via a `_cpp_method_scope` gap. |
| R80 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Realistic multi-class lib (exactly the genuinely-dead internals flag), oracle 27/27, bounded two-library same-name over-root (== Python `__all__`), order-independent, cross-language gated, 100-export + 50-dead stress exact. opus surfaced **F1** (pre-existing, non-blocking): a class exported via a *class-level* attribute doesn't propagate `exported` to its public methods — queued as **v2.1.6**. |

The 2.1.5 lesson: **a limitation audit is itself a doc-driven hunt** — `LIMITATIONS.md` enumerates the
known gaps the way a language reference enumerates mechanisms, and triaging it cleanly separates the
*fixable* (this release), the *fundamental* (needs a type model), and the *intentional* (the contract).
R78 reprised the now-familiar pattern one more time (the fix handled the common declarator, the panel
found the wrapped one). Cardinal-safety again made the project-wide rooting cheap: over-rooting a
homonym across libraries is bounded and safe, exactly as Python's flat `__all__` already is.

## 2.1.6 — C/C++ class-level export-attribute cardinal fix (R81–R83)

The last cardinal item from the limitation audit (R80 F1), completing the C/C++ export-attribute
story (definition → header-declaration → class-level). `class __attribute__((visibility("default")))
Foo {…}` / `__declspec(dllexport)` exports the whole public interface, so a public method with no
per-method attribute is public ABI; its out-of-line definition carries none and was false-flagged
dead. Fix: `_c_public_method_names` collects the public/protected method names of an
export-attributed class body into the project-wide `c_decl_exports` set.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R81 | 3 | ✗ | opus found a CARDINAL miss: the collector iterated only `field_declaration` children, so an **inline-defined** public method (parses as `function_definition`; templated → `template_declaration`) was missed and flagged dead at 0.6. Fixed by handling all three member shapes and including `protected` (out-of-tree subclass ABI). sonnet (its long run overlapped the commit) reviewed the fixed code and confirmed clean. |
| R82 | 3 | ✓ | full-diversity clean — every member-shape × access combination, defaulted/deleted/pure/qualified/friend/using, empty-body interaction, `struct`/`class` `__declspec` variants, determinism, full==streaming, incremental==full. Footnote (non-blocking): a *double-nested* `template<T> template<U>` member isn't collected — but that is ill-formed C++ no compiler accepts, so it is out of the cardinal invariant's practical scope. |
| R83 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Real-world header-only lib, the entire v2.1.4–2.1.6 attribute surface in one project (no cross-mechanism interference), namespaced exported classes, inheritance/virtual overrides, 40-class+30-dead stress (exactly the 30 flag), bounded two-library over-root, cross-language gated, nested classes correctly excluded (GCC visibility rules). |

The 2.1.6 lesson closes the C/C++ export-attribute arc that ran v2.1.4→v2.1.6: **the same
find-the-common-form / panel-finds-the-other-form rhythm repeated at every level** — definition
attrs (v2.1.4: bare → `__name__` synonyms → weak/section/alias), header declarations (v2.1.5:
direct → pointer/reference wrappers), class-level (v2.1.6: declared-only → inline/templated). Each
broadening was cardinal-safe, so the cost of the iterative widening was only review rounds, never a
shipped regression. What remains documented for C/C++ is genuinely unfixable (macro-wrapped
attributes — no preprocessor) or non-compilable (double-nested member templates).

## 2.1.7 — recall: third-party Rust test harnesses + ByteBuddy/Moshi annotations (R84–R85)

The first **non-cardinal** release from the limitation audit — recall gaps that under-report live code
as dead (a test/framework method that *is* reached, surfacing as a stale candidate). `_is_rust_test_attr`
gained the common third-party harnesses (`#[rstest]`/`#[test_case]`/`#[gtest]`/`#[quickcheck]`, matched
on the last path segment); the Java callback-annotation set gained ByteBuddy `@Advice.OnMethodEnter`/
`@OnMethodExit` and Moshi `@ToJson`/`@FromJson`.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R84 | 3 | ✓ | full-diversity clean **on the first round** (unlike the cardinal fixes, which kept surfacing wrapped/inline forms). Last-segment match rejects `#[my_rstest]`/`#[derive(rstest)]`/`#[cfg(feature="rstest")]`, accepts the real ones; monkeypatch diff shows only the intended new roots; zero cross-language bleed. |
| R85 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Stress (30 Rust + 20 dead → 20/20 flagged; 20 Java + 15 dead → 15/15 flagged), rstest `#[case]`/`#[fixture]`/mixed `mod tests`, Kotlin produces zero nodes (no bleed), Go `OnMethodEnter` live by Go's own export rule (not bleed), build.rs/malformed no crash, determinism 5×, 290 regressions + 426 full. |

The 2.1.7 contrast is the lesson: **a recall (additive-allowlist) change converges in one round, where a
cardinal extraction change took two or three** — because over-rooting is structurally safe, so the only
review questions are over-match, cross-language bleed, and regression, all of which a single thorough
round settles. The cardinal fixes had a second axis (every syntactic form of the mechanism) that only
adversarial breadth across rounds exhausts.

## 2.1.8 — recall: PHP bare-string function callables (R86–R87)

The last queued non-cardinal item from the limitation audit. A PHP global function passed by bare
string to a known callback builtin (`usort($x, 'topcmp')`, `call_user_func('handler')`,
`array_map('mapper', …)`) is reached at runtime but the syntactic call scan misses the string;
`_php_string_callable_names` now emits a REFERENCES edge to it, scoped to a curated builtin allowlist
so an ordinary string matching a function name doesn't over-root. Also a docs-only correction:
`export * from './m'` is *not* an unrooted JS form (re-exported symbols are already inline-exported in
`m`), verified by fixture.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R86 | 3 | ✓ | full-diversity clean on the first round. Positive coverage across both arg positions, over-match guard (string to a non-builtin stays dead), `Class::method`/namespaced safely dropped, cross-language no bleed, JS `export *` independently verified non-gap. The one finding — top-level module-scope callables not covered — is the *documented* pre-existing recall gap (both array and bare-string scans run over def bodies, not `_module_uses`). |
| R87 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Magento-style mixed array+bare-string, nested `array_map(array_filter(…))`, closures, heredoc/interpolated/constant callbacks (no crash), all 6 input guards correct, polyglot index (PHP+Rust+Java+C+++Python) zero contamination, cardinal matrix 12/12, self-dogfood on the real Python codebase unchanged, 292 regressions + 428 full. |

The 2.1.8 lesson reinforces 2.1.7's: the recall release converged in **two clean rounds with zero
code findings across both** — the only notes were pre-existing documented gaps (module-scope,
heredoc, `preg_replace_callback_array`) and a *docs correction* (`export *`). A scoped-allowlist
additive change is the cheapest kind to gate, because the only failure modes are over-match (bounded
by the allowlist) and cross-language bleed (bounded by the spec flag), both settled in one thorough
round.

## 2.1.9 — runtime / native (FFI) entry-point directives across Rust, C#, Go (R88–R89)

Doc-driven hunt into each language's *runtime-entry* surface — functions a runtime or native caller
invokes automatically, with no in-tree caller, and (unlike the already-covered `pub`/public forms)
not necessarily public. Each candidate was probed with a minimal fixture before fixing, and the
already-covered forms were left alone (the JS-`export*` discipline): Rust `#[proc_macro]*` require
`pub`, C# `[JSInvokable]` on a public method, Go capitalised `//export`, and `#[global_allocator]`
(on a `static`, never extracted) were all confirmed already-live and skipped.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R88 | 3 | ✓ | full-diversity clean on the first round. The decisive test: one index with Rust+Go+C#+Java+Python all defining `start`/`export`/`UnmanagedCallersOnly` — all 13 unattributed homonyms stayed dead, only the 3 attributed rooted (zero cross-language bleed). Wrapper syntaxes covered; name-mismatch/blank-gap/prev-None guards work; streaming==full (the Go `prev_sibling` read happens in pass 1 before the tree is freed). Non-cardinal notes: the Go regex over-roots `// export`(space) / a blank-line gap — cardinal-safe, real cgo never triggers. |
| R89 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Real-world embedded-Rust / Go-cgo / C#-native shapes; a polyglot index exercising *every* prior cross-language fix at once (no interference); cardinal matrix 12/12; stress 36-dead-exact across three languages, 3 orderings identical; word-boundary + name-equality + csharp-only-set verified at the code level. |

The 2.1.9 lesson extends the export-attribute arc to a sibling surface — *runtime*-invoked entries
(panic handlers, native callees, cgo exports) rather than *linker*-exported symbols — and the
cross-language bleed test became the centerpiece: as the per-language entry-point sets grow, the
sharpest risk is one language's marker leaking into another, so the "N languages, same symbol name,
only the right ones root" fixture is now the canonical check for any entry-point addition.

## 2.1.10 — Python IPython/Jupyter display-protocol hooks, found by dogfooding rich (R90–R91)

The first release this stretch **discovered by dogfooding a real library** rather than doc-driven
hypothesis. Indexing `rich` flagged `JupyterMixin._repr_mimebundle_` dead: the IPython rich-display
protocol (`_repr_html_`, `_repr_png_`, `_repr_mimebundle_`, `_ipython_display_`, …) is invoked *by
name* by IPython on display, but the methods are *single*-underscore so the `__x__` dunder pass
missed them. Fix: `_seed_protocol_dunders` ties a class's IPython-protocol methods to the class like
dunders (shared `_is_protocol_method`; documented 13-name set, exact membership).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R90 | 3 | ✓ | full-diversity clean on the first round. Cardinal-safety is structural (the seed edge is class→method, so it never self-roots the class — a class whose only surface is a hook stays fully dead); scope discrimination (a top-level *function* named `_repr_html_` stays dead); exact frozenset (no `_repr_*` glob); dunder pass unchanged; rich 38→36 (exactly the 2 hooks). |
| R91 | 3 | ✓ | full-diversity clean — **streak 2, gate met, RELEASABLE**. Breadth dogfood across 6 real libs (ipython/prompt_toolkit/rich/tabulate/textual/traitlets) — 65 protocol hooks, 0 flagged dead; `--precise` path identical; override interaction; 30-item stress; polyglot zero interference; cardinal matrix 12/12. |

The 2.1.10 lesson is the value of **dogfooding over hypothesis**: doc-driven hunting enumerates a
language's *own* implicit surface, but a real library exercises *ecosystem* protocols (IPython's
display hooks aren't in the Python language reference — they're an IPython convention). Running on
`rich` surfaced in one shot what no amount of synthetic Python probing would. The fix slotted into
the existing dunder-seeding machinery (same class-scoped, cardinal-safe mechanism), and the
breadth-dogfood across six IPython-integrating libraries (65 hooks, 0 false-dead) is the kind of
real-world validation that earns "best repo."

## 2.1.11 — Python implicit-invocation surface: generics, enum hooks, pytest hooks (R92–R93)

Three Python cardinal false-positives in one release, found by **combining** the two hunting modes
that earlier releases used separately: real-codebase dogfooding (sqlalchemy, werkzeug) *and* a
doc-driven manual pass over the Python language/library reference. All three are reachability-*adding*
and therefore cardinal-safe by construction (they can only make more code live).

1. **Subscripted generic base** (`class Sub(Base[K, V])`) recorded no INHERITS edge — the base is an
   `ast.Subscript` and `_name_of` returned `None`, dropping the edge and the polymorphic-override
   path. New `_base_name` helper unwraps the subscript (loops for nested `Base[K][V]`).
2. **Enum machinery hooks** `_missing_` / `_generate_next_value_` — single-underscore (not dunders),
   invoked by name by the enum metaclass. Added to `_is_protocol_method` so the existing class→method
   seed keeps them (and callees) live when the enum is reachable.
3. **pytest plugin hooks** `pytest_*` in test files — discovered/invoked by name by pytest with no
   in-tree call site. New `_is_pytest_hook` roots them (callback role), scoped to `is_test_file`.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R92 | 3 | ✓ | full-diversity clean round 1. All three fixes strictly reachability-adding (subscripted base previously produced *neither* edge nor external-base entry — no diversion possible); `Generic[T]`/`Protocol[T]` in `_PLAIN_BASES` so not framework-misclassified (dead private method still flagged); enum/pytest hooks class- or test-file-scoped, dead variants stay dead. Minor/nit (nested-subscript one-level, pytest in any test-tree file, comment wording) all cardinal-safe — addressed in polish (`_base_name` while-loop, scope-accurate comment, external-base + nested-subscript tests). |
| R93 | 3 | ✓ | full-diversity clean round 2 — **streak 2, gate met, RELEASABLE**. opus: full/streaming/incremental byte-identical, determinism holds, helper fuzzing. haiku: 441/441 + oracles 27/27 + ruff/mypy clean. sonnet: real-corpus dogfood (click/flask/requests/httpx/werkzeug) live-stays-live, and **confirmed the fix also resolves a real pre-existing false *negative*** — `flask.SecureCookieSession(CallbackDict[str, t.Any])` now correctly gets the `callback` role. |

The 2.1.11 lesson: **dogfood + manual-reference together** is stronger than either alone. The manual
pass enumerates a language's own implicit surface (enum hooks, subscripted-generic syntax); dogfooding
proves they bite in real code (sqlalchemy/werkzeug mixins, conftest hooks) and — as the flask
false-negative shows — the same INHERITS-edge fix that closes a cardinal gap also tightens recall.
A single shared helper (`_base_name`) now backs the INHERITS edge and external-base detection, with a
tracked follow-up to extend it to `_is_abstract_class` (find_holes precision, non-cardinal).

## 2.1.12 — Transitive framework-inheritance callback rooting, tree-sitter (R94–R95)

One cardinal fix clearing the **same root cause across PHP, C#, Java, and C++** — a symmetry gap
where the Python extractor's `_apply_callback_roles` did a transitive INHERITS closure but the
tree-sitter extractor marked only the *direct* subclass of an external framework base. A concrete
override two-or-more hops below the framework base (via an in-tree abstract intermediary) is
framework-invoked but had no in-tree caller, so it was confidently flagged dead. New
`_framework_classes` helper: (a) direct external base + (b) same-name self-loop + (c) transitive
first-party closure (fixpoint down the in-tree INHERITS tree). Confirmed on real Magento 2.4.7 and
the C# explicit-`IDisposable.Dispose`-via-project-interface shape.

This release is the clearest example yet of **the gate doing its job**: round 1 was clean on opus's
*first* pass — but a deeper opus probe found a blocker (the case-(b) self-loop, `class Foo extends
pkg.Foo`, was dropped in the port, reintroducing the same cardinal class in a different shape). Fixed
to full python.py parity (cases a+b+c), then a fresh two-round full-diversity gate.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R94 | 3 | ✓ | re-gate round 1 (after the self-loop blocker fix). opus: blocker genuinely fixed (PHP/C#/Java self-loop overrides live), full a/b/c parity, cycle-safe, determinism + streaming parity; one pre-existing collision MINOR + a docstring NIT (both addressed/tracked). sonnet: **1000-iteration fuzz proves the change is a strict superset of prior rooting — under-rooting mathematically impossible vs pre-2.1.12**; real PHP/Java/C++ dogfood live-stays-live. haiku: 446 + oracles 27 + ruff/mypy + R94 tests green. |
| R95 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE**. Final confirmation on the complete release (incl. the docstring caveat); no cardinal introduced, over-mask boundary holds, residual cross-file-collision case confirmed pre-existing and tracked. |

The 2.1.12 lesson: **a clean first look is not a clean gate.** opus's initial pass said RELEASABLE;
its deeper pass found a real cardinal. Two-round, multi-model, *adversarial-not-confirmatory* review
is what catches the port that's 90% right. The residual name-collision case (a true self-loop that
also collides with an unrelated same-named class) is the one shape still resolved by name rather than
`dst_id`; cardinal-safe in common shapes, pre-existing, tracked for a resolved-edge fix.

## 2.1.13 — Runtime/native entry-point attributes: C ISR, Rust `#[ctor]`, Java `native` (R96–R97)

Three narrow cardinal fixes batched into one release, extending the v2.1.9 runtime/native (FFI)
entry-point arc to the attribute/modifier-marked entries the runtime or toolchain invokes
automatically: C `__attribute__((interrupt))`/`((interrupt_handler))`/AVR `((signal))` (rooted
`callback` via the implicit-entry regex), Rust `ctor`/`dtor` (added to `_RUST_RUNTIME_ENTRY_ATTRS`),
and Java `native` (new `_is_java_native`). All attribute/modifier-gated, add-roots-only.

Like 2.1.12, the gate paid off: the first round-1 pass found a **cardinal blocker** (opus *and*
sonnet, independently) — the ISR regex matched `interrupt`/`signal` but not the trailing-word form
`interrupt_handler` (after `interrupt`, the `_` is a word char so `\b` fails), so ARM/MIPS/m68k
`__attribute__((interrupt_handler))` ISRs were flagged dead *and the changelog falsely claimed the
form was covered*. Fixed (`interrupt(?:_handler)?|signal(?:_handler)?`) + regression, then a fresh
two-round full-diversity gate.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R96 | 3 | ✓ | re-gate round 1 (after the `interrupt_handler` blocker fix). opus: blocker fixed (interrupt_handler/signal_handler ISR + callees live), `(?:_handler)?` neither over- nor under-matches (`interrupt_handler_foo` longer identifier stays dead; `my_interrupt_handler` attr name doesn't match), Rust/Java re-confirmed. sonnet: 11-form C ISR matrix incl. `[[gnu::interrupt]]` + GNU synonyms, Rust 9 forms, Java 10 forms — all correct; macro-wrapped ISR is the pre-existing preprocessor boundary, not new. haiku: interrupt_handler verified empirically, 450 + oracles 27 + ruff/mypy + readiness RELEASABLE. |
| R97 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE**. Final confirmation on the corrected release; no cardinal across all three fixes; over-mask boundary holds; determinism/streaming parity. |

The 2.1.13 lesson reinforces 2.1.12's: **a regex that's right for the keyword can still be wrong for
the keyword's real-world spellings.** `interrupt` matched but `interrupt_handler` (a genuine
MIPS/m68k/ARM attribute) did not — and a confirmatory review that only checked the documented happy
path would have shipped it. The adversarial probe that enumerated *toolchain spellings* found it. The
reference/audit finds the mechanism; the adversarial panel finds the spelling.

## 2.1.14 — Ruby implicit conversion / Enumerable protocol methods (R98–R99)

The Ruby analogue of Python's dunder rooting: the interpreter/stdlib invoke a class's conversion
(`to_s`/`inspect`/`to_str`/…), numeric-coercion (`to_i`/`to_f`/`to_r`), Enumerable (`each`),
Hash-key (`hash`/`eql?`), and marshalling (`marshal_dump`/`_dump`/…) methods *by name*, so a live
class's protocol methods (and their callees) were false-flagged dead. Fix: extend
`_IMPLICIT_HOOKS["ruby"]` with the documented protocol names — each such method (in a `.rb` file) is
rooted `callback`. Add-roots-only, cardinal-safe; Ruby-gated (no cross-language bleed).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R98 | 3 | ✓ | round 1. opus: all 22 names verified genuine Ruby protocol (no wrong member), Ruby-gated (no bleed into Python/JS `each`/`to_s`/`hash`), callee cascade live, genuinely-dead non-protocol methods still flag; NIT (top-level free fn named `to_s` also rooted — safe over-root, consistent with existing hooks). sonnet: 54 fixtures incl. real Rack/Money/ActiveRecord-style gems, before/after patch comparison proving the FPs were real; flagged `to_f`/`to_r` omission (same cardinal class as `to_i`). haiku: cross-language bleed empirically negative, 451 + oracles 27 + ruff/mypy + RELEASABLE. |
| R99 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE**. `to_f`/`to_r` added (completing the conversion family); final confirmation on the complete set — no cardinal, no bleed, over-mask boundary holds. |

The 2.1.14 lesson: **complete the family.** Adding `to_s`/`to_i` but not `to_f`/`to_r` leaves the
same cardinal open for the un-added members — `Integer(obj)`/`Float(obj)` emit a call to
`Integer`/`Float`, never to the object's hook, so a coercion-only `to_f` has no textual caller. The
panel that enumerated the *whole* numeric-conversion family caught the omission a per-name spot-check
would not.

## 2.1.15 — C++ range-based-`for` `begin()`/`end()` customization points (R100–R101)

`for (x : r)` is desugared by the compiler to `r.begin()`/`r.end()` (or ADL `begin(r)`/`end(r)`), so
the name-based call graph never sees those calls — an iterable type's `begin`/`end` (and what they
reach) were false-flagged dead. `_IMPLICIT_HOOKS` had no C++ entry and the special-member pass covers
only operators/destructors. Fix: a `"cpp"` entry rooting `begin`/`end` as `callback`. Add-roots-only,
cardinal-safe.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R100 | 3 | ✓ | round 1. opus: core fix works (begin/end + callees live, genuinely-dead still flag), pre-existing cardinal confirmed, const `begin() const` extracts to name `begin` (rooted), no cross-language bleed, no C-file over-rooting; flagged a DOC inaccuracy — the `.h`-as-C boundary claim is wrong, `_header_lang` content-sniffs C++ `.h` headers to `cpp` so their begin/end ARE rooted (better coverage, cardinal-safe). sonnet: real C++ lib dogfood (`Span`/`SmallVec`/`FilterView` in `.hpp`) all live, rbegin/rend correctly NOT rooted (called explicitly), same doc-inaccuracy finding. haiku: C-file bleed negative, 452 + oracles 27 + RELEASABLE. |
| R101 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE**. Doc wording corrected (the `.h` `_header_lang` sniff); final confirmation across `.cpp`/`.hpp`/`.h`-C++/pure-C-`.h`/`.c` — no cardinal, no bleed, over-mask boundary holds, determinism. |

The 2.1.15 lesson: **the docs are part of the diff.** The code was correct and cardinal-safe, but the
comment/CHANGELOG/release-notes asserted a `.h` "out of scope" boundary that the `_header_lang`
content-sniffer had already removed — the panel checked the *claim against the code* and caught it. A
factually-wrong comment that understates coverage is still a defect worth fixing.

## 2.1.16 — Bash callback/invocation argument recognition (R102–R103)

Commands that invoke a function via an *argument* (not the command head) are missed by the
head-keyed command scan. `_bash_trap_handlers` generalized to `_bash_callback_refs`, now rooting the
function named by `trap HANDLER` (incl. inside function bodies), `complete -F`/`compgen -F`,
`export -f` (a `declaration_command`), and `time FUNC` — each routed through `_ref` so only project
functions are rooted (cardinal-safe).

Two adversarial rounds drove the parser to correctness on the messy real-world spellings:

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R102 | 3 | ✓ | re-gate round 1 (after the first round's recall/precision fixes: quote-strip in `_bash_command_words` for `time "bench"`/`complete -F "_c"`; `_bash_flag_arg` rewritten to take the immediate slot after `-F` so `complete -F ${VAR} cmd` stops grabbing `cmd`). opus + haiku clean; sonnet confirmed both fixes and found three pre-existing LOW gaps. |
| R103 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE**. The one pathological cardinal from R102 (`complete -F f1 -F f2` rooting only the first, flagging the live last handler dead) fixed — `_bash_flag_arg` now roots every `-F` slot (cardinal-safe over-rooting of the overwritten one). Final confirmation: no cardinal across all four mechanisms; trap precision intact; genuinely-dead still flags; no cross-language bleed. |

Pre-existing LOW gaps left as tracked follow-ups (cardinal-safe / rare): bare `trap EXIT` one-word
reset rooting the signal name; `time { group; }` brace form (tree-sitter grammar limitation);
`declare -xf`/`typeset -fx` export-f synonyms.

The 2.1.16 lesson: **an argument parser meets the shell's real grammar, not the tidy form.** The tidy
`complete -F _c cmd` worked first try; the rounds found `-F "_c"` (quoted), `-F ${VAR} cmd` (dynamic,
grabbed the wrong word), and `-F f1 -F f2` (last wins) — each a different real spelling. Routing every
candidate through `_ref` kept all of them cardinal-safe while the spellings were nailed down.

## 2.1.17 — Ruby `&:symbol` / `enum_for` / `&method(:m)` symbol dispatch (R104–R105)

Ruby names a method via a literal symbol in idioms the name-based call graph can't see, so the method
(and its callees) was false-flagged dead. New `_ruby_symbol_refs` pass roots the method named by
`&:sym` block arguments and by the first symbol arg of `enum_for`/`to_enum`/`method`/`instance_method`
— each routed through `_ref` so only project methods are rooted (cardinal-safe). `send`/`public_send`
stay the documented dynamic-dispatch limitation.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R104 | 3 | ✓ | re-gate round 1 (after a CARDINAL fix). opus's *first* round found the blocker — `_ruby_symbol_name` kept the `=` for setter symbols (`:name=` → `name=`), but setter defs are keyed without the `=` (`name`), so `method(:name=)` flagged the live setter dead; sonnet and haiku both missed it. Fixed (strip a trailing `=`, keep `?`/`!`). This R104: opus re-confirmed the fix; sonnet ran a full symbol-name surface audit (operators `:[]`/`:[]=`/`:+`/`:<=>`/`:-@` correctly skipped here and pre-rooted by `_is_ruby_operator_method`; setter was the *only* def-key mismatch); haiku verified the setter end-to-end. |
| R105 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE**. Final confirmation across all dispatch forms; setters live; genuinely-dead flags; `send` excluded; no cross-language bleed; determinism. |

The 2.1.17 lesson: **emit the key the def side uses, not the source spelling.** `?`/`!` are part of a
Ruby method name (kept) but `=` is stripped from setter def keys — so the symbol side had to match.
The single diverse reviewer who tested a *setter* found a cardinal two others, testing only
getters/predicates, called clean. Diversity of *test inputs* across the panel is the safeguard.

### 2.1.18 — JS/TS object-literal function-member bodies (#48)

A top-level function called only inside an object-literal member (`const obj = { run(){ helper() } }`,
method shorthand / function-valued property / nested object) was false-flagged dead: the object value
was never traversed, so the member body's calls were invisible. The fix grew a new `_object_members`
pass (members extracted as nodes, bodies walked, module-scope members rooted `callback`; class
members `exported`; fn-scoped members CONTAINS-gated). This was the **deepest single surface of the
campaign** — thirteen rounds, ten distinct cardinal classes — because every round of the diverse
panel exposed one more {value-kind × wrapper × scope × branch} combination the extraction had to
cover, and once because a fix over-reached.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R106 | 3 | — | core #48; round-1 cardinals: dynamically-dispatched underscore members (`handlers["_"+x]()`), computed-key member body walk, string-key members (opus found the underscore-dynamic one). |
| R107 | 3 | — | member-VALUE wrappers (`run: (() => h())`, `(fn satisfies T)`) and class-valued members (`{ Parser: class {} }`) dropped the member body (opus, 3). |
| R108 | 3 | — | fn-scoped class-valued member orphaned its methods (`enclosing_func=None` walked the class body as module-scope) — sonnet; gated methods to the class. |
| R109 | 3 | — | the `assignment_expression` branch carried the identical fn-scoped class orphaning (`obj.X = class{}` in a fn) — sonnet; same one-liner gate. opus clean. |
| R110 | 3 | ✓ | CLEAN. opus noted one MINOR pre-existing parity item (assignment-branch underscore gate vs object path) → deferred #77. |
| R111 | 3 | — | TS wrappers not peeled on the fn/class assignment RHS (`obj.X = (class{}) satisfies T`) nor the arrow-const value — sonnet+opus; `_unwrap_ts_value` applied uniformly. |
| R112 | 3 | ✓ | CLEAN. opus noted the `_unwrap_ts_value` `seen<8` cap under 9+ literal parens (LOW/theoretical) → deferred #79. |
| R113 | 3 | — | `generator_function` values omitted from the function-value tuples (opus); a **delayed** round-8 sonnet additionally found the chained/parenthesized-assignment value gap (`const routes = module.exports = {}`). |
| R114 | 3 | ✓ | completed `generator_function` in the 4th tuple (the multi-line `_module_uses` def-skip the round-8 edit missed). Cardinal-SAFE precision; both reviewers clean on cardinals. |
| R115 | 3 | ✓ | CLEAN on the generator-complete code; full matrix + real-world objects (Redux/Vuex/Express/RxJS). |
| R116 | 3 | — | **REGRESSION caught + reverted.** The round-8 chained-assignment `else` fallthrough escalated the pre-existing #75 expression-shape family (helper-recall) into a CARDINAL by minting object/class methods as *unrooted, mis-qualed* nodes (opus). Reverted; chained-assignment + expression-position objects deferred to #75. |
| R117 | 3 | ✓ | CLEAN — opus+sonnet `FINDINGS: none`; haiku surfaced only the **verified-pre-existing** #75/#80 family (proven byte-identical on the `5cb47bc` baseline). First of two consecutive clean on the final HEAD. |
| R118 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** All three clean; ~45/35 differential fixtures HEAD-vs-`5cb47bc`, zero new flags, no crash. Direct object-literal member surface fully hardened. |

The 2.1.18 lessons — three of them: (1) **a partial fix can be worse than the gap.** R116's `else`
fallthrough "fixed" a pre-existing helper-recall (#75) but escalated it to a live-*method*-flagged-dead
cardinal by letting generic descent mint object members as unrooted nodes; reverting and deferring the
whole family to one principled pass was correct. (2) **wait for every reviewer.** The delayed round-8
sonnet (22 min) found the chained-assignment cardinal after the round had been prematurely concluded
on opus+haiku — concluding a round before its slowest model returns hides findings. (3) **distinct
test names matter:** a self-probe was masked when a fixture's method (`m`) collided with the module
node id (`m.ts::m`); the panel's `svc.ts`/`mmm` naming exposed it. The expression-position family
(#75: IIFE/ternary/`||`/`Object.freeze`/array/sequence/chained-assignment), `const X = class {…}`
(#80), and a few others are **pre-existing** (identical on `5cb47bc`) and deferred to a focused next
release that routes every object literal through `_object_members`.

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
