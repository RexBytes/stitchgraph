# Review History

How stitchgraph is being reviewed and hardened toward v1.0.0: the trajectory, the
issues found, and the fixes. Methodology in `CONTRIBUTING.md`; deliberate
tradeoffs in `LIMITATIONS.md`; the rubric in `RELEASE_READINESS.md`.

## TL;DR

| Metric | Value |
|---|---|
| Multi-model review panels | 2.1.29: R140–R141 on Python abstract/Protocol interface methods (#70/#86). 2.1.30: R142–R143 on C/C++ struct-used-as-a-type (#89). **2.1.31: R144 (2 cardinals found) → R145–R146** (full diversity opus/sonnet/haiku) on Bash function-export recall (#73) — clean in 2 fresh rounds; **closes the #70–#89 follow-up backlog** |
| Hard gates | tests ✅ · ruff ✅ · mypy ✅ · mutation (`structure.py` 15/15 + `graphdiff` 9/9; 3.1.0 added `similar.py` 29/32, 3 justified-equivalent) ✅ · oracles 233 (3.2.0 +51-case JS/TS; 3.3.0 +45-case Go; 3.4.0 +38-case Rust battery) ✅ · no-open-defects ✅ |
| Tests | 863 passing (full extras) |
| Coverage | ~93% |
| Convergence | 2.3.0: shared `tarjan_scc` → R149 (NIT) → R150 (✗ MEDIUM) → R151✓ R152✓. 3.0.0: intra-procedural body matrix → R153–R163 (8 fix rounds) → R164✓ R165✓ on a frozen HEAD. 3.1.0: mutation-harden `find_similar`'s dense path → R166 → R167✓ R168✓. 3.2.0: body matrix → JS/TS/TSX (`structure_js.py` + 51-case oracle) → R169–R174 (4 dropped-sub-expr fixes incl. TS `as`/`satisfies`, + 3 stale-scope doc fixes) → R175✓ R176✓ (streak 2, RELEASABLE). 3.3.0: body matrix → Go (`structure_go.py` + 45-case oracle) → R177–R178 (2 doc-scope fixes; zero code defects) → R179✓ R180✓ (streak 2, RELEASABLE). 3.4.0: body matrix → Rust (`structure_rust.py` + 38-case oracle) → R181 (1 real defect: match-arm guards dropped, fixed + oracle-pinned) → R182✓ R183✓ full-diversity on a frozen HEAD (streak 2, gate met, RELEASABLE). 3.5.0: → C/C++ (R184–R192). 3.6.0: → Java + C# (`structure_java.py`/`structure_csharp.py`); C# exposed the float-rounding oracle blind spot latent in all 7 + a repeated-for-field-children class → hardened predicate to exact fingerprint-equality → R193–R197 fixes → R198✓ R199✓ (RELEASABLE). **3.7.0: → Ruby + PHP + Bash — completes all 12 languages; a long unbounded grind closed ≈a dozen esoteric advisory drops mostly latent in already-shipped frontends, each closed matrix-wide + oracle-pinned (comment-trivia positional picks, repeated-field reads, no-flow-arm initializers, exception selectors, decorator args, C# interpolation alignment) → R200✓ R201✓ on frozen HEAD 8d9b95f (streak 2, gate met, RELEASABLE)**. **3.8.0: §5c phase 1 — the layered code-property graph (`model.Layer` + `structure.vfg_source` across all 12 langs + `get_matrix(layer="expression")` drill-down; graph_diff = two-layer diff; on-demand, advisory, no schema change) → R202✓ R203✓ on frozen HEAD 0dae62c (streak 2, gate met, RELEASABLE)**. **3.9.0: §5c phase 2 — the STATEMENT / PDG layer (`structure.pdg_source` + `get_matrix(layer="statement")`, Python-only, advisory) → R204 → R205 (✗ determinism: set-iteration made cells `PYTHONHASHSEED`-dependent; fixed + subprocess test) → R206✓ R207 (✗ cosmetic layer-order) R208✓ R209 (✗ cosmetic layer-order — closed class-wide) R210 (✗ real: `match`/`case` bodies dropped from the PDG; fixed + oracle-pinned) R211 (✗ last cosmetic layer-order + opus's exhaustive compound-completeness hunt proving match was the only gap) → R212✓ R213✓ on frozen HEAD ad7ff46 (streak 2, gate met, RELEASABLE)**. **3.10.0: §5c sweep phase 3 — the STATEMENT layer learns the JS family (`structure_js.pdg_source`, js/ts/tsx; `get_matrix(layer="statement")` dispatches Python + JS) → R214 (✗ real: `with`-body dropped — block-bearing statement not descended; fixed class-wide + a cosmetic docstring nit) R215✓ R216 (✗ LOW: `typeof x` in a TS type position leaked a false data read; fixed + a cosmetic wording nit) → R217✓ R218✓ on frozen HEAD b488617 (streak 2, gate met, RELEASABLE)**. **3.11.0: §5c sweep language 3 — the STATEMENT layer learns Go (`structure_go.pdg_source`) → R219 (✗ LOW: type-switch `case <T>:` types leaked as spurious nodes; fixed class-wide + oracle test) → R220✓ R221✓ on frozen HEAD 37cd045 (streak 2, gate met, RELEASABLE)**. **3.12.0: §5c sweep language 4 — the STATEMENT layer learns Rust (`structure_rust.pdg_source`; expression-oriented value-position folding) → the longest grind of the sweep, R222–R238: 13 real defects, all ONE class (the read/write projection reading a non-value token / pattern binding, or dropping a consumed read, vs the VFG sibling) — self receivers, value-position control/block bodies, let-else, let-chains, macro names, struct-shorthand + if-let/while-let pattern bindings, loop/block labels, const/static, lifetime + type turbofish, closed uniformly in BOTH the PDG and VFG builders. R228 ADDED a white-box VFG-vs-PDG differential oracle (generated depth-2 value-position wrapper corpus; VFG-reads ⟹ PDG-reads + binding-reaches-use + no-spurious-read families) that regresses the whole class at once → R237✓ R238✓ on frozen HEAD 00792d1 (streak 2, gate met, RELEASABLE)**. **3.13.0: §5c sweep language 5 — the STATEMENT layer learns C/C++ (`structure_cpp.pdg_source`; statement-oriented, one walker for both C and C++). The differential oracle + type/label/field guards were **front-loaded with panel 1**, turning Rust's 17-panel grind into **6 real defects across 11 panels (R239–R249)**: parenthesized-RMW store dropped, GNU statement-expression fold, lambda init-capture, then the two that slipped to R245/R246 — a **type-position VFG over-read** (`g<v>()`/`decltype(v)` read by the VFG, dropped by the correct PDG) and a **structured-binding VFG under-read** (`auto [a,b]=v; use(a)`), i.e. the same VFG-vs-PDG divergence in composed positions the corpus hadn't yet generated, both closed by mirroring the PDG into the VFG + widening the oracle → R248✓ R249✓ on frozen HEAD 9575760 (streak 2, gate met, RELEASABLE)**. **3.14.0: §5c sweep language 6 — the STATEMENT layer learns Java (`structure_java.pdg_source`; statement-oriented, methods keyed by the dotted enclosing-type chain). The differential oracle + type/method-name/field/label guards were front-loaded WITH panel 1 and the read-projection mirrors the VFG `ev`/`bind` node-for-node, so Java became the FIRST sweep language to ship with ZERO code defects — 3 panels (R250–R252), the only finding a one-line docstring nit → R251✓ R252✓ on frozen HEAD 35eeb58 (streak 2, gate met, RELEASABLE)**. **3.15.0: §5c sweep language 7 — the STATEMENT layer learns C# (`structure_csharp.pdg_source`; statement-oriented, mirrors the VFG ev/bind node-for-node). Front-loaded like Java → the SECOND consecutive language to ship with ZERO code defects across its panels (R253–R254); the one substantive find was a pre-existing C# VFG bug (`_do_var_declaration` dropped a bare-identifier copy `int r = v;`) surfaced by mirroring and fixed in BOTH builders; opus falsification ran 317,057 fuzz cases with 0 divergences → R253✓ R254✓ on frozen HEAD ee66402 (streak 2, gate met, RELEASABLE)**. **3.16.0: §5c sweep language 8 — the STATEMENT layer learns Ruby (`structure_ruby.pdg_source`; EXPRESSION-oriented like Rust, value-position control folds, mirrors the VFG ev/bind/_do node-for-node). The front-loading template survived the hard tier: 3 panels / 1 real defect (a `case/in` guard field the VFG's generic fallback covered but the PDG hand-enumeration dropped, R255) vs Rust's 17/13 for the same shape pre-oracle; opus re-cert ran 61 curated + 28,000 fuzz cases with 0 divergences → R256✓ R257✓ on frozen HEAD b3bf7a9 (streak 2, gate met, RELEASABLE)**. **3.17.0: §5c sweep language 9 — the STATEMENT layer learns PHP (`structure_php.pdg_source`; statement-oriented, mirrors the VFG ev/bind node-for-node). Preceded by a grammar-reconciliation probe (tree-sitter emits `member_call_expression`/`nullsafe_member_call_expression`, routed through the shared generic fallback in BOTH builders; `scoped_call`/`function_call` hit the explicit CALL handler) so the two builders stay in lock-step incl. the symmetric gaps (`foreach $k=>$v` pair binds nothing, `Foo::$x` opaque freevar, member NAME unread even for dynamic `$o->$v`). Front-loaded like Java/C# → the THIRD consecutive language to ship with ZERO code defects (2 panels R258–R259, 0 findings); opus falsification ran ~86,000 cases (61k first panel + 24.9k re-cert, incl. 4,000 multi-param differential) with 0 VFG⟹PDG divergences → R258✓ R259✓ on frozen HEAD 20732f1 (streak 2, gate met, RELEASABLE)**. **3.18.0: §5c sweep language 10 — the STATEMENT layer learns Bash, the FINAL language → the sweep now covers EVERY body-matrix language (`structure_bash.pdg_source`; command-oriented outlier — shell functions have NO parameter list, so ENTRY carries no params, mirroring the VFG which seeds no PARAM nodes). The differential oracle is SEEDED via a first `v=$SEED` assignment (node 0 = SEED's FREE node in the VFG; node 1 = the seed Assign in the PDG). Grammar-probed the literal-vs-dynamic command-name split (a literal command name is a free callee, never a var read). Front-loaded → 2 clean panels / 0 code defects; the one finding was a doc LOW (stale README status header at v3.16.0, fixed). opus falsification ran ~86,000 cases (39.9k first panel + 24k + 21.9k re-certs) with 0 VFG⟹PDG divergences, node-1 attribution proven sound, three symmetric under-reads (${#v}/v+=x/extglob) confirmed non-divergent → R261✓ R262✓ on frozen HEAD a9ac0e9 (streak 2, gate met, RELEASABLE). §5c STATEMENT-layer sweep COMPLETE across all 10 sweep-languages / 12 body-matrix languages** |
| Dogfood (self) | find_stale advisory-only (no false-dead) · holes 0 |
| Verdict | **Consolidated into the v2.2.0 milestone release** (the cardinal sweep across all 10 languages + the #70–#89 follow-up backlog; no API/schema change, `find_stale` strictly more precise). 1.0.0–2.1.26 RELEASED/releasable (maintainer tags); 2.1.26 closed the per-language cardinal sweep. **2.1.27–2.1.31 close the post-sweep cardinal-safe follow-up backlog (#70–#89)** — all RELEASABLE, awaiting the maintainer's manual tags: **2.1.27** JS/TS exported-object shorthand incl. `as const`/`satisfies` (#74); **2.1.28** TS `#private`-via-`this.#m()` + dynamic-keyed class methods (#76/#78); **2.1.29** Python subscripted-Protocol/ABC + bodyless abstract interface methods (#70/#86); **2.1.30** C/C++ struct used only as a type (#89); **2.1.31** Bash `declare -fx`/`-f -x` / `typeset -fx` export + `time { … }` recall (#73). The rest of #70–#89 were resolved without code change or documented as deliberate cardinal-safe boundaries (#71/#72/#77/#79/#81/#82/#83/#84/#87/#88) or are coverage-only (#85). **2.2.1** then fixed the `PROMPT_COMMAND=fn` half of that gap (#95) — full-diversity panels R147–R148 clean (the generic `var=fn; $var` indirection and the `PROMPT_COMMAND+=fn` append form stay deferred cardinal-safe recall gaps). GitHub issues #18–#22 (v1.0.4-era) verified already fixed in shipped code and closeable. |

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

### 2.1.19 — `const X = class {…}` class-expression declarator (#80)

A class expression bound to a const (`export const Widget = class extends Component {
render(){ helper() } }`) was never modeled — the `variable_declarator` branch handled
arrow/function/generator/object values but not `class`/`class_expression` — so a helper called
only from its methods was flagged dead. Closed by adding a class branch that mirrors the
`assignment_expression` class handling (CLASS node, INHERITS edges, body walk, `exported` rescue,
round-3/4 fn-scope gating). Now at parity with a regular `class X {}`.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R119 | 3 | ✓ | round 1. opus+haiku clean with full parity tables vs regular `class X {}` (const-class is frequently *safer* in fn-scope — gates methods to the class rather than orphaning). sonnet's lone finding `@Injectable() const Service = class{}` is **invalid TypeScript** (decorating a const → parse-ERROR node, decorator unreachable) → out of scope, tracked LOW #82; the valid decorator patterns (method decorators inside, framework-base `extends`) work at parity. |
| R120 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus+haiku clean (haiku 64 custom + 475 pytest; opus const-extends-const / regular-extends-const / cross-file INHERITS / mixins / re-export). sonnet clean on the cardinal invariant across 20 scenarios; one LOW *cardinal-safe* precision note (a fn-nested const-class over-roots its genuinely-dead methods) — by design (the round-3/4 over-rooting, required by the gating regression). |

The 2.1.19 lesson: **a const-bound class expression is a class, so give it the class treatment.**
The fix is a near-verbatim mirror of the `assignment_expression` class branch; the diverse panel's
job here was parity verification (run the regular-`class` control for every fixture and report only
a delta). The one "delta" found was the const-class being *more* cardinal-safe than a regular nested
class — the right direction.

### 2.1.20 — JS/TS object & class literals in EXPRESSION positions (#75)

The broad close-out of the JS/TS object/class extraction line (after 2.1.11 method shorthand, 2.1.18
`_object_members`, 2.1.19 `const X = class {…}`). An object or class literal reached only through an
*expression shape* — a call argument (`register({ onInit(){ helper() } })`, `Object.freeze({…})`),
an array element (`[ {…} ]`), a ternary / `||` / `??` branch, an IIFE return (`(() => ({…}))()`), a
sequence (`(init(), {…})`), or a chained/parenthesized assignment (`const r = m.exports = {…}`) —
had its members invisible. Two distinct failures fed the same cardinal: a `variable_declarator` whose
value was such a shape was **swallowed** (no `else`, so the wrapping call/array/ternary was never
descended), while a bare-statement form descended generically and minted the member **unrooted** (the
"round-11" cardinal that the 2.1.18 effort's deliberate no-else had guarded against). Closed by
routing every generically-reached `object` through `_object_members` and every anonymous
`class`/`class_expression` through a class model (with a `body`-field guard against the bare `class`
keyword token), then re-enabling a now-safe descending `else` on the declarator.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R121 | 3 | ✓ | round 1. All three FINDINGS: none. opus built a **base-vs-HEAD differential harness** and gave a structural proof the change is reachability-MONOTONIC and purely additive (only appends nodes/edges/roles; synthesized `<obj@L_C>`/`<class@L_C>` ids cannot collide with real ids; HEAD *fixes* pre-existing cardinals, introduces none). sonnet 35 framework fixtures (createSlice/defineComponent/Express/RTK/Vue), haiku 18+. Two cardinal-safe non-bugs noted: getter/setter sharing a synthesized id (over-rooting only) and bare arrow/function in expression position (pre-existing #75-family recall gap → tracked **#83**). |
| R122 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** Fresh angles. opus: multi-file homonym resolution over-approximates to ALL same-named candidates (a synthetic `<obj@..>.go` never evicts a real `Foo.go`); a getter/setter id-collision loses a node *row* but not its call *edges* (reachability keys adjacency by edge `src`, requiring only `dst` to exist) so nothing real goes stale; positional per-file ids cannot collide with real symbols. sonnet 25+ scenarios (Angular `@Component`/NgRx/Pinia `this`-dispatch/class-field initializers/default-param objects/JSX/40-symbol mixed live+dead — all 5 genuinely-dead still flag, 20+ live retained); the 2 apparent violations confirmed PRE-EXISTING on base (function-reference-as-argument liveness, #83). haiku 100+ cases incl. empty/spread-only/deeply-nested. |

The 2.1.20 lesson: **the "no else" was a placeholder, not a verdict.** The 2.1.18 declarator branch
deliberately refused to descend into expression-shaped values because raw generic descent mints an
object's `method_definition`s as unrooted module-scope nodes — escalating a recall gap into a cardinal
(round 11). The right fix was not to keep swallowing but to make descent *safe*: intercept the literal
at the point generic descent reaches it and route it through proper member rooting, so the `else` can
finally descend. The diverse panel's value this round was the **structural / differential** argument
(opus's monotonicity proof + base-vs-HEAD harness) over fixture enumeration — the strongest evidence
that an *additive* change cannot introduce a false-dead.

### 2.1.21 — Go method value / method expression references (#49, cobra dogfood)

An **unexported** Go method reached only as a *method value* (`reg(v.run)`), *method expression*
(`use(t.run)`), or struct-literal field value (`cfg{onRun: v.run}`) was flagged dead. These are
references, not calls: `_direct_calls` sees only `v.run()` call sites, and `_direct_refs` collected
`identifier`/`type_identifier`/`constant`/`name` nodes but not the selector's `field_identifier`, so
the method got no inbound edge. (Capitalized/exported methods are rooted as public API — that masked
the gap until it was probed with unexported receivers, the methodical lesson: *test the unexported
surface, because the exported one is auto-live.*) Closed by emitting the trailing `field` name of a
Go `selector_expression` as a by-name REFERENCES edge in `_direct_refs` (Go-scoped — that node type
is unique to the Go grammar).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R123 | 3 | ✓ | round 1. All three FINDINGS: none. opus structural proof — the branch is strictly additive and `find_stale` is weight-agnostic boolean BFS, so it can only over-root (cardinal-safe); base-commit dump confirms HEAD fixes a real false-dead. sonnet 24 framework fixtures (cobra/gin/k8s/goroutine/defer/embedding/dispatch-map/25-symbol mixed live+dead); haiku 68. Genuinely-dead unexported method still flags; no crash. |
| R124 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus confirmed Go-uniqueness of `selector_expression` across all 12 grammars (no cross-language effect), streaming==in-memory parity, CALLS/REFERENCES double-dedup, base-vs-HEAD differential. sonnet quantified the over-rooting blast radius (a struct-field read shares the selector shape, so a dead function whose name *exactly* collides with a struct field is kept live — synthetic worst case 22/25; cardinal-SAFE precision-over-recall, bounded to exact-name matches, strictly better than the pre-fix false-dead) → precision follow-up **#84**. haiku 66 crash/edge cases clean. |

The 2.1.21 lesson: **a reference is not a call, and a method value is a reference.** The selector
field that names a method handed off as a value never appears in the call scan; emitting it as a
REFERENCES edge (the same closing move the Python `_direct_names` / the JS bare-name refs use) keeps
the live target live. The diverse panel's value this round was twofold: opus's *weight-agnostic-BFS*
structural proof (an additive edge change cannot strand a live node), and sonnet's *blast-radius
quantification* — naming the precision cost precisely (exact-name field/function collisions) and
scoping the safe-but-real recall loss into its own follow-up rather than letting it block a
cardinal-clean release.

### 2.1.22 — same-name method-overload role clobber (#61, store-level, all languages)

Two same-name method **overloads** (`void f()` / `void f(int)` in Java/C#/C++) collapse to one node
id (`Class.f` — the extractor doesn't put arity in the id). `Store.add_node` used `INSERT OR REPLACE`,
so the **last-written** overload's row won outright and **clobbered the earlier overload's roles**: a
public-API method (`exported`) overloaded with a private same-name helper declared *after* it, or a
framework-callback overload (`@PostConstruct`/`@Test`) followed by a plain one, lost its only root and
was confidently flagged dead though live. The failure was declaration-order-dependent. Closed by
upserting with `ON CONFLICT(id) DO UPDATE` that **unions** the colliding rows' roles — a rooting role
is never dropped. The fix is store-level, so it covers C#/C++ overloads, not only Java.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R125 | 3 | ✓ | round 1. All three FINDINGS: none (cardinal). opus structural proof — roles strictly additive + liveness MONOTONIC in roles (no role gates out, audited `detect`/`_live_set`/`_stale_candidates`) + non-role columns identical to old REPLACE; base-vs-HEAD differential. sonnet 16 framework fixtures (JUnit/Spring/servlet/builder/C# `[Fact]`/C++ `operator()`), both declaration orders, and quantified the bounded cardinal-safe precision masking (1 dead overload masked per rooted same-id sibling, never leaks wider — inherent to the arity-less id scheme, since adding arity would risk wrong-overload call resolution = a *new* cardinal). haiku Java/C#/Go/Python, streaming+in-memory, 50+ overloads. |
| R126 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus attacked the proof on five fronts and it held: no role gates out; the post-extraction seed passes all run on in-memory frozensets BEFORE `add_node` (never on the joined string); every joined-string reader splits-and-dedups; no role is a substring of another (LIKE-safe); no `roles == value` exact-match exists; `kind`/`is_stub` newest-row can only move a node OUT of candidacy (safe); streaming+incremental byte-parity. sonnet 15+ fixtures (duplicated-role strings, `runtime` carry through `replace_file`, mixed-language repo, all downstream ops crash-free, truly-dead private still flags). haiku 33 (stub/kind collisions, 100-overload stress, SQL-injection attempt). |

The 2.1.22 lesson: **when two defs must share an id, merge their liveness signal — never let last-writer-wins decide it.** The id scheme deliberately omits arity (so a call `f(x)` resolves to *the* method `f` without arity inference — adding arity to ids would risk resolving a call to the wrong overload and flagging the real one dead, a worse cardinal). Given that merge, the node row must UNION the rooting signal, not replace it. The diverse panel's value this round was opus's *monotonicity* proof (an additive role change cannot strand a live node, because no role gates out) — the strongest possible evidence for a store-level change — backed by sonnet's enumeration that no role is a substring of another and the bounded precision cost.

### 2.1.23 — Java anonymous-inner-class override in a class-scope initializer (#62)

An anonymous inner class (`new Base(){ … }`) has no name, so its overriding method can never be
resolved by a `Class.method` by-name call — it is invoked only polymorphically through the base type.
Inside a method body the enclosing-function containment edge already keeps the override live; but in a
**field / static / instance initializer** (class scope, no enclosing function) nothing rooted it, so a
non-`public` override — and the private helper it alone calls — was flagged dead though live. (Public
overrides were masked by the `exported` role; the gap surfaced on `protected`/package-private
overrides of a custom abstract base.) Closed by rooting a def that sits directly in an anonymous class
body (`class_body` child of `object_creation_expression`) as `callback` when at class scope; the
in-method case stays containment-gated, preserving its precision.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R127 | 3 | ✓ | round 1. All three FINDINGS: none (cardinal). opus structural proof (additive/monotonic, cross-checked vs #61's no-role-gates-out) + 165k-invocation crash fuzz (0 exceptions) + base-vs-HEAD differential + streaming parity. sonnet 15 framework fixtures (Swing/`Runnable`/`Comparator`/`TimerTask`/custom base) + quantified the bounded cardinal-safe precision masking (~1 dead private per class-scope anon class, never leaks to named classes). haiku 12 crash cases incl. enum constants. → precision/under-rooting follow-up **#87** (anon dead-member masking + enum-constant-body override gap). |
| R128 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus attacked the proof on fresh corners and it held: lambda-in-field (a Java lambda is not a def scope, so `enclosing_func` stays None → correct rooting, no flip), incremental `replace_file` byte-equal to a fresh reindex across edits, streaming parity on a 2101-file tree, base-vs-HEAD strict-SUBSET (numeric monotonicity), enum-constant bodies unchanged from base. sonnet all downstream ops crash-free + masking bounded to anon-class members + no conflict with `_seed_callback_roles`/exported seeding. haiku 200+ fuzz files + 5-deep nesting. |

The 2.1.23 lesson: **an anonymous class has no name, so name-resolution can never reach its
overrides — they must be rooted structurally.** The two reachability paths for a nameless override
are the containment edge (in a method body) and polymorphic dispatch (everywhere); only the former
was modeled, leaving class-scope initializers orphaned. Rooting anon-class members `callback` at
class scope closes it, gated by `enclosing_func is None` so the in-method case keeps its precise
containment gating. The diverse panel again leaned on the *monotonicity* proof (additive role ⇒ can't
strand a live node) and a large crash-fuzz, with sonnet bounding the precision cost.

### 2.1.24 — C/C++ function called only inside a `#define` macro body (#59)

A function called or named *only* inside a preprocessor macro body — `#define LOG(m) log_impl(m)`, a
function-pointer macro `#define DEFAULT handler`, a helper-wrapping macro — was confidently flagged
dead. Tree-sitter parses a macro body as a single raw-text `preproc_arg`, so the call inside it is
invisible to the AST call scan. Closed by `_macro_body_ref_names` (a text-scan of macro bodies, the
direct analogue of the `EXPORT_SYMBOL` scan) rooting matching project C/C++ F/M nodes `callback`,
project-wide across the unified C/C++ bucket. The first review round drove two refinements in: splice
`\<newline>` line continuations before scanning (a continuation splitting an identifier was read as
two fragments, missing the target — a found cardinal-recall gap), and exclude the macro's own
`preproc_params` from the body scan.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R129 | 3 | ✓ | round 1 on the FINAL code. All three FINDINGS: none (cardinal). opus structural proof (additive/monotonic, base-vs-HEAD differential) + crash hunt (20k-deep nesting, garbage bytes, `#undef`/conditionals). sonnet attributed precision cleanly: #59 body-token over-rooting vs the SEPARATE pre-existing #88 module-walk param-reference over-rooting (distinguished by the `callback` role). haiku 200+ fuzz files. Cross-language gate holds; streaming parity. (The pre-fix round-1 that found the line-continuation + param-precision issues is folded into this fix, not counted as a clean panel.) |
| R130 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus: incremental `replace_file`/reindex (no stale-state leak), streaming byte-parity on a ≥2000-file tree, real-world `util.h` (X-macros/`container_of`/`##`), #59-vs-#88 boundary nailed by disabling the pass in-process, monotonicity gained=0, crash corners (1MB body, recursive `#define A A`). sonnet multi-file C library + all downstream ops crash-free + variadic macros + C++ `::`-qualified names + quantified both precision sources (~2/fixture each). haiku 250+ fuzz + independently pinpointed #88's source (`_module_uses` `preproc_params`, ~line 1890). |

The 2.1.24 lesson: **scan the text the grammar refuses to parse — but scan it the way the
preprocessor would.** The macro body is opaque `preproc_arg` text, so a byte-scan (the EXPORT_SYMBOL
move) is the right tool; the round-1 panel's value was catching that a faithful scan must also splice
line continuations (as the preprocessor does) and exclude parameter placeholders, and — sharply —
*attributing* the residual param over-rooting to a pre-existing, separate mechanism (`_module_uses`
walking `preproc_params`, #88) rather than to this change, by keying on the `callback` role. Knowing
which code owns a symptom is as valuable as the fix.

### 2.1.25 — C/C++ function-pointer table / vtable promotion (#69)

A C/C++ function whose address is taken in a global function-pointer table (`int (*ops[])(int) =
{op_a, op_b}`), a plugin/vtable struct, a designated-initializer table, or a scalar (`cb h =
handler`) is invoked indirectly through that global — possibly in a different translation unit via
`extern`. Globals aren't graph nodes, so the cross-TU use is untrackable, and the address-taken
functions were false-flagged dead when their TU had no entry point (the passive registration-unit
pattern). Closed by `_c_global_init_fn_refs` rooting matching project C/C++ F/M nodes `callback`,
matching the `initializer_list` node directly (the dialect common denominator — C++ mis-parses
`int (*tab[])() = {…}` as an `expression_statement`).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R131 | 3 | ✓ | round 1 on the final code. All three FINDINGS: none. The pre-fix round-1 (sonnet) had quantified a precision regression — the scan collected designated-init FIELD names (`.open`/`.read`/`.free`, the commonest C function names) — fixed by collecting only `identifier` (the value), not `field_identifier`. This round verified it: opus directly REFUTED the under-rooting risk (function-pointer values are always `identifier`; only designators/member-components dropped — base-vs-HEAD confirms all values retained); sonnet quantified the win (file_operations 12 spurious → 0); haiku fuzz. |
| R132 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus incremental + streaming (2102 files) + real-world driver/registry + monotonicity gained=0 + crash sweep (1MB / depth-6000 / recursive typedefs). sonnet driver project + rooting-pass interaction (all additive unions, no conflict) + downstream ops crash-free + 3 cardinal-safe precision residuals quantified. haiku 350+ fuzz + cross-language gate. |

The 2.1.25 lesson: **the over-approximation has a floor you must not cross.** The panel pushed two
ways at once — sonnet found field-designator over-collection (precision, fixed by dropping
`field_identifier`), but the *opposite* tightening it later suggested (skipping C++ lambda bodies)
would have been cardinal-UNSAFE: a global lambda body is not walked by any def pass, so its callees
are live *only* because the initializer scan collects them — skip it and a function called only in a
*used* global lambda goes false-dead. Knowing which direction is the cardinal-safe one (collect more,
never less, for indirect-dispatch text the grammar doesn't model) is the whole discipline. The
collect-`identifier`-only fix threads it: it drops non-value designators (pure precision) while
keeping every value and every body callee (cardinal-safety preserved).

### 2.1.26 — JS/TS implicit-dispatch class members (#54) — closes the cardinal sweep

A JS/TS class member the runtime invokes *implicitly* is never reached by a plain `obj.method()`
by-name call, so the by-name resolver finds no caller and — in a non-exported (but instantiated)
class — the member and the private helpers it alone calls were confidently flagged dead though live.
Three forms: a well-known-Symbol computed key (`[Symbol.iterator]`/`[Symbol.asyncIterator]`/
`[Symbol.toPrimitive]`/`[Symbol.hasInstance]`/`[Symbol.toStringTag]`, run by `for…of`/spread/`+`
coercion/`instanceof`/`Object.prototype.toString`); a `get`/`set` accessor (run by a property
read/write, which the graph models as a member access, not a call); and a serialization/coercion hook
by name (`toJSON` via `JSON.stringify`, `toString`/`valueOf` via string & numeric coercion). Closed by
`_is_js_implicit_dispatch_method` (a `computed_property_name` containing `Symbol.`, a `get`/`set`
child node, or a name in `{toJSON, toString, valueOf}`), language-gated to javascript/typescript/tsx,
rooting the member `callback`. Exported-class members were already rescued by
`_seed_exported_class_methods`, so the gap surfaced on non-exported classes.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R133 | 3 | ✓ | round 1 on the final code. All three FINDINGS: none. opus: rooting purely additive (`roles.add` only, never deletes; Node id/kind independent of roles), helper cannot raise on malformed/unicode/empty-child nodes, single call site, streaming==in-memory, plain uncalled method still flags dead. sonnet (empiricist, 14 fixtures across .ts/.tsx/.js/.jsx/.mjs): every branch keeps member+private callee live, genuinely-dead members still flag, no crash. haiku (42 crash-robustness checks): empty/malformed bodies, non-UTF-8 bytes, backwards byte ranges, `Symbol.` substring false-positives — all over-root-or-no-op, zero crashes. |
| R134 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE; closes the entire per-language cardinal sweep.** opus (interaction angles): adding `callback` can't change `cid`/kind; same-name collapse (`get value()`/`set value()`, `[Symbol.iterator]` vs plain method) unions roles via #61 `ON CONFLICT` so no live member/callee lost under collision (streaming + in-memory); helper `name` never diverges from the qual; TS overload sigs+impl collapse correctly; 396 regression tests pass. sonnet (deep transitive chains): 4-deep iterator→_a→_b→_c, getter→private→module-free-fn, toString→static-field-init→builder, cross-class iterator→helper — all live; dead methods in a live-iterator class still flag. haiku (stress): 50+ implicit-dispatch members, deep nesting, huge `Symbol.` expressions, mixed valid/broken tree, empty/ctor-only classes — no crash. |

The 2.1.26 close: **the same precision-over-recall instinct that built `_seed_exported_class_methods`,
generalized to the implicit-dispatch surface that rescue couldn't reach.** With this the per-language
cardinal sweep is complete — v2.1.1 through v2.1.26 each shipped one gated cardinal fix across all ten
supported languages (Python, JS/TS, Go, Rust, C/C++, C#, Java, PHP, Ruby, Bash). What remains (#70–#89)
is entirely cardinal-*safe* precision/coverage follow-ups — over-rooting to tighten and recall gaps to
widen, none of which can flag live code dead — all deferred.

### 2.1.27 — JS/TS shorthand member of an exported object (#74) — first of the follow-up backlog

The post-sweep backlog (#70–#89) is cardinal-*safe* in aggregate, but a few entries are themselves
pre-existing cardinals worth closing. #74 is one: a function referenced via object-literal SHORTHAND
in an exported object — `export const handlers = { onClick, onHover }` — is public API (an importer
reaches `handlers.onClick`), but a `shorthand_property_identifier` is never modeled as a reference, so
the named function and the private helpers it alone calls were false-flagged dead. The CJS/default
forms (`module.exports = { onClick }`, `export default { onClick }`) were already handled by
`_reexport_names`; the named-const-export form was the gap. Closed by collecting an exported
declaration's object-literal member names (shorthand idents + `pair` value idents) into the same
reexport→`exported` path.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R135 | 3 | ✗ | initial panel — round 1 clean (3/3) but round 2 haiku found an IN-SCOPE CARDINAL: the canonical TS idiom `export const handlers = { onClick } as const` wraps the object in an `as_expression` (also `satisfies`/parens), not a bare `object`, so the new branch's `v.type == "object"` check missed it and the member stayed dead. **Fixed** by unwrapping TS value wrappers via `_unwrap_ts_value` on both the named-export object and the `module.exports = …` RHS (mirroring the declarator/assignment def branches). Streak reset. |
| R136 | 3 | ✓ | fresh round 1 on the final code. All three FINDINGS: none. opus: unwrap additive, `v.type=="object"` gate rejects unwrapped non-objects (`export const x = f as const` correctly NOT collected), monotonic role-union, streaming parity, crash sweep clean. sonnet: 9 real-world scenarios (as const / satisfies / parens / NestJS mixed / CJS-wrapped / double-chained / spread / non-object / precision) all correct. haiku: as-const cardinal confirmed fixed, 407 regression tests pass. |
| R137 | 3 | ✓ | fresh round 2 — **streak 2, gate met, RELEASABLE.** opus: all four export forms coexist + root independently; dual-name/double-export over-root only; DOGFOOD streaming==in-memory parity, 0 public-API symbols newly flagged. sonnet: 5-level transitive across 3 files live, cross-language isolation, 7 dead siblings still flag (no blanket-root). haiku: full suite 543 + oracles 27 green; pathological inputs (1000 members, unicode/keyword keys, chained wrappers) no crash. |

The 2.1.27 lesson: **the panel earns its keep on the follow-up backlog too.** Round 1 was clean on the
plain shorthand, but the most common real-world shape (`{ … } as const` on a TS handler object) only
surfaced in round 2 — and the fix was the one-line unwrap the def branches already used. #77 (underscore
member-assign), #81, and #83 in the same cluster were resolved WITHOUT a code change: #77 is a deliberate
precision boundary (a statically-named underscore member that is actually called resolves by name —
verified), and #81/#83 are already covered for exported modules via `_module_uses` + def-body recursion.

### 2.1.28 — TS class-member resolution cardinals (#76, #78)

Two ways a genuinely-live TS class method (and the private helpers it alone calls) was confidently
flagged dead. #76: a `#private` method called via `this.#m()` — `_name_of` and `_callee` both
returned None for `private_property_identifier`, so the `#m` def was dropped (body unwalked → helper
dead) and the call edge was lost. #78: a class method with a dynamic key (string `"k"(){}`,
computed-string `["k"](){}`, numeric `42(){}`) — `_name_of` returned None, dropping the def. Closed
by (a) adding `private_property_identifier` to `_trailing_id`'s leaf set so the def name and the call
site resolve to the same `#m`, and (b) modeling a dynamic-keyed class method as a node (named from
the raw key) with its body walked, rooted `callback` (reachable only via a dynamic subscript). A
`#private` method resolves by name, so an UNCALLED one still flags dead (precision preserved); the
dynamic-key rooting is the class-body analogue of the object-literal computed-key rule.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R138 | 3 | ✓ | round 1. opus: the `_trailing_id` change is strictly ADDITIVE (former None → `#name`, no misroute; `#`-prefix unique so no public/private collision; inert at other call sites by grammar); #78 modeling additive with id-collision safety (`["run"]` vs `run` distinct). sonnet (12 fixtures): private state machines, static/getter/arrow-field `#private`, Redux `["ACTION"]` maps, #54 Symbol coexistence — all live, uncalled #private + plain-dead still flag. haiku: crash sweep (hex/unicode/escaped keys, 200+ dyn methods, broken syntax) zero crashes, 441 tests. |
| R139 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus: other `_trailing_id`/`_name_of` consumers unaffected; a `#private` in an exported class is correctly NOT rescued (so uncalled still flags); `.`/`::` dyn-key ids cause no crash + no under-rooting; DOGFOOD streaming==in-memory parity; 550 tests. sonnet: 6-file library, inheritance, plain-JS ES2022, regression bundle (#54/#74) no regression, free-fn-via-#private transitive live. haiku: 550 + oracles 27; downstream ops crash-free on #private/dyn-key nodes; reindex idempotent. |

A pre-existing, cardinal-SAFE note the panel surfaced: two classes with an identically-named
`#private` method share a by-name bucket, so a `this.#m()` call resolves AMBIGUOUSLY to both — an
over-root in the safe direction, and a net correctness *gain* over the prior state (where the
`#private` node didn't exist at all and the called method was a false-dead).

### 2.1.29 — Python abstract / Protocol interface methods (#70, #86)

A bodyless interface-method declaration is a contract fulfilled by overrides, never called by name —
so it should not be reported as dead code. Two compounding gaps: `_is_abstract_class` used `_name_of`
on each base, returning None for an `ast.Subscript` (`Protocol[T]`, `Generic[T]`), so a subscripted-
base abstract class wasn't recognized (#70); and a bodyless abstract/Protocol method had no root, so
an uncalled one was flagged dead (#86). Closed by (a) `_is_abstract_class` unwrapping subscripted
bases via `_base_name`, and (b) rooting a bodyless `_is_abstract` method `callback`. Cardinal-safe: a
concrete (real-body) uncalled method in an ABC and its private helper still flag dead.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R140 | 3 | ✓ | round 1. opus: change is strictly ADDITIVE to liveness (`is_stub`/`is_abstract` never feed reachability — verified by grep; `_is_abstract_class` still matches only the literal Protocol/ABC set); #71 deferral confirmed cardinal-safe. sonnet (6 fixture suites): Protocol[T]/ABC,Generic[T]/ABCMeta stubs spared, concrete-dead still flags, non-abstract subscripted bases unaffected, find_stale not a no-op. haiku: 24 edge cases no crash, parity. |
| R141 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus: inheritance+override end-to-end, cross-file Protocol, #51 subscripted-base INHERITS coexistence, no metric inflation, DOGFOOD streaming==in-memory parity. sonnet: plugin ABC architecture, DI Protocols, @runtime_checkable/@abstractmethod @property/abstract @classmethod, precision intact. haiku: 553 + oracles 27; find_holes correctly spares bodyless abstract methods (contracts, not holes); reindex idempotent; downstream ops crash-free. |

#71 (`_framework_classes` name-collision over-masks) was resolved WITHOUT a code change: over-masking
is the cardinal-SAFE direction (it keeps a possibly-framework-reachable class live), and tightening
it would *un-mask* — risking a live framework-only-reachable class flagged dead. A deliberate
precision boundary, left intentionally; the panel independently confirmed the reasoning.

### 2.1.30 — C/C++ struct used only as a type (#89)

A struct/union/enum used only as a TYPE — `struct Config g;`, `void f(struct Config *p)`, a field or
return type — is a live data-model definition, but C/C++ has no constructor call to edge it, so it had
no inbound edge and was false-flagged dead. Closed by `_c_type_ref_names`, which collects the names of
bodyless (type-use) struct/union/enum/class specifiers (the body-bearing definition is skipped); the
post-pass roots every matching C/C++ class node `callback`. Project-wide, scoped to C/C++.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R142 | 3 | ✓ | round 1. opus: post-pass purely additive, body-is-None guard excludes the definition, cross-language gate blocks Java/C#/TS homonyms, `_trailing_id` safe on qualified/template/scoped-enum shapes, cross-file rooting works. sonnet (6 fixtures): header/source split, field/typedef/enum/union uses, C++ class/enum-class/namespace/template, #69+#59 coexistence, dead C functions still caught. haiku: 12 crash/edge cases no crash, 422+27 green, parity. |
| R143 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus (mechanism): rooting a class `callback` does NOT rescue its methods (LIVENESS_RELATIONS excludes CONTAINS; `_seed_exported*` key on `exported`), so a dead method of a type-rooted class still flags; .h/.cpp/.c unification; sizeof/cast/fn-ptr-typedef uses rooted; 2000-struct + recursive typedef reindex 0.3s; parity. sonnet: realistic C allocator + C++ inheritance/templates, precision boundary, transitive type liveness, mixed-language isolation. haiku: 558 + oracles 27, downstream ops crash-free, idempotent, 9 pathological scenarios. |

Cardinal-safe boundaries the panel confirmed (left intentionally): **#88** (the C/C++ module walk
treats a `#define` parameter name as a reference) is over-rooting — un-masking would risk a cardinal.
**#87** (enum-constant-body overrides) is already handled — a Java enum constant's override and its
helpers stay live; the companion class-scope anon-class over-rooting is the cardinal-safe direction. A
recall backstop gap noted by the panel (cardinal-safe, deferred): a C++ bare type name without the
`struct`/`class` keyword (`Config c;`) parses as `type_identifier`, so `_c_type_ref_names` doesn't
collect it — but when the using code is live the `REFERENCES` edge already rescues the type, and when
the using code is dead the type being dead is correct, so it is never a false-dead.

### 2.1.31 — Bash function-export recall (#73); closes the #70–#89 backlog

A function exported for subshells via `declare -fx` / `typeset -fx` (the ksh/bash spellings of
`export -f`) or invoked under `time { fn; }` was flagged dead though live. `_bash_export_decl` now
accumulates the `f`/`x` flag characters across the leading flag words (so the split spellings
`declare -f -x` / `declare -x -f` work as well as combined `declare -fx`); `_bash_time_target` takes
the first bare-identifier word (robust to any brace token, incl. nested `time { { fn; }; }`).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R144 | 3 | ✗ | initial panel — R1 all clean, but R2 found TWO in-scope cardinals: opus, split flags `declare -f -x` (the combined-token check missed the split spelling); haiku, nested `time { { fn; }; }` (first word arg is the multi-char token `{ {`, which an exact `{`/`}` skip missed). **Fixed** by accumulating `f`/`x` across flag words and taking the first identifier word. Streak reset. |
| R145 | 3 | ✓ | fresh round 1 on the final code. opus verified against bash POSIX options-then-operands semantics that NO valid export form places the flag after the name (so the leading-run accumulation has no under-rooting gap) + 3000-case fuzz; sonnet 9 groups; haiku both cardinals fixed + triple-nested time. |
| R146 | 3 | ✓ | fresh round 2 — **streak 2, gate met, RELEASABLE; closes the #70–#89 follow-up backlog.** opus comprehensive all-mechanisms script + cross-file + 10-file dogfood parity + deferral re-confirmation; sonnet 7 fixtures + over-root guard; haiku 571+27 + 2000-fn stress + idempotency. |

The 2.1.31 lesson: **the panel earns its keep even on the last release.** A clean round 1 on the
obvious forms, then round 2 surfaced two valid-bash spellings the first fix didn't cover
(`declare -f -x` split flags; nested `time { { } }`). The corrected accumulate-across-flag-words
model was then validated against actual bash option-parsing semantics — there is no valid form that
places the exporting flag after the name, so the recall is complete, not just patched for the cases
seen. With this, the post-sweep follow-up backlog (#70–#89) is closed: #70/#74/#76/#78/#86/#89/#73
fixed behind the full gate; the remainder resolved without code change, documented as deliberate
cardinal-safe boundaries, or coverage-only.

### 2.2.1 — Bash `PROMPT_COMMAND=fn` hook recall (#95)

A function registered via `PROMPT_COMMAND=fn` (also `="fn1; fn2"` / `export PROMPT_COMMAND=fn`) is
run by the interactive shell before each prompt — a runtime hook with no textual call site — so it
was false-flagged dead. `_bash_prompt_command_ref` now roots the function name(s) in a
`PROMPT_COMMAND` assignment; scoped to that well-known variable, cardinal-safe (only a name that
resolves to a project function is rooted). The generic `var=fn; $var` indirection and the
`PROMPT_COMMAND+=fn` append form stay deferred cardinal-safe recall gaps.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R147 | 3 | ✓ | round 1. opus: end-to-end additivity proof (append-only into `calls`→`_ref`, no-ops on unresolved names, cannot under-root/abort), `PROMPT_COMMAND`-only scoping, crash-free on array/empty/expansion/append values, parity. sonnet: chained-dotfile + full v2.1.x bash coexistence + hoisting/reset precision, 79/79 bash tests. haiku: 7 core + 15 crash cases, 466 tests. |
| R148 | 3 | ✓ | round 2 — **streak 2, gate met, RELEASABLE.** opus: 4-level PROMPT_COMMAND-only chain live, no crash on array/non-UTF-8/`${:-}`/heredoc/comment/PC-named-fn, prior bash fixes intact, no metric inflation, parity. sonnet: starship-style dispatcher + `${PROMPT_COMMAND:+…}` preserve-existing, cross-language isolation (bash `render` rooted, same-named Python `render` stays dead), multi-file sourced-hook, exact precision; 575/575. haiku: downstream ops crash-free, idempotent, 18KB/50-fn stress. |

A process note worth recording: 2.2.1 initially shipped on the deterministic gate alone (ruff/mypy/
pytest/oracles/mutation + both-directions regression) — the two-round panel was added retroactively
when the maintainer asked whether it had run. The bar is *two clean full-diversity panels per
code-changing release*; an additive cardinal-safe recall fix is not an exemption from it.

## v2.3.0 — shared `tarjan_scc` core (the first research-driven refactor)

The body-level structural-clone research (`research/02-body-matrix`, cross-validated by `03-pdg`)
surfaced a byte-identical Tarjan SCC core duplicated in `reach.strongly_connected_components` and
`dataloop._tarjan` — invisible to the call-graph clone detector. Extracted into
`core/_scc.py:tarjan_scc`; pure de-duplication, behaviour-preserving. This is the first time the
project's own research line drove a production change.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R149 | 3 | ~ | round 1. opus: CLEAN — strongconnect byte-identical to both old copies; find_stale uses `reachable_from` BFS, structurally insulated from SCC. sonnet: 1 LOW (dataloop `node_count` over-count — PRE-EXISTING, safe-direction) + 1 NIT (no *direct* tests for the primitive). haiku: 12 edge cases pass; theoretical >536M-node `setrecursionlimit` overflow is pre-existing → NIT. **Action: added `tests/test_scc.py`.** |
| R150 | 3 | ✗ | round 2 — **gap found.** opus + haiku CLEAN (no state bleed, defaultdict read-only via `.get`, docs accurate). sonnet: **650-graph differential = 0 divergences (code correct)** but a MEDIUM test-coverage gap — the unit tests didn't pin the `elif w in on_stack` cross-edge guard. **Action: added cross-edge + defaultdict tests; mutation now 6/6 killed by `test_scc.py` alone.** |
| R151 | 3 | ✓ | clean-cycle round 1 — CLEAN (code frozen since R150; only tests grew). opus reproduced the guard mutant and confirmed the new test kills it. sonnet: **800-graph differential = 0 divergences**, mutation 6/6. haiku: new tests load-bearing, docstrings accurate. |
| R152 | 3 | ✓ | clean-cycle round 2 — **streak 2, gate met, RELEASABLE.** opus: full 4-file diff behaviour-preserving, cardinal unaffected, all CHANGELOG/notes claims true; lone NIT (pre-existing CHANGELOG 2.2.x ordering) **fixed during finalize**. sonnet: deterministic gate re-run (590/27 + ruff/mypy), readiness RELEASABLE (RRS 94.8), live `scan`/`find_data_loops` coherent. haiku: version 2.3.0 consistent (metadata-derived), no stale refs/debug. |

Process note: unlike 2.2.1, the panels ran *before* shipping. The two-round bar did real work here —
R150 caught a genuine unit-test gap (the `on_stack` guard) in tests I had just written; the fix was
verified by a standalone mutation run, then re-confirmed by a fresh two clean rounds (R151/R152).
The code itself was differentially proven equivalent across 800+ random graphs.

## v3.0.0 — the intra-procedural body matrix (the first MAJOR since the streaming rewrite)

The matrix-as-oracle research promoted to `src/`: `core/structure.py` (a per-Python-function
value-flow fingerprint — operations + control points, data + control edges, copy propagation,
order/name-invariant Weisfeiler-Lehman kernel), `find_similar(mode="structure")`, and a body-aware
`graph_diff`. Advisory and read-only by construction — none of it feeds `find_stale`, so the
cardinal rule is structurally unaffected. A genuinely hard analysis shipped as a safe approximation;
the panels did the hardening.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R153 | opus · sonnet | ✗ | round 1. opus: **HIGH** qualname-collision (body matching keyed by bare name collapsed same-named funcs → key by full node id) + MEDIUM empty-body phantom + MEDIUM operator-blind labels + LOW sim>1.0. sonnet: MEDIUM corrupt-db traceback + MEDIUM **alien sqlite silently migrated/mutated** → read-only probe before `Store()`. |
| R154 | 3 | ✗ | opus LOW: URI-reserved-char (`?`/`#`) db path falsely refused → `Path().resolve().as_uri()`. |
| R155 | 3 | ✗ | opus MEDIUM: control-flow-nested defs dropped (visit didn't descend non-def nodes). sonnet NIT: `body_threshold` not exposed → threaded through. |
| R156 | 3 | ✗ | opus **CRITICAL**: a deep-but-valid expression overflowed the recursive walk → traceback from the advisory layer; `fingerprint_source` now catches `RecursionError`/`SyntaxError`/`ValueError`. |
| R157 | 3 | ✓ | clean — but predates the R158 change; doesn't count. |
| R158 | 3 | ✗ | opus MEDIUM: `match` statements dropped → explicit branch + `_match_captures()` + a **generic fallback** so a future syntax addition can't silently vanish. |
| R159 | 3 | ✓ | clean — but predates the R160 fix. Between R159/R160 the **white-box completeness oracle** (`tests/oracles/test_structure_completeness.py`) was added; it deterministically caught two further drops the panels had missed (Subscript index, Dict keys). |
| R160 | 3 | ✗ | opus **HIGH**: `graph_diff` passed the read-only probe but then opened a valid **older-schema** index with `Store()`, whose `_migrate` (ALTER TABLE + commit) mutated the user's file on disk → fix: diff over a **temp copy**, never the original. |
| R161 | 3 | ✓ | clean-cycle round (post temp-copy). Lone NIT: docs said `--other-db` but the CLI is a positional `OTHER_DB` — fixed during finalize. haiku flagged a stale editable-install `dist-info` (2.1.31) = ENV artifact, not a source defect. |
| R162 | 3 | ✗ | opus **MEDIUM**: AugAssign on a bare Name dropped the target read-edge (`x += e` ≠ `x = x + e`, sim 0.50) — broke the documented temp-var/reorder invariance, internally inconsistent (attr/subscript targets kept it). Fix: `read_target()` Load-semantics lookup. sonnet corrected the doc'd graphdiff mutation count 8/8 → 9/9. |
| R163 | 3 | ✗ | sonnet ran the mutation meta-oracle over `structure.py` and surfaced **test gaps** (not code bugs): MEDIUM `_VFG.link`'s edge-add branch un-pinned — nothing asserted that two functions with an identical node bag but different wiring score low (the *core* property); LOW the match-guard link + the with-as bind likewise un-pinned. Added 3 tests → mutation now **`structure.py` 15/15 + `graphdiff` 9/9**. |
| R164 | 3 | ✓ | **final clean-cycle round 1** on a frozen HEAD. opus+sonnet CLEAN (gate 698/85, mutation 15/15+9/9, cardinal byte-identical, dogfood). haiku reported a "CRITICAL" mutation failure that was a **reviewer methodology error** — it ran the wrong kill-signal (the dogfood oracle / a partial suite, not the modules' own unit tests); re-verified false. Surfaced a real doc-precision NIT (name the kill-signal in the docs), folded into finalize. |
| R165 | 3 | ✓ | **final clean-cycle round 2** — streak 2, gate met, **RELEASABLE** (RRS 93.3). All three used the correct kill-signals (15/15 + 9/9). opus: 2000-case structural fuzz + edge cases, zero crashes; cardinal `find_stale` byte-identical. sonnet: dogfood + read-only stress + determinism clean. haiku: counts/version/CLI/claims all true. |

Process notes (this release earned several): (1) **two genuinely-frozen clean rounds** — when the
maintainer asked whether the panels review the same code, the answer is yes *within* a round, and
any real finding resets the streak; R164/R165 ran on one byte-frozen commit with nothing committed
between launch and both reports. (2) The **completeness oracle** replaced panel whack-a-mole for the
"dropped node type" class — it found two bugs seven panel rounds had missed, and an introspective
guard fails if a future Python adds a node type. (3) **Mutation survivors are findings**: R163's
gaps were in tests I'd just written; closing them (and naming the kill-signal in the docs after a
reviewer ran the wrong one) is the same shape as the v2.3.0 `on_stack` lesson. (4) A surfaced but
**out-of-scope** item — `similar.py`'s optional semantic/dense path has pre-existing mutation gaps
(documented optional-dep blind spot) — was parked in `docs/IDEAS.md` §5d rather than chased.

## v3.1.0 — mutation-harden `find_similar`'s dense path + a clearer README (test-only)

A small, safe follow-up to v3.0.0: no runtime source change. It closes the one parked hardening
item (`docs/IDEAS.md` §5d) — the optional dense/`model2vec` retrieval path in `core/similar.py` had
~15 surviving mutants — and makes the README lead with *what stitchgraph delivers*.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R166 | 3 | ✗ | round 1 (HEAD 46b7d5f). No product defect — opus brute-forced the 3 mutation survivors and confirmed them genuinely equivalent; haiku verified docs/counts. sonnet saw a one-off two-test failure on a cold run → traced to a **test-determinism gap** (LOW, test-only): `find_similar`'s module-global dense backend (`_EMBEDDER`/`_M2V_TRIED`) wasn't isolated per test, so the token-path `reverse=` mutant was killed only by a *leaked* embedder (the shared fixture's query ties at 0.447), masking a coverage hole. **Fix:** autouse isolation fixture + a tie-free token-ranking test + localized model2vec import-failure (`sys.modules[None]`) and a subprocess latch check. |
| R167 | 3 | ✓ | final clean-cycle round 1 (frozen HEAD 7bffb12). opus: suite 703×2 + the previously-flaky pair ×3 all deterministic; `similar.py` mutation **29/32 run twice, identical survivors** (11/23/28); all three re-confirmed equivalent. sonnet: gate 703/85, mutation 29/32 + 15/15 + 9/9, dogfood + cardinal byte-identical. haiku: counts/version/README all true. |
| R168 | 3 | ✓ | final clean-cycle round 2 — **streak 2, gate met, RELEASABLE.** opus probed fresh angles (subprocess robustness, fixture not masking path-switching, token-test tie-robustness) — all negative. sonnet: 703×2, mutation 29/32 + 15/15 + 9/9. haiku: docs consistent; its lone "bare-mypy error in tests" note was a methodology slip — mypy is configured `files=["src"]`, so tests are out of the type-check gate by design and the gate is clean. |

Process notes: (1) the **mutation meta-oracle found a real test gap a panel hadn't** — `similar.py`'s
dense ranking was only *apparently* covered; a mutant killed by leaked global state is not covered,
and making the run deterministic (the isolation fixture) exposed and then closed it. The same lesson
as v2.3.0's `on_stack` gap, one layer deeper. (2) Two review **artifacts** were correctly run down
to non-defects: a cold-run suite flake and transient ruff/oracle failures, both from running
`mutate.py` (which writes to `src/` under the editable install) *concurrently* with `pytest`/`ruff`
— a reviewer-methodology hazard now called out in the panel briefs (run the meta-oracle
sequentially). (3) The 3 residual `similar.py` survivors are **justified-equivalent**, documented in
the `tests/test_similar.py` docstring and re-verified by adversarial distinguishing-input attempts in
both clean rounds — "kill or justify," with the justification itself audited.

## v3.2.0 — the body matrix learns JS / TS / TSX (the first language beyond Python)

The intra-procedural body matrix (v3.0.0, Python) gains a second frontend: `core/structure_js.py`, a
tree-sitter CST walker for the JS/TS/TSX family that emits the **same** `_VFG` the language-neutral
core fingerprints. Wired into `find_similar(mode="structure")` and `graph_diff(body=True)` by
auto-sniffing the snippet/file language and ranking **same-language only** (a body fingerprint's
topology tracks its extractor, so cross-language scores are not comparable — and a node id maps to
exactly one file = one language, so the comparison is same-language by construction). Advisory and
read-only: it never feeds `find_stale`, so the cardinal rule is structurally untouchable. The
**completeness oracle** recipe ported too: a 51-case metamorphic battery (`helper()` CALL vs `0`
CONST in every value-bearing position) + a generic fallback so an unhandled node can't silently
vanish — the tree-sitter introspective guard doesn't port (no small enumerable supertype set), so the
fallback is the structural "nothing vanishes" guarantee. tree-sitter is an optional extra; without it
`fingerprint_source` returns `{}` and the JS layer adds nothing (Python stays stdlib-only).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R169 | 3 | ✗ | dev round 1. opus **MEDIUM**: JS `new_expression` read only the `function` field (None for `new C()` / `new (expr)()`) → constructor sub-expression dropped; fixed to read `function` OR `constructor` + a New-callee oracle case. (The oracle also caught a `template_string` `${…}` substitution drop mid-development.) haiku MEDIUM: stale "Python-only" body-matrix scope in the `find_similar_structure` docstring. |
| R170 | 3 | ✗ | dev round 2. haiku **MEDIUM**: a second stale scope mention — the `find_similar` dispatcher docstring still called the structure mode Python-only. Fixed. |
| R171 | 3 | ✗ | dev round 3. haiku **MEDIUM**: residual "Python-only"/"(Python)" claims in the README (core-capabilities bullet, "what it delivers", operations table). Fixed; an exhaustive grep then confirmed **zero** residual stale body-matrix scope mentions. |
| R172 | 3 | ✗ | dev round 4. opus **LOW**: JS `x += e` / `x++` didn't rebind the target, so later reads saw the pre-mutation value — diverged from the Python layer's `x += e ≡ x = x + e` invariance. Fixed via bind-back + base-operator normalization (`+=`→`+`) + a rebind-like-explicit oracle test. (sonnet observed the switch case-value double-walk → R173.) Cardinal-safe (advisory metric only). |
| R173 | 3 | ✗ | dev round 5 (frozen HEAD 8ab121b). sonnet **LOW**: the switch case-value skip compared by node identity (`st is val`) — a no-op, because tree-sitter returns a fresh wrapper per access, so the case value was walked twice (spurious ARGUMENTS / PROPERTY_IDENTIFIER nodes). Fixed by comparing **byte spans** + a Switch-case-value oracle case + a no-double-walk regression test. Cardinal-safe. |
| R174 | 3 | ✗ | clean-cycle attempt (frozen HEAD 8ab121b). sonnet + haiku CLEAN (gate 758/136, mutation 15/15 + 9/9, cardinal byte-identical, zero stale scope). opus **MEDIUM** (deep hunt): TS `as`/`satisfies` casts sat in the `_TRANSPARENT` set that descends to the **last** named child — but their children are `[operand, type]`, so it kept the no-flow type node and **dropped the operand's value flow** (`helper() as number` collapsed to `0 as number`, sim 1.0), making `graph_diff` miss a real body change and `find_similar` mis-rank. The inverse of every documented approximation — a genuine dropped-sub-expression. Fixed via `_CAST_OPERAND_FIRST` (first child for as/satisfies, last for `(x)`/`x!`/`<T>x`) + 3 TS-cast metamorphic oracle cases + a TS-cast-no-value-flow invariant test. Streak resets. |
| R175 | 3 | ✓ | **final clean-cycle round 1** (frozen HEAD 81e4916). opus (deepest pass): ~60 constructs beyond the 51-case oracle (nested/as-const casts, decorators, optional chaining, computed members, tagged templates, JSX/tsx, destructuring, spreads, sequence) — every value-bearing position discriminates; the three sim==1.0 cases (param-default, object-shorthand-method, class-field-init) are documented approximations that match Python exactly; the R174 cast fix holds across nested casts. sonnet: gate **762/140**, ruff+mypy clean, mutation 15/15 + 9/9, clean degradation without the extra, cardinal byte-identical. haiku: counts 762/140/51 + mutation + version 3.2.0 + zero stale scope all verified. |
| R176 | 3 | ✓ | **final clean-cycle round 2 — streak 2, gate met, RELEASABLE.** Independent re-run, not a rerun of R175. opus: fresh-angle dropped-sub-expression hunt on realistic multi-statement async/generator/try-catch-finally functions + private-method `this`-dispatch — all discriminate; the three sim==1.0 cases (subscript-**write** index, destructuring defaults, name/position invariance) mirror Python identically; cardinal `find_stale` byte-identical before/after `find_similar`+`graph_diff` on a mixed Python+JS+TS index (scratch db kept outside the source tree). sonnet: gate 762/140, mutation 15/15 + 9/9, cross-language comparison structurally impossible, HEAD unchanged. haiku: 762/140/51 + mutation + version + zero stale scope re-verified. |

Process notes: (1) the **completeness-oracle-first recipe paid off again** — three of the four real
dev-round findings (New-callee, template-substitution, and the class the oracle is built for) are
dropped-sub-expressions the metamorphic battery catches deterministically; each fix added an oracle
case, so the guard is now denser for the next language. (2) The deepest defect (R174 `as`/`satisfies`)
slipped through five rounds because the *clean* panels (sonnet/haiku) don't hunt dropped-sub-expr and
the lone TS test only exercised parameter/return annotations — **opus's deep-hunt angle is the one
that finds this class**; keep it in every body-matrix panel. (3) Same `mutate.py`-concurrency hazard
as v3.1.0, plus a new one: an *interrupted* `mutate.py` left an `ast.unparse`'d mutant of
`structure_js.py` on disk (its `finally` restore was bypassed by a worker SIGKILL) — reviewers are now
told **not to run `mutate.py` on `structure_js.py`** until that tool is made interrupt-safe.

## v3.3.0 — the body matrix learns Go (language 2 of the §5b sweep)

`core/structure_go.py` — a tree-sitter Go walker emitting the **same** `_VFG` the Python and JS
frontends do, reusing the WL kernel. Bare-name qualnames (a method keys as `Method`, not `T.Method`)
and nested `func` literals opaque — matching the Go extractor's granularity. Seeds the method
receiver + named results as parameters. Same advisory/read-only/same-language-ranking contract; the
45-case completeness oracle (`tests/oracles/test_structure_go_completeness.py`) drove the walker.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R177 | 3 | ✗ | clean-cycle attempt 1 (frozen HEAD 4c3d5a2). opus + sonnet CLEAN (gate **816/190**, mutation 15/15 + 9/9, cardinal byte-identical; opus's ~37-probe deep hunt found only documented Python-parity approximations — the one sim==1.0, `m[k]=e` index-**write** key, matches Python's SETITEM exactly). haiku **MEDIUM**: `LIMITATIONS.md` still scoped the body matrix to "Python + JS/TS/TSX … not the other 8 languages" — a now-false claim post-Go. Fixed → "Python + JS/TS/TSX + Go". Streak resets (doc-accuracy, same class as v3.2.0 R169–R171). |
| R178 | 3 | ✗ | clean-cycle attempt 2 (frozen HEAD 8c52061). opus + sonnet CLEAN (gate 816/190, mutation 15/15 + 9/9, cardinal: the genuinely-orphan Go func is never flagged by the body matrix). haiku **MEDIUM** (exhaustive scope grep; its first run was cut off by a worker restart, the rerun caught more): the `graph_diff()` **function** docstring still said "Python or JS/TS" (missing Go **and** TSX — the module docstring had been updated but not the function's) and README:34 said "JS/TS + Go" (missing TSX). Both fixed to canonical. Streak resets. |
| R179 | 3 | ✓ | **final clean-cycle round 1** (frozen HEAD 30c35a4). opus: whole-function fidelity on idiomatic Go incl. **generics** — a generic body fingerprints non-empty, `helper()` vs `0` inside it differs, type params/constraints (`comparable`/`~int`) carry no spurious flow, generic ≡ concrete; renamed clone ranks first, data-flow change caught. sonnet: gate 816/190, mutation 15/15 + 9/9, clean degradation, cross-language misroute empirically impossible. haiku: exhaustive scope grep, both R178 fixes re-verified, counts 816/190/45. |
| R180 | 3 | ✓ | **final clean-cycle round 2 — streak 2, gate met, RELEASABLE.** opus: parse-robustness hunt over 30 edge inputs (syntax error, empty/package-only, unicode idents, 2000–3000-deep expression chains, lone surrogates) — `fingerprint_source` **never raised**, always returned a dict ({} or partial); deep nesting caught → `{}` (documented advisory degrade); cardinal byte-identical, no Go node ever in the stale set. sonnet: 816/190, mutation 15/15 + 9/9, HEAD unchanged. haiku: counts/version/scope all canonical. |

Process notes: (1) the completeness-oracle-first recipe made Go **cheaper than JS** — the walker passed
its 45-case battery on the first build and the panels found **no code defect at all** (only two
doc-scope misses). The bug taxonomy in `docs/BODY_MATRIX_LESSONS.md` is paying compounding dividends.
(2) Both findings this release were **documentation scope-consistency** (the `graph_diff` *function*
docstring and a README bullet lagging the module docstring) — a recurring tail when one representation
gains a language; the fix each time is a grep across *every* surface, not just the obvious ones.
(3) Generics were the one genuinely-new Go construct vs Python/JS; opus confirmed type parameters seed
no spurious value flow (the walker only reads `receiver`/`parameters`/`result`).

## v3.4.0 — the body matrix learns Rust (language 3 of the §5b sweep)

`core/structure_rust.py` — a tree-sitter Rust walker emitting the **same** `_VFG` the Python/JS/Go
frontends do. Rust is expression-oriented, so a block's **trailing expression** is its value (`{ x }`
≡ `{ return x; }`); `if`/`match`/`loop`/`while`/`for` are expressions; `?`/`&x`/`as`/ranges/tuples/
struct-literals carry operand flow (type carries none); macros are walked best-effort as token trees;
closures are opaque `NESTED` leaves; `self`/named-results seed like params. Qualname scheme matches
the Rust extractor: free functions bare, impl methods `Type.method`. The 38-case completeness oracle
drove the walker.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R181 | 3 | ✗ | clean-cycle attempt 1 (frozen HEAD 5e0152b). sonnet + haiku CLEAN (gate **862/232**, mutation 15/15 + 9/9, cardinal inviolable, exhaustive doc-scope grep clean incl. the `graph_diff` *function* docstring — the Go-release lesson applied up front). opus **MEDIUM** (deep hunt): match-arm **guards** (`pat if <cond> => …`) dropped their condition — the guard lives under the arm's `match_pattern.condition`, not the arm `value`, and the handler walked only `value`. `match v { n if expensive(n) => 1, _ => 0 }` collapsed to `n if true` (sim 1.0). Python captures the analogous `case x if …`; Rust alone dropped it; the oracle's `Match-arm` probed only the arm value. Fixed → walk the guard into the BRANCH + a `Match-guard` oracle case (battery 37→38). Advisory-only; cardinal never at risk. Streak resets. |
| R182 | 3 | ✓ | **final clean-cycle round 1** (frozen HEAD 1c52422). opus (fresh angle): `if let`/`while let`/`let else`, struct-update `..base`, deref/index assignment, tuple/struct binding sub-patterns, `?` chains, unsafe/async-move/trait-default/impl-Trait bodies — all discriminate; R181 match-guard fix verified live; the two sim==1.0 (closure-opaque, `a[i]=e` index-write) mirror Python exactly. sonnet: gate **863/233**, mutation 15/15 + 9/9, clean degradation, cross-language impossible. haiku: exhaustive scope grep, counts 863/233/38. |
| R183 | 3 | ✓ | **final clean-cycle round 2 — streak 2, gate met, RELEASABLE.** opus: parse-robustness + whole-function hunt (syntax error→partial, empty→{}, `todo!()`, 5000-deep→RecursionError→{} graceful degrade, unicode idents, iterator chains `.iter().map().filter().collect()` + `?`-propagation + match) — `fingerprint_source` never raised; renamed clone ranks first; data-flow changes caught (0.14–0.94); the lone sim==1.0 (commutative arg swap) is the documented WL position-invariance, identical in Python. sonnet: 863/233, mutation 15/15 + 9/9, HEAD unchanged. haiku: counts/version/scope all canonical. |

Process notes: (1) the curve ticked back **up** vs Go (1 real code defect vs 0) — exactly as predicted:
Rust's expression-oriented blocks + `match` guards are genuinely novel value-flow shapes, and the
match-**guard** is a position the metamorphic battery under-covered (it probed the arm *value*, not the
guard predicate). The deep-hunt reviewer (opus) is the one that finds this class; the fix always adds
the missing oracle case so it can't reopen. (2) The doc-scope-consistency tail **did not recur** this
release — applying the Go lesson (grep *every* surface incl. the `graph_diff` function docstring, not
just the module docstring) up front meant haiku found zero stale scope mentions in R181. The recurring
cost is payable once you know to look for it.

## v3.5.0 — the body matrix learns C and C++ (language 4 of the §5b sweep)

`core/structure_cpp.py` — one tree-sitter `cpp` walker for both C and C++ (the grammar is a superset),
emitting the **same** `_VFG` the Python/JS/Go/Rust frontends do. Statement-oriented (explicit
`return`); the function name lives *inside* the declarator (unwrap pointer/reference wrappers, take a
qualified_identifier's last component for out-of-line `Foo::m`); compound-assign / casts / `?:` /
`*p` / `&x` / `a[i]` carry operand flow; lambdas are opaque `NESTED` leaves; `sizeof`/`alignof`/
`decltype`/`noexcept` are unevaluated → CONST; the preprocessor is not expanded. The completeness
oracle (45 metamorphic cases + invariants) drove the walker. **The hardest language of the sweep so
far** — 9 real dropped-value-flow defects found and fixed across the panels, every one a C/C++-specific
node-shape the metamorphic battery had not yet probed; each fix added the missing oracle case.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R184 | 3 | ✓* | first clean-cycle attempt; opus value-flow CLEAN, sonnet gate/cardinal CLEAN, haiku one README file-list NIT (folded into finalize). |
| R185 | 3 | ✗ | opus **MEDIUM**: reference-return functions (`T& f()`, `V& grow()`) dropped — `reference_declarator` doesn't field-name its inner `function_declarator`, name-unwrap returned None, function never keyed. Fix: `_decl_child` fallback + oracle invariant. |
| R186 | 3 | ✗ | opus **MEDIUM**: constructor member-initializer-lists (`S(int x): n(compute(x))`) never walked — `field_initializer_list` is a SIBLING of the body, `_build_vfg` walked only `body`. Fix: evaluate each init as a member write + oracle case. |
| R187 | 3 | ✗ | opus **MEDIUM**: array-new size (`new T[helper()]`) dropped (lives under `new_declarator.length`, not `arguments`). **INCIDENT**: a reviewer was mistakenly asked to run `mutate.py` (not interrupt-safe); a concurrent run left `graphdiff.py` mutated and a stop-hook-prompted `git add -A` committed the mutant (body-divergence flipped `<`→`>=`). Traced across commits, restored byte-identical to canonical f188f97; the mutant was never validated by a passing full suite. Lesson: `mutate.py` runs strictly serially, never by a reviewer, never alongside git staging. |
| R187b | 3 | ✗ | analysis-only reviewers (no `mutate.py`). opus **2 MEDIUM**: C++17 `if`/`switch` init-statement + C++20 range-`for` init-statement dropped (`_strip_cond` discarded the `initializer` field); placement-`new (addr) T(…)` placement address dropped (separate `placement` field). Both fixed + oracle cases. |
| R188 | 3 | ✗ | opus **2 MEDIUM**: stack VLA size (`int arr[helper()]`) dropped (bind skipped the `array_declarator` `size` — inconsistent with the R187 heap-new fix); C++14 lambda init-capture (`[z = helper()]`) dropped (`_FUNC_NODES` returned a bare opaque leaf). Both fixed (lambda body stays opaque) + oracle cases. |
| R189 | 3 | ✓ | clean-cycle round (frozen HEAD 79ed271) — CLEAN. opus deep-hunt 60+ positions all discriminate; sonnet gate **920**, graphdiff `<`, cardinal/isolation/degradation; haiku 920/286/45-case battery, all scope surfaces canonical. (HEAD later advanced for the R190 fix, so this clean panel predates the final HEAD.) |
| R190 | 3 | ✗ | opus **LOW**: ctor/dtor function-try-block (`S() try : init {…} catch{}`) body + member-init dropped — a function-try-block has NO `body` field; the grammar nests them in an unnamed `try_statement`. A LOW (weight 1 < τ, narrow construct) but a genuine drop of the same class, so fixed for consistency (fall back to the `try_statement` child) + oracle invariant rather than documented. |
| R191 | 3 | ✓ | **final clean-cycle round 1** (frozen HEAD 33d274d, post function-try-block fix). opus ~90 positions all discriminate; FTB fix verified (destructor/free-function/multi-catch). Cosmetic LOW *noted, not a defect*: `noexcept(expr)` over-counts its operand via the generic fallback — conservative over-counting, the OPPOSITE of a dropped position. sonnet gate **921**, graphdiff `<`; haiku 921/287/45-case battery. |
| R192 | 3 | ✓ | **final clean-cycle round 2 — streak 2 on the final HEAD, gate met, RELEASABLE.** opus: fresh 30-position sweep (29/30 discriminate; lone sim 1.0 is the documented subscript-LHS-index Python-parity approx) + all 9 prior fixes re-confirmed + 20/20 robustness inputs never raised + whole-function realism (rename-clone 1.0, real change 0.69). sonnet 921, graphdiff `<`, HEAD stable; haiku counts/version/scope all canonical. |

Process notes: (1) the curve ticked **sharply up** vs Go (0) / Rust (1) — exactly as the lessons doc
predicted C/C++ would: pointers, the declarator-name-inside-the-declarator inversion, out-of-line
methods, function-try-blocks, VLAs, and C++14/17/20 init forms are a dense field of novel value-flow
shapes, and the opus deep-hunt is the reviewer that finds each one. The generic fallback kept every
unhandled node *visible* (nothing silently vanished structurally), but the metamorphic battery is what
proved each value-bearing position is actually *walked*. (2) The release also produced the sweep's
first **process** failure rather than a code defect: a `mutate.py` mutant reached two commits because a
panel reviewer ran the not-interrupt-safe mutator concurrently and a stop-hook prompted a commit
mid-run. The recovery was clean (the suite pins the body-divergence direction, so the mutant could
never have passed a full gate), and the standing rule is now explicit — the mutation meta-oracle is
run by the orchestrator alone, serially, never by a reviewer and never alongside git staging.

## v3.6.0 — the body matrix learns Java and C# (languages 5 & 6 of the §5b sweep)

`core/structure_java.py` + `core/structure_csharp.py` — two tree-sitter walkers emitting the **same**
`_VFG` the other frontends do. Both key by the dotted chain of enclosing TYPE names (package /
namespace excluded): Java `Outer.Inner.m` / `C.C`; C# `Calc.Compute` / `Calc.Calc` / local function
`Calc.Local.Inner`. The first release to land a *pair* in one MINOR. Two completeness oracles (Java +
C#) drove the walkers; both passed first-run, but the panels then turned the pair into a free
adversarial probe of the shared machinery — see the two cross-cutting wins below.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R193 | 3 | ✗ | opus(Java) CLEAN; sonnet CLEAN; opus(C#) **2 MEDIUM** + exposed a **meta-oracle weakness in all 7 languages**: the metamorphic check `similarity(a,b) < 1.0` could PASS on byte-identical fingerprints (cosine self-sim of a large WL vector rounds to 0.999…98 < 1.0). Hardened the predicate to exact fingerprint-equality everywhere; that surfaced a 3rd masked C# drop. Three C# fixes: `using (var r=e)` paren resource, `$"{…}"` interpolation holes, `new int[]{…}` element init. Other six re-verified clean under the stricter predicate. |
| R194 | 3 | ✗ | opus(C#) + opus(Java/cross-frontend) + sonnet CLEAN; haiku 2 stale scope strings (doc LOW, fixed). |
| R195 | 3 | ✗ | opus + sonnet CLEAN; haiku — the per-frontend module docstrings enumerated only predecessors (doc LOW). Resolved decisively by rephrasing all six to a non-enumerating future-proof form. |
| R196 | 3 | ✓ | final clean-cycle round 1 (HEAD 9417762). opus 70+ exact-inequality positions; sonnet gate 1024/388 + 7 oracle guards; haiku 20 enumerating surfaces all seven. |
| R197 | 3 | ✗ | **final-round-2 caught two NEW dropped positions** (this is why the 2nd confirmation panel exists). opus: comma-form `for` 2nd+ init/update (`for(…; i++, sink(x))`) dropped in BOTH frontends (grammars use REPEATED `update`/`init` field children; walker used `child_by_field_name` = first only); C# `catch (E e) when (filter)` predicate never walked. Both fixed + oracle cases; C/C++ & JS verified unaffected (single comma/sequence node). |
| R198 | 3 | ✓ | final clean-cycle round 1 on corrected HEAD ea410c3. opus re-verified R197 fixes + swept every repeated/positional field-child position; sonnet 1029/393; haiku all surfaces seven. |
| R199 | 3 | ✓ | **final clean-cycle round 2 — streak 2, gate met, RELEASABLE.** opus ~110 fresh exact-inequality probes all differ, all fixes re-confirmed, realism + robustness clean; sonnet 1029/393 all checks; haiku 1029/393/46/47/3.6.0 canonical. |

Process notes: (1) **Language diversity is a defect-finding signal for the shared kernel, not just
new per-language code** — exactly the model-diversity lesson, one level down. C# (a *new* language)
exposed the float-rounding oracle blind spot that had been latent in all seven oracles since v3.0.0,
and hardening it retroactively strengthened the five already-shipped languages. The
repeated-field-children defect found in Java/C# immediately triggered a cross-frontend audit (C/C++ &
JS confirmed safe). Net: the body matrix is *more* trustworthy after C# than after C/C++, in ways
unrelated to C#. (2) The completeness-oracle predicate is now **exact fingerprint inequality**, not a
`sim < 1.0` threshold — a permanent meta-oracle hardening. (3) The two real code-defect classes this
release were both **tree-sitter structural surprises** (positional unnamed-field children;
field-named-but-*repeated* children) — the kind the generic fallback can't catch and only a
value-bearing metamorphic probe surfaces.

## v3.7.0 — the body matrix learns Ruby, PHP and Bash (the final 3 of the §5b sweep — all 12 languages)

`core/structure_ruby.py` + `core/structure_php.py` + `core/structure_bash.py` — three more
tree-sitter walkers emitting the **same** `_VFG`, completing the intra-procedural body matrix across
all 12 indexed languages (Python via stdlib `ast` + 11 via tree-sitter). Ruby keys by dotted
module/class chain, PHP by class chain (namespace excluded), Bash is command-oriented. Three new
completeness oracles drove the walkers; as before, adding three languages turned the panels into a
free adversarial probe of the shared kernel — and this cycle was the most productive yet: roughly a
dozen esoteric, advisory-only value-flow drops surfaced, **most of them latent in the
already-shipped frontends**, each fixed and closed *matrix-wide* (not just in the language the panel
named) plus oracle-pinned.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| (build) | — | — | Ruby/PHP/Bash frontends + 3 oracles + wiring (find_similar/graph_diff) + dogfood; full gate green. |
| comment-trivia | 3 | ✗ | **comment nodes are named tree-sitter children**, so any *positional* `named_children[i]` / `[-1]` / "first non-body" pick can be displaced by a comment. Closed across all 8 tree-sitter frontends in two layers — per-operator positional picks, then the inline transparent-unwrap descents (`(x /*c*/)`, `await (x /*c*/)`, `f(x /*c*/)`, `d[i /*c*/]`) — via comment-skipping `_nc`/`_first`/`_last` helpers. Pinned by a 64-case comment-invariance battery. |
| repeated-field | 3 | ✗ | Ruby multi-value `when 1, helper()` dropped values past the first (`child_by_field_name` = first-only over a REPEATED `pattern` field). Audited the multi-value/multi-label arm + multi-declarator class across all 12; only Ruby leaked. |
| no-flow-arm init | 3 | ✗ | a declaration routed to a `pass`/skip arm but carrying an **initializer**: PHP `static $x = helper()`, then (panel-found twin) Rust local `const`/`static`. Audited a declaration-with-initializer in all 12 (incl. TS `enum` members, bash `local`/`declare`/`readonly` with `$(…)`); all walk it now. |
| exception selector | 3 | ✗ | runtime-evaluated exception selectors dropped: Ruby `rescue <expr>` (the `exceptions` field) and Python `except <expr>:` / `except*` (`handler.type`). The only two langs with evaluated selectors (others use static type names). |
| decorator args | 3 | ✗ | Python nested def/class `@deco(helper())` argument dropped — sibling of the enclosing-scope class (defaults/bases/keywords were walked; decorator-call args were not). JS/TS already walked them; Java/C#/PHP attribute args must be compile-time constants. |
| C# interpolation align | 3 | ✗ | C# `$"{v,helper()}"` alignment clause dropped (excluded wholesale with the literal `:format` clause). Walk the alignment expression, keep `:format`/brace excluded. Folded two stale doc counts. |
| R200 | 3 | ✓ | **clean-cycle round 1** (HEAD 8d9b95f). opus ~150 fresh exact-inequality constructs all discriminate (every surfaced drop a rigor violation or closed class); sonnet gate 1269 suite / 630 oracles + cardinal + live-matrix; haiku 8 oracle counts + 12-lang enumerations canonical. |
| R201 | 3 | ✓ | **clean-cycle round 2 — streak 2, gate met, RELEASABLE.** opus independent second certification could not falsify round 1 (every frontend's exotic positions probed; non-findings documented under the rigor rules); sonnet cardinal AST-verified + 630 oracles + ruff/mypy + matrix live (Py/Go/Ruby); HEAD frozen. |

Process notes: (1) **The maintainer's process correction mid-cycle — "I thought you checked the same
class across all languages" — is now the rule:** when a panel finds a defect, close its *entire
class matrix-wide within the same round* (probe the analogous construct in all 12 and fix every
instance) before re-panelling, rather than fixing only the named language and letting the next panel
surface the twins. The PHP-`static` → Rust-`const`/`static` miss is the cautionary example; every
later class (exception selectors, decorator args, interpolation alignment) was audited across all 12
the same round. (2) Ruby/PHP/Bash were a far richer kernel probe than the Java/C# pair: the
comment-trivia-positional-pick and no-flow-arm-initializer classes were **latent in frontends
shipped as far back as v3.2.0–v3.5.0** and only surfaced now — the body matrix is materially more
trustworthy across *all* languages after the final three than before. (3) The grind converged
asymptotically (≈1 esoteric advisory gap per round for many rounds) exactly as the maintainer
anticipated when choosing the unbounded grind; the two-consecutive-clean gate held the line until
the space was genuinely exhausted.

## v3.8.0 — the layered code-property graph (§5c phase 1): call ↔ expression drill-down

The 12-language body matrix (v3.0.0–v3.7.0) was an internal fingerprint input; v3.8.0 promotes it to
a first-class, drill-down-able **layer**. `model.Layer` (CALL / EXPRESSION; STATEMENT reserved);
`structure.vfg` / `vfg_source` (+ `vfg_source` on all 9 tree-sitter frontends) expose the per-function
value-flow graph — each frontend's `fingerprint_source` was refactored into a shared
`_walk(source, …, build)` so the fingerprint and the raw graph key **identically** by construction;
`get_matrix(layer="expression")` drills a single function's VFG on demand; `graph_diff` is documented
as the two-layer diff. On-demand only (no store schema change), advisory-only (never feeds
`find_stale`). The 8 tree-sitter frontends' `vfg_source` were added by 8 parallel same-recipe agents,
each self-verified, then oracle-pinned across all 12.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R202 | 3 | ✓ | clean-cycle round 1 (HEAD 0dae62c). opus exhaustive: layer-arg handling (case/None/non-str), call-layer byte-identity, every refusal + n=300/301 boundary, all 9 non-Python frontends + qualname parity, differential fuzz 12 langs × 25 inputs × 2 encodings (0 key mismatches / 0 raises), cardinal, registry/CLI/MCP. sonnet: reach.py + operations top-level import-clean (lazy structure import only inside `_expression_vfg`), full suite 1309, ruff+mypy, `find_stale` byte-identical before/after a drill. haiku: version 3.8.0 everywhere; new tests 7 + 33; oracles 663. |
| R203 | 3 | ✓ | **clean-cycle round 2 — streak 2, gate met, RELEASABLE.** opus (independent) verified `fingerprint_source` is bit-for-bit unchanged by the `_walk` refactor (independent WL recompute from `vfg_source`) across 12 langs, and a WHOLE-REPO invariant sweep — drilled all 637 of stitchgraph's own functions: 568 built / 69 clean refusals / **0 raises / 0 invariant violations**. sonnet: AST-verified operations has zero top-level structure imports; 663 oracles; a 30-function real-repo drill sample (0 crashes) + `find_stale` byte-identical before/after a 41-drill batch. |

Process notes: (1) **The refactor-then-fan-out shape.** Rather than hand-edit the same `vfg_source`
addition into 9 frontends, one reference implementation (structure_js.py) fixed the exact recipe, then
8 parallel agents applied it to the rest — each self-verifying key-parity + a well-formed graph + ruff.
A shared `_walk(build)` per frontend makes fingerprint/vfg key-drift impossible by construction (they
differ only in the terminal `build` lambda). (2) **The layer arrived cheap because the graph already
existed** — `graph_diff(body=True)` and `find_similar(mode="structure")` were already computing the
expression layer on demand; §5c mostly *named* it (`Layer`) and *exposed* it (`vfg_source` +
`get_matrix` drill-down), which is why v3.8.0 is a clean MINOR with no schema change and a 2-round
clean cycle on the first HEAD. (3) **On-demand was the right persistence call** — the whole-repo sweep
(637 functions drilled with zero indexer involvement) shows the expression layer scales as a
compute-on-read view; persisting deep edges would have fought the streaming indexer for no consumer
benefit.

## v3.9.0 — the layered code-property graph (§5c phase 2): the STATEMENT / PDG layer

Phase 1 (v3.8.0) added the EXPRESSION layer; v3.9.0 adds the middle depth — the **program-dependence
graph**. `structure.pdg` / `pdg_source` build a per-Python-function PDG: statement nodes + a synthetic
`ENTRY` carrying the parameters, control (`C`, nested-under-a-header) and data (`D`, a sequential
reaching-def) edges; reorder-invariant. `get_matrix(layer="statement")` drills a single function's PDG
on demand — Python-only so far (deep stdlib `ast`; other languages a future sweep), advisory, never
feeds `find_stale`. Promotes the validated `research/03-pdg/` prototype; keys identically to
`fingerprint_source`/`vfg_source` via the shared `_walk_functions`. `Layer.STATEMENT` is no longer
"reserved". This was a long grind — three real find→fix cycles plus a fully-closed cosmetic class.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R204 | 3 | ✓ | round 1 (HEAD 83e18a1). All three CLEAN — exhaustive PDG cert, gate 1317 passed, docs. (The determinism defect below was latent and uncaught here.) |
| R205 | 3 | ✗ | **opus caught STATEMENT-layer non-determinism**: `_build_pdg` emitted data edges by iterating a `set` of load names, so `pdg` edges / `get_matrix` cells were `PYTHONHASHSEED`-dependent — the CALL layer's byte-reproducibility guarantee regressed. Advisory-only, cardinal rule never at risk (WL fingerprints sort internally, so the oracle stayed green — which is why round 1 missed it). Fixed 6010687: `sorted(loads/stores)` at the root + `sorted(seen)` cells at the choke point + a cross-`PYTHONHASHSEED` subprocess regression test. |
| R206 | 3 | ✓ | post-fix (6010687→4b500f8). All CLEAN. opus proved the fix does real work (reverting the sort diverges into distinct hashes); sonnet 1317 passed + cardinal; and independently reproduced a test-hardening fix (4b500f8: the determinism guard now sets `PYTHONHASHSEED` per child env so it can't go inert under a seed-pinning CI). |
| R207 | 3 | ✗ | opus+sonnet CLEAN; **haiku** found a cosmetic layer-order nit (unknown-layer message `call\|expression\|statement` ≠ enum/depth order). Fixed 2da922b. |
| R208 | 3 | ✓ | All CLEAN (opus 223 fuzz inputs + 5-seed determinism + exact >300 boundary; sonnet gate+cardinal; haiku docs). |
| R209 | 3 | ✗ | opus+sonnet CLEAN (opus 4000+ fuzz, repo-wide key parity, cardinal digest byte-identical across 60 drills); **haiku** found another layer-order instance (IDEAS.md). Closed the ENTIRE class matrix-wide at 2a7c18d (enum docstring, get_matrix/_body_matrix docstrings, IDEAS.md). |
| R210 | 3 | ✗ | **opus found a real completeness defect**: `ast.Match`/`case` bodies were dropped from the PDG — `walk_block` descended only into `body/orelse/finalbody`+`handlers`, but Match sub-statements live in `cases[].body`, so case-body statements vanished and `header_names` misattributed case-body names to the Match node (the EXPRESSION layer already handled match — inconsistent). Fixed e861ebd: descend into `cases[].body`, exclude `cases` from the header. Match is the only such compound in Python's grammar — class closed. |
| R211 | 3 | ✗ | **opus CLEAN with an exhaustive structural-completeness hunt** — enumerated every `ast.stmt` compound programmatically; zero unwalked statement-bearing fields remain (match was the last gap). sonnet gate CLEAN. **haiku** found the last layer-order instance (README "Layered" bullet); closed class-wide (README + the final two in-code listings) at ad7ff46. |
| R212 | 3 | ✓ | **FINAL HEAD ad7ff46 — all three CLEAN.** opus full re-cert incl. the match fix (case bodies descend, 2000 fuzz, 4-seed determinism, exact boundary, cardinal byte-identical); sonnet 761 (targeted) passed + cardinal + byte-identical isolation on a match fn; haiku all docs consistent. Reviewed in-place (no `git checkout` of the shared tree). |
| R213 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus 6000+ fuzz + 4-seed determinism + cardinal airtight; sonnet targeted 761 + ruff/mypy + the 3 PDG tests, cardinal import-hygiene (reach.py closure never reaches structure/similar/graphdiff, empirically via `sys.modules`) + byte-identical `find_stale` across a statement drill on a match fn; haiku 11/11 docs consistent. |

Process notes: (1) **Two panels missed the determinism defect because the oracle was immune to it.**
`_wl_features` sorts each node's signature, so the WL fingerprint — and thus every reorder-invariance
test — is order-blind by construction; the non-determinism only surfaced in the *raw* `cells`/edge
list, which no test compared byte-wise until R205's opus probe. Lesson: an order-invariant oracle
cannot guard an order-sensitive output; the fix shipped its own cross-`PYTHONHASHSEED` subprocess test.
(2) **"Close the whole class, matrix-wide."** The layer-ordering nit recurred across R207/R209/R211
because each fix touched only the flagged instance; it stopped only once a repo-wide grep normalized
*every* current-state listing to `call → statement → expression` at once (historical CHANGELOG/
REVIEW_HISTORY/readiness records left intact). Same discipline that closed the §5b language matrix.
(3) **Completeness gaps hide in the one asymmetric AST field.** `ast.Match` is the sole Python compound
whose child statements live outside `body/orelse/finalbody/handlers` (in `cases[].body`), so it was the
one place `walk_block` under-walked; R211's fix was to *enumerate every compound* and prove no other
field is unwalked, rather than patch match alone. (4) **Shared-worktree hazard.** Reviewer subagents
that `git checkout <sha>` mutate the one working tree everyone shares (it detached HEAD mid-run once);
later panels were told to review in-place at the branch tip — cheaper and safer than worktree isolation
for read-only review.

## v3.10.0 — the STATEMENT layer learns the JS family (§5c sweep, language 2)

v3.9.0 shipped the program-dependence-graph layer for Python; v3.10.0 begins sweeping it to the
tree-sitter languages, starting with the JS family. `structure_js.pdg_source` / `_build_pdg` build a
per-function PDG from the tree-sitter tree, mirroring Python's `structure._build_pdg` (statement nodes
+ synthetic ENTRY carrying params; control 'C' / data 'D' edges via a sequential reaching-def;
reorder-invariant; nested functions opaque). `get_matrix(layer="statement")` now dispatches `.py` →
`structure` and js/ts/tsx → `structure_js`; other languages refuse with a supported-set message.
On-demand, advisory — never feeds `find_stale`.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R214 | 3 | ✗ | **opus caught a real completeness gap**: the JS PDG's generic `else` branch ran only `data_edges`, so a block-bearing statement not special-cased (the deprecated `with`) dropped its whole body — diverging from Python's generic `walk_block` and the JS EXPRESSION layer. Fixed fc89810: the else branch descends any nested `statement_block`/`else_clause` child through `block()`, closing the class (not just `with`) + oracle test. haiku: stale "Python-only" get_matrix/`structure.pdg` docstrings → fixed. sonnet CLEAN (769 passed). |
| R215 | 3 | ✓ | post-with-fix (fc89810). All CLEAN. opus verified the fix general (3599+ fuzz, every JS/TS statement kind, TS type positions, cardinal); sonnet 770 passed + byte-identical isolation; haiku docs. Superseded when R216 found a further defect. |
| R216 | 3 | ✗ | **opus caught a LOW precision defect**: `typeof x` in a TS TYPE position (`type_query`) leaked a false data read — a construct erased at runtime. Fixed b488617: `collect` returns at `type_query` (value-position `typeof` is a `unary_expression`, so the disambiguation is sound) + oracle test. haiku: README/IDEAS said "JS/TS" where the CHANGELOG/model say "the JS family (js/ts/tsx)" → normalized. sonnet CLEAN (770 passed). |
| R217 | 3 | ✓ | post-type_query-fix (b488617). All CLEAN. opus re-cert verified BOTH fixes general (4000 fuzz; exhaustive TS type-position probe confirming `type_query` is the only leak vector; with/block descent); sonnet 771 passed + cardinal + byte-identical; haiku docs (both the "Python-only" and "JS/TS" wording classes closed). |
| R218 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus 4000 fuzz × 3 langs + all statement kinds + exact >300 boundary + cardinal; sonnet 771 passed + ruff/mypy + import-hygiene + byte-identical JS isolation; haiku 7/7 docs. |

Process notes: (1) **The tree-sitter PDG reused the reference-frontend playbook.** structure_js already had a statement walker inside `_build_vfg`; the PDG added a parallel `_build_pdg` that reuses the same param/target helpers and the shared `_walk`, so `pdg_source` keys identically to `fingerprint_source`/`vfg_source` by construction. (2) **Two completeness gaps, both closed class-wide, not instance-wide.** The `with`-body drop (R214) was fixed by descending *any* un-special-cased block-bearing statement — the same "close the whole class" discipline that fixed the Python `match` gap in v3.9.0 — and the `typeof`-in-type-position leak (R216) was fixed at the one grammar node (`type_query`) that is the sole place a runtime identifier appears in a type. (3) **A tree-sitter frontend needs its own type-erasure awareness.** Unlike Python's `ast` (where `.ctx` distinguishes load/store and there are no type positions), the TS concrete tree puts value identifiers inside type nodes; the PDG's reads/writes collector had to learn that `type_query` carries no runtime flow — a class of bug that simply does not exist in the Python frontend.

## v3.11.0 — the STATEMENT layer learns Go (§5c sweep, language 3)

Third language of the STATEMENT/PDG sweep, after Python (v3.9.0) and the JS family (v3.10.0).
`structure_go.pdg_source` / `_build_pdg` build a per-function PDG from the tree-sitter Go tree,
mirroring the Python/JS builders and reusing Go's existing `_walk`/`_param_names`. Go had the richest
statement grammar swept so far — expression + type switch, `select`, `defer`/`go`, `range` bindings,
multi-value `:=`, channel send — all covered, with the method receiver seeded at ENTRY.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R219 | 3 | ✗ | **opus caught a LOW completeness defect**: a type-switch `case <T>:` type operand leaked in as a spurious `TypeIdentifier` PDG node — the type sits under the tree-sitter field `type` (not `value`), so `_case_body`'s skip-span was `None` and the `type_identifier` was treated as a body statement. Fixed 37cd045: `_case_body` descends only genuine statements (statement_list + statement/declaration nodes), never a case's value/type operands — closing the class rather than special-casing the `type` field. sonnet + haiku CLEAN. |
| R220 | 3 | ✓ | post-fix (37cd045). All CLEAN. opus verified the fix general (625 fuzz, exotic type-switch cases `[]map[string]func()`/`chan struct{}`, nested type-switch inside an expression-switch case, all Go statement kinds); sonnet 780 passed + byte-identical isolation; haiku docs. |
| R221 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus 3000 fuzz + all Go statement kinds + exact >300 boundary + cardinal (find_stale byte-identical); sonnet 780 passed + import-hygiene + byte-identical Go isolation; haiku 11/11 docs. |

Process note: **the template is paying off, and the one defect was a familiar class.** Go's PDG reused
the JS builder's shape (ENTRY seeding, `process`/`block`/`collect`/`data_edges`/`bind_target`, sorted
determinism, generic block descent) almost verbatim; the single defect (R219) was the same *class* as
the earlier case/clause-operand leaks (Go type-switch types are to `type_case` what the Python `match`
case bodies and the switch case values were) — caught by opus's completeness hunt and closed by keying
on statement-ness. The typed-language type-position hazard (TS `type_query` in v3.10.0) did not recur
here because Go case *types* are the analogue and were the thing fixed; expect it again in the other
typed frontends (Rust/C/C++/Java/C#).

## v3.12.0 — the STATEMENT layer learns Rust (§5c sweep, language 4)

Fourth language of the STATEMENT/PDG sweep, after Python (v3.9.0), the JS family (v3.10.0), and Go
(v3.11.0). `structure_rust.pdg_source` / `_build_pdg` build a per-function PDG from the tree-sitter
Rust tree, reusing Rust's existing `_walk`/`_pattern_names` and seeding the method receiver + `self` at
ENTRY. Rust is **expression-oriented** — if/match/loop/while/for are expressions — so the builder
splits handling by position: control-flow in *statement* position becomes control nodes (`process`),
while in *value* position (`let y = if …`) it folds into the enclosing statement's reads (`collect`).

This was by far the longest grind of the sweep — **13 real defects across 17 panels (R222–R238)** — but
every one was a single class: **the read/write projection reading a non-value token (or a pattern
binding), or dropping a consumed read, versus the VFG sibling that walks the same AST.** Rust's rich
surface (self receivers, value-position control folding, let-else, let-chains, labels/lifetimes,
turbofish, macros, function-local const/static, struct/if-let pattern bindings) exposed the class in
far more positions than the prior three languages combined.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R222 | 3 | ✗ | opus: `self`/`super`/`crate` receiver refs (tree-sitter `self` nodes, not `identifier`) dropped by `collect` → ENTRY `self` seed dead, every receiver-mediated dependence lost. |
| R223 | 3 | ✗ | opus: value-position control/**block body** reads dropped — `collect` blanket-skipped `block`. Gave it explicit value-position handling mirroring `process`'s read projection. |
| R224 | 3 | ✗ | opus: **let-else** `else {…}` reads dropped (the `alternative` field never folded). |
| R225 | 3 | ✗ | opus: **let-chain** plain (non-let) clause reads dropped in both `_cond_reads` and `cond_edges`. |
| R226 | 3 | ✗ | opus: spurious read of a **macro-invocation name** (generic fallback read the macro name identifier). Read only the token-tree args. |
| R227 | 3 | ✓ | First clean panel (streak 1). opus VFG-vs-PDG differential verified R222–R226; sonnet 795 passed + cardinal; haiku docs. |
| R228 | 3 | ✗ | opus: **struct-pattern shorthand** bindings dropped (`add_target` didn't handle the shorthand leaf). haiku: two stale test-module docstrings. **ADDED `tests/oracles/test_pdg_rust_vfg_differential.py`** — a white-box differential oracle (generated value-position wrapper corpus composed to depth 2, cross-checked VFG-reads ⟹ PDG-reads; binding-reaches-use; no-spurious-read families) to regress the whole class at once instead of one finding per panel. |
| R229 | 3 | ✗ | opus: spurious **loop-label** read in break/continue. |
| R230 | 3 | ✗ | opus: spurious **labeled-block label** read (label is a `block` child). Guarded `label` uniformly in `collect` + `process`. |
| R231 | 3 | ✗ | opus: function-local **const/static** binding dropped + declared name self-read. |
| R232 | 3 | ✗ | opus: PDG spurious read of a **lifetime turbofish** (`foo::<'lt>()`). Guarded `lifetime`. |
| R233 | 3 | ✗ | opus: the label/lifetime class was **still live in the VFG sibling** (`ev`) — corrupted WL fingerprints by label name. Guarded label/lifetime in `ev` too; hardened the oracle to assert neither builder reads a non-value token. |
| R234 | 3 | ✗ | opus: VFG read a **turbofish TYPE arg** (`foo::<v>()`) as a value. Gave type names a name-agnostic `freevar` branch (mirrors the PDG's type-skip). |
| R235 | 3 | ✓ | Clean panel (streak 1). opus: read/write projection complete in both builders; sonnet 1058 passed + cardinal; haiku docs. |
| R236 | 3 | ✗ | opus: VFG read **if-let/while-let pattern bindings** (and struct field-pattern names) as values (no `let_condition` handler). Added one that binds the pattern + reads only the scrutinee. haiku: `_build_pdg` docstring clarity. |
| R237 | 3 | ✓ | **Clean (streak 1).** opus independent re-derivation: complete VFG-side sweep of every pattern/type/label position, each guarded/bound in BOTH builders; sonnet 1061 passed + cardinal HOLDS; haiku docs. |
| R238 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus 55+ fresh constructs + 6000-composition depth-3 fuzz + 3000-program well-formedness/determinism fuzz + comment-invariance + key-parity (0 violations); sonnet 1061 passed + cardinal + byte-identical isolation; haiku docs. Tag left to the maintainer. |

Process note: **the whack-a-mole was the point being made, and the fix for it was tooling, not luck.**
The recurring class — `collect`/`add_target`/`_cond_reads` (PDG) and `ev` (VFG) reading a non-value
token whose name happens to collide with an in-scope value — kept surfacing one position at a time
because nothing cross-checked the two builders. R228 added the **VFG-vs-PDG differential oracle**: it
generates value-position construct *combinations* (depth-2 wrapper composition) and asserts
`VFG-reads(v) ⟹ PDG-reads(v)`, plus absolute binding-reaches-use and no-spurious-read families. Once
in place, the remaining findings were driven to closure and the class is now guarded **uniformly in
both builders** across labels, lifetimes, type positions, macro names, and every pattern position;
the differential regresses the whole class in one test. The typed-language type-position hazard
predicted in v3.11.0 did recur (R232/R234 turbofish), as did the pattern-binding hazard (R228/R236) —
both now closed. Remaining tree-sitter languages (C/C++, Java, C#, Ruby, PHP, Bash) are the rest of
the sweep; the differential-oracle harness is the template to carry forward.

## v3.13.0 — the STATEMENT layer learns C and C++ (§5c sweep, language 5)

Fifth language of the STATEMENT/PDG sweep, after Python (v3.9.0), the JS family (v3.10.0), Go
(v3.11.0), and Rust (v3.12.0). `structure_cpp.pdg_source` / `_build_pdg` build a per-function PDG
from the tree-sitter C/C++ tree — one walker covers both C and C++ — reusing the shared `_walk` and
seeding the method receiver at ENTRY. C/C++ is **statement-oriented** (like Go), so the builder is far
less contorted than Rust's expression-oriented fold; the grind was instead C++'s enormous surface.

Learning from Rust, the whole recurring class was **front-loaded**: the `_TYPE_NODES`/label/field
guards and the VFG-vs-PDG differential oracle (`tests/oracles/test_pdg_cpp_vfg_differential.py`)
shipped with the first panel (R239), so the RMW/stmt-expr/init-capture defects fell fast. The two
late finds were the exact divergence the oracle exists to catch — but in positions the corpus had not
yet composed: a **type-position VFG over-read** (R245) and a **structured-binding VFG under-read**
(R246), both closed by mirroring the PDG into the VFG and extended into the oracle. **6 real defects
across 11 panels (R239–R249)** — half of Rust's grind.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R239 | 3 | ✗ | opus: a parenthesized RMW target `(x)+=`/`(x)++` recorded no STORE (`rmw_target` didn't unwrap `parenthesized_expression`) → later use threaded from ENTRY. haiku: stale test_layer_matrix docstring. **Shipped the C/C++ VFG-vs-PDG differential oracle with panel 1.** |
| R240 | 3 | ✗ | opus: a GNU statement-expression `({ …; value })` in value position had its inner reads/writes dropped (`collect` blanket-skipped `compound_statement`). Fold them into the enclosing statement. |
| R241 | 3 | ✗ | opus: a lambda **init-capture** `[z = v]` initializer (enclosing-scope flow the VFG walks) was dropped because `collect` early-returned on _FUNC_NODES before inspecting captures. Fold each capture initializer; plain captures stay opaque. |
| R242 | 3 | ✗ | opus: default-parameter-value VFG/PDG asymmetry undocumented → docstring note. haiku: Python PDG oracle docstring omitted C/C++. sonnet clean. Docs only. |
| R243 | 3 | ✗ | opus: variadic parameter packs seeded by NEITHER builder — a symmetric under-approximation — undocumented. Completed the docstring's under-approximation list. Docs only. |
| R244 | 3 | ✓ | Clean panel (streak 1). opus 360 single-position + ~4300 composed cases, 0 violations; sonnet 1186 passed + cardinal. Streak broken by R245 (this corpus omitted decltype/template-type-arg positions). |
| R245 | 3 | ✗ | opus: the VFG (`ev`) read a param name in a **TYPE position** — `g<v>()`, `decltype(v)`, template TYPE args, alias/typedef — that the correct PDG drops (unevaluated compile-time operand): a literal VFG-reads/PDG-drops divergence. Mirror the PDG's `_TYPE_NODES` skip into `ev` (name-agnostic TYPE node, no descent; qualified_identifier excepted); genuine value operands still read. Extended the oracle's no-spurious-read family. |
| R246 | 3 | ✗ | opus: the VFG (`bind`) dropped a **used structured-binding** param `auto [a,b]=v; use(a)` (RHS evaluated then discarded). Added a `structured_binding_declarator` case + routed _DECL_WRAP through `_decl_child` so `auto& [a,b]` reaches its inner name. Docstring split the one asymmetry from the two symmetric gaps. haiku: bare-'JS' nomenclature. |
| R247 | 3 | ✗ | opus + sonnet **CLEAN** — R245/R246 fixes held under 47 hand-written + ~856 fuzz cases and the 1194-test gate + byte-identical isolation; no `_decl_child` regressions. haiku: one pre-existing bare-'JS' in the Rust `_build_pdg` docstring (harmonized). Docs only. |
| R248 | 3 | ✓ | **Clean (streak 1).** opus independent re-derivation, fresh ~10,100-case corpus (depth-3 composition fuzz, per-parameter attribution), 0 VFG⟹PDG violations; sonnet 1194 passed + cardinal + byte-identical isolation; haiku docs. |
| R249 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus re-certification with ~24,000 fresh depth-3 fuzz + ~75 curated cases — could not falsify (one informational safe-direction artifact: a vexing-parse `({ long(v); })` the VFG over-reads and the PDG handles correctly). sonnet 1194 passed + cardinal + byte-identical isolation; haiku docs. Tag left to the maintainer. |

Process note: **front-loading the oracle turned Rust's 17-panel grind into 11.** The differential
oracle and the type/label/field guards shipped with panel 1 (not discovered panel-by-panel), so the
class-of-one defects (RMW, stmt-expr, init-capture) were caught in the first three rounds. The two
that slipped to R245/R246 were not new *kinds* of bug — they were the same VFG-vs-PDG divergence in
**composed positions the corpus had not yet generated** (a param name inside a `decltype`/template
argument; a param destructured by a structured binding). Both were closed by making the VFG mirror the
PDG and then *widening the oracle's corpus* so the whole sub-class regresses — the fix for whack-a-mole
remains tooling, not luck. The type-position hazard predicted since v3.11.0 recurred here (R245) exactly
as in Rust (R232/R234), confirming it as the standing typed-language risk to front-load for Java/C#.
Remaining sweep: Java, C#, Ruby, PHP, Bash.

## v3.14.0 — the STATEMENT layer learns Java (§5c sweep, language 6)

Sixth language of the STATEMENT/PDG sweep, after Python (v3.9.0), the JS family (v3.10.0), Go
(v3.11.0), Rust (v3.12.0), and C/C++ (v3.13.0). `structure_java.pdg_source` / `_build_pdg` build a
per-function PDG from the tree-sitter Java tree — Java is **statement-oriented** (like Go and C/C++),
everything lives in a type, methods/constructors keyed by the dotted enclosing-type chain (`Outer.m`,
`C.C`). The read/write projection (`collect`/`bind_place`/`rmw_target`) was written to **mirror the
VFG's `ev`/`bind` node-for-node**, so the two builders agree by construction.

The C/C++ lesson was fully applied: the whole recurring class was **front-loaded**. The white-box
VFG-vs-PDG differential oracle (`tests/oracles/test_pdg_java_vfg_differential.py`) AND the
`_PDG_TYPE_NODES` / method-name / field / label guards shipped **with panel 1** — and Java became the
first language of the sweep to ship with **ZERO code defects across all panels**. The only finding in
three rounds was a one-line docstring nit.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R250 | 3 | ✗ | First Java panel. opus deep-cert of the brand-new builder: 0 VFG⟹PDG violations across ~50 hand-built + a 4000-iteration nested fuzz; precision/determinism/well-formedness/key-parity all pass — **no code defect on the first panel**. sonnet 1312 passed + cardinal + byte-identical isolation. haiku: one doc nit (Python PDG oracle docstring omitted Java). Docs-only. |
| R251 | 3 | ✓ | **Clean (streak 1).** opus independent falsification, fresh ~75-case corpus + 5000 depth-3 fuzz, 0 DROP / 0 spurious; accepted artifacts only (a `case v:` label-constant safe over-read; a catch-param-shadowing-a-method-param case that is INVALID Java and reverse-direction — PDG more precise than VFG). sonnet 1312 passed + cardinal + byte-identical isolation. haiku docs. |
| R252 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus re-certification with ~80 fresh cases + 8000 depth-3 fuzz (statement- and expression-position) + a 21-case name-collision corpus — could not falsify; all divergences accepted safe-direction/symmetric; docstring accurate. sonnet 1312 passed + cardinal + byte-identical isolation. haiku docs. Tag left to the maintainer. |

Process note: **front-loading beat the grind — Java shipped defect-free.** Rust took 17 panels and 13
defects because the differential oracle was built mid-stream (R228); C/C++ took 11 panels and 6
defects with the oracle shipped at panel 1 but the corpus still discovering composed type/binding
positions (R245/R246); Java took **3 panels and 0 code defects** because the oracle, the type/label/
method-name/field guards, AND the discipline of mirroring the VFG `ev`/`bind` node-for-node were ALL
in place before the first review. The recurring class did not open once. The remaining sweep (C#,
Ruby, PHP, Bash) carries the same template; C# is the next typed language and inherits the
type-position front-loading directly.

## v3.15.0 — the STATEMENT layer learns C# (§5c sweep, language 7)

Seventh language of the STATEMENT/PDG sweep, after Python (v3.9.0), the JS family (v3.10.0), Go
(v3.11.0), Rust (v3.12.0), C/C++ (v3.13.0), and Java (v3.14.0). `structure_csharp.pdg_source` /
`_build_pdg` build a per-function PDG from the tree-sitter C# tree; C# is statement-oriented (like
Java and C/C++), methods/constructors keyed by the dotted enclosing-type chain. The read/write
projection **mirrors the VFG's `ev`/`bind` node-for-node**, so the two builders agree by construction
— and helpfully, C#'s `member_access_expression` reads only its `expression` field, so a call's method
name is skipped naturally.

Like Java, the whole recurring class was **front-loaded** — the differential oracle
(`tests/oracles/test_pdg_csharp_vfg_differential.py`) and the type/member-name/label guards shipped
with panel 1 — and C# became the **second consecutive language to ship with ZERO code defects**.

The one substantive find was a *pre-existing* C# VFG bug surfaced while mirroring: `_do_var_declaration`
skipped ALL identifier declarator children (to avoid re-reading the declared name), so a bare-identifier
initializer `int r = v;` dropped the copy entirely. Fixed by identifying the name via its `name` FIELD
and reading every other child — applied to BOTH `_build_vfg` and the new `_build_pdg` so they stay
consistent (VFG-reads ⟹ PDG-reads). The expression-layer suite (structure/find_similar/completeness)
was unregressed by the VFG change.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R253 | 3 | ✓ | First C# panel — and clean. opus deep-cert of the brand-new builder: 0 VFG⟹PDG violations across ~75 hand-built + 1728 depth-3 composs + 3150 context×wrapper fuzz; **found + fixed the pre-existing `_do_var_declaration` bare-identifier-copy bug in both builders**. sonnet 1432 passed + cardinal + byte-identical isolation (no regression from the VFG fix). haiku's only note — the [3.14.0] changelog 'remaining languages' list — was REJECTED as an immutable point-in-time historical record (no edit). Streak 1. |
| R254 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus re-certification with a fresh corpus + depth-3 fuzz = **317,057 tested cases** (incl. LINQ, stackalloc, `with`, collection expressions, patterns) — 0 divergences, 0 spurious reads; keys/determinism/well-formedness pass; docstring accurate. sonnet 1432 passed + cardinal + byte-identical isolation. haiku docs clean. Tag left to the maintainer. |

Process note: **two languages defect-free in a row confirms the front-loading template.** Java (3
panels, 0 defects) was not a fluke — C# repeated it (2 panels, 0 code defects). The recurring
VFG-vs-PDG divergence class is now reliably closed *before* the first review by three things shipped
together at panel 1: the white-box differential oracle, the type/member-name/label guards, and the
discipline of writing the PDG's read-projection to mirror the VFG's `ev`/`bind` node-for-node. The
only surprise was a *pre-existing* expression-layer bug (bare-identifier initializer) that mirroring
exposed — fixed in both builders at once. A useful lesson: building the PDG as a faithful mirror of
the VFG is also an audit of the VFG. Remaining sweep: Ruby, PHP, Bash (dynamically-typed — the
type-position hazard recedes; the pattern/label and method-name hazards remain).

## v3.20.1 — `get_callers`/`get_callees`: precise, actionable name-resolution refusals (dogfood fix)

A PATCH surfaced by the **dogfood build experiment** (`research/07-dogfood-build`, round 2): a fresh
agent extending an unfamiliar codebase ran `get_callers "nodes"` and got *"'nodes' is not a unique
symbol"* — the same message the ops emit for a **genuinely ambiguous** name, even though `nodes`
simply *didn't exist*. Not a crash (the honest-refusal envelope returned a clean `ok=False`), but a
misleading, unactionable message. Fixed with a new `_resolve_or_explain` helper: unknown → *"no symbol
named 'X' in the index"* (matching `find_symbol`); ambiguous → lists the sorted candidate ids (cap 8 +
"(+K more)") and *"pass a qualified id (Type.method or path::qualified.name)"*. `_resolve_target` /
`_resolve_one` are byte-unchanged, so `trace_path`/`impact_of` are untouched; message/usability only.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R269 | 3 | ✓ | **Clean (streak 1).** opus falsification on 0/1/2/10-def stores: message + count N + sorted deterministic listing + 8-id cap all correct; qualified `Type.method` and full `path::qual` still resolve (no regression); never raises on None/non-str/empty/whitespace/unicode/dotted/'::'-nonexistent; cardinal find_stale byte-identical, import loads no CLI/MCP, reach.py untouched; test_regressions 440 passed. haiku docs consistent (pyproject 3.20.1, CHANGELOG accurate — message-only, not a crash fix). sonnet gate by main: full suite 2295 passed / 28 skipped / 0 failures, ruff + mypy clean. |
| R270 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus independent re-cert: never raises on adversarial inputs incl. regex metachars, SQL-injection string, 5000-char name, whitespace-only, unicode; "valid full id AND bare homonym" shown structurally impossible; cardinal byte-identical after hammering all four resolving ops; test_regressions 440 passed. haiku docs consistent. Gate re-verified. Non-defect noted both rounds: `impact_of` keeps its own pre-existing ambiguous phrasing (cross-op message parity is a future nicety, not a regression). Tag left to the maintainer. |

### v3.21.0 — `find_modes` (behavioural POD) + `scaffold_coverage` (sandboxed capture kit), §6 win 3

New advisory pair: `find_modes` takes the POD (mean-centred SVD) of a per-test coverage matrix → behavioural modes, intrinsic dimensionality, minimal covering test set; `scaffold_coverage` generates a **sandboxed** (Docker no-network/cap-drop/read-only + shell + CI) capture kit so the user produces that coverage in their own jail — stitchgraph never runs their code, only reads the inert matrix. Language-agnostic (`stitchgraph-coverage-v1`), Python turnkey. Dogfooded with a full POD on stitchgraph itself (2315×764 → 10 modes recovering the per-language architecture; research/10).

| panel | models | clean | notes |
|---|---|---|---|
| R271 | 3 | ✗ | FIRST v3.21.0 panel. opus MEDIUM: redundancy used an O(n_tests²) similarity matrix → uncaught MemoryError on big-suite/few-function artifacts. LOW: `minimal_test_set` truncated at [:200] (not always a full cover). NIT: converter emitted absolute-path ids at SRC='.'. Fixed: O(n) frozenset grouping + catch MemoryError; un-truncated; always-relativise. |
| R272 | 3 | ✗ | opus MEDIUM: `intrinsic_dimensionality` could exceed the number of modes on zero-variance coverage (all-identical rows → mean-centred matrix all-zero → searchsorted returned len+1); numpy LinAlgError (SVD non-convergence) not caught. haiku: README/STATUS stale (v3.20.0 header, "15 operations", missing numpy dep). Fixed: k90=0 on zero variance + clamp to len(cum) + LinAlgError→RuntimeError refuse; docs synced. |
| R273 | 3 | ✓ | opus re-falsification CLEAN (idim≤modes across all inputs; k90 recompute matches; cardinal re-verified vs real store). One NIT (OVERVIEW.md [spectral] omitted find_modes, fixed) + one non-reachable INFO hardened (clamp k90 to nmodes for direct decompose(k=1)). |
| R274 | 3 | ✗ | opus **HIGH**: shipped Python converter used bare ast names → distinct same-named methods (A.run/B.run, every __init__/run) collapsed to one node id, corrupting the matrix and contradicting the kit's own README spec. NIT: pyproject [spectral] comment omitted find_modes. Fixed: func_ranges walks class/function nesting → qualified ids (Class.method, outer.inner) + innermost-range attribution; comment fixed. |
| R275 | 3 | ✓ | opus CLEAN — find_modes numeric core exact vs brute force over 400 fuzz trials; greedy cover proven complete; no numpy-scalar JSON leaks; all 4 cardinal rules. 3 non-defect INFO (uncentred scipy path semantics, bare-dict key literally 'format', direct decompose(k<0)). |
| R276 | 3 | ✓ | FIRST confirmation on converter-fixed tip. opus CLEAN — qualified-name fix exact node-id set-equality vs extract_project() across staticmethod/classmethod/async/nested/property/overload; property+overload same-name collapse matches the extractor's own behaviour. NIT: coverage.py suffixes test-id keys with `|phase`; README example omitted it (keys opaque; values match) — fixed. |
| R277 | 3 | ✓ | SECOND confirmation. opus comprehensive: numeric hand-recompute exact, determinism, adversarial battery all envelopes (4005×4005>cap refuse, 6000×4, 500-unique untruncated, zero-variance idim=0), sandbox flags present + confined, all cardinal rules. LOW: non-turnkey run_coverage.sh built by .replace() left a stray `--cov=.`/pip/to_canonical line — fixed (clean `_TEMPLATE_RUN` placeholder). |
| R278 | 3 | ✓ | Clean re-confirmation on the R276/R277-fixed tip: non-turnkey run script clean (bash -n, no stray lines), python turnkey intact, sandbox flags, qualified converter, find_modes battery + determinism + JSON-clean, all cardinal rules. Recommends passing the gate. |
| R279 | 3 | ✓ | **FINAL sign-off — streak ≥2, gate met, RELEASABLE.** opus independent re-attack: numeric hand-recompute exact, converter ids match reindex node-id parts, sandbox confinement, all 4 cardinal rules, 12/12 test_modes. One UNREACHABLE INFO (direct decompose(k<0) → negative idim; impossible via library/CLI/MCP which sanitise k to ≥2 or None) — accepted non-defect (class of R270's note); optional `max(0,…)` floor a future nicety. Gate: full suite 2305 passed / 28 skipped, ruff + mypy clean. Tag left to the maintainer. |

### v3.22.0 — forward-looking POD ops `select_tests` + `co_change` + `find_coupling` (§6)

Three advisory, read-only, **no-numpy** operations (new `core/coverage_query.py`) that turn the runtime
co-activation matrix into change-oriented queries: `select_tests` (which tests to run for a change —
runtime coverage fused with the static blast radius: both / runtime_only / static_only), `co_change`
(functions that co-activate with a symbol — "what moves together / implements this outcome"), and
`find_coupling` (pairs that co-run but have no static edge — implicit coupling the call graph can't see).
Test-ids normalised to node-id convention so runtime/static namespaces align. Dogfooded on stitchgraph's
own 2315×764 coverage. Total ops: 22 + admin `reindex`.

| panel | models | clean | notes |
|---|---|---|---|
| R280 | 3 | ✗ | FIRST v3.22.0 panel. opus falsification: essentially CLEAN — all 4 cardinal rules verified (no eager import; find_stale byte-identical; never raises across the adversarial artifact battery incl. NaN/Inf JSON + a 5000-fn giant test; no user-code exec), select_tests partition exact, scores ∈ [0,1], self-pair excluded, find_coupling excludes any-relation/either-direction edges + handles unresolved dst_id, `_COOC_FUNC_CAP=400` bounds OOM, limit/min_shared clamp. One NIT: `base_test_id` split on '|' before stripping `[param]` → a param containing '|' or nested brackets mis-normalised (advisory under-merge, never raises) — fixed (11e18b5). haiku NIT: OVERVIEW select_tests row hyphenated `runtime-only`/`static-only` vs underscored fields — fixed (7424757). |
| R281 | 3 | ✓ | FIRST clean confirmation on the fixed tip 11e18b5. opus: base_test_id fix correct AND complete (phase stripped before greedy end-anchored `[param]`; `test[a|b]|run`, nested `test[a[b]]`, param-containing-'run' all collapse; 2-part `file::func` intentionally not class-rewritten). Full re-attack clean: artifacts refuse cleanly, select_tests partition + homonym refuse, co_change cosine deterministic across PYTHONHASHSEED, find_coupling edge-exclusion + OOM guard + clamps, all 4 cardinal rules. |
| R282 | 3 | ✓ | **FINAL sign-off — streak 2 (readiness streak 8), gate met, RELEASABLE.** opus independent re-attack with hand-computed numerics: select_tests both/runtime_only/static_only exact; co_change cosine matches (0.8165, 0.5774); find_coupling score/shared exact, edge-excluded both directions, normalize collapses param rows; JSON-serializable + deterministic; 72-case adversarial battery zero raises; all 4 cardinal rules. One INFO non-defect: `_COOC_FUNC_CAP` skips a >400-fn test from the pair numerator but not the sizes denominator → a pair co-running only in such a near-global test under-reports its cosine (bounded, conservative — only ever under-reports; matches documented near-global-noise suppression). No action required. Gate: full suite **2315 passed / 28 skipped** (tip a48696c; the sole subsequent change is the localised `base_test_id` regex, covered by test_coverage_query 9/9 + test_modes 12/12 on the final tip), ruff + mypy clean. Tag left to the maintainer. |

Process note: the first defect *found by dogfooding stitchgraph as a build aid rather than by a panel*
— the round-2 extender agent's own DEVLOG recorded the confusing refusal, which became this fix. A
reminder that the honest-envelope "refuse clearly" principle is only as good as the clarity of the
refusal *message*: returning `ok=False` was correct; conflating "unknown" with "ambiguous" and hiding
the candidates was the real gap.

## v3.20.0 — `find_subsystems`: spectral subsystem decomposition (§6 spectral research → package)

The second §6 "system-matrix" win graduates into the package. New advisory operation `find_subsystems`
partitions the call/reference graph into its **natural subsystems** by spectral clustering of the graph
Laplacian, and auto-labels each cluster with the identifier tokens that most distinguish it (a
"spectral-summarize"). It is the *structural* complement to the semantic `find_similar` /
`summarize_subsystem`: it **discovers** the module boundaries rather than describing a scope you name.
Cluster count auto-selected from the spectral eigengap (or set via `k`). Backed by a new
`core/spectral.py` (normalised-Laplacian embedding + deterministic k-means++ + distinctive-token
labels); numpy-only out of the box (dense, capped at 2500 giant-component nodes), with an optional
`[spectral]` extra (scipy) that adds a sparse ARPACK solver for larger graphs. Advisory only, never
feeds `find_stale`.

The panel earned its keep again: opus's independent falsification caught a real HIGH that the
hand-written tests had not exercised — `find_subsystems` was **nondeterministic on the scipy/`eigsh`
path** for graphs with degenerate top Laplacian eigenvalues (regular graphs, hubs, rings): repeated
calls on the same store returned different partitions, and the sparse path disagreed with the
deterministic dense path. ARPACK injects random restart vectors on Lanczos breakdown (a fixed all-ones
`v0` is exactly the Perron eigenvector of a regular graph) and returns an arbitrary basis of a
degenerate eigenspace. Fixed by preferring the deterministic dense LAPACK solver for every giant
within the cap (even when scipy is installed) and reserving sparse `eigsh` for above-cap graphs, where
it now uses a fixed-seed generic start vector plus a tiny deterministic symmetry-breaking term — then
re-verified deterministic across processes and thread counts on genuine >2500-node graphs.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R266 | 3 | ✗ | First panel. opus falsification found ONE HIGH: nondeterministic clustering on the scipy/`eigsh` path for degenerate top eigenvalues (hub+15 → 10 distinct partitions / 10 calls; sparse≠dense) — ARPACK random restart on an exact-Perron `v0` + arbitrary degenerate-eigenspace basis. Reachable on ordinary hub/ring motifs. Fixed (c420361): dense LAPACK preferred ≤ cap even with scipy; sparse only > cap, with a fixed-seed generic `v0` + 1e-6·(i/n) diagonal symmetry-breaking. haiku docs clean; sonnet superseded by the fix. Dirty (1 HIGH, fixed). |
| R267 | 3 | ✓ | **Clean (streak 1).** opus re-cert on the fixed tip: 8 degenerate motifs ×10 calls deterministic on default + forced-sparse; a genuine 3000-node >cap SBM on the real sparse path deterministic across PYTHONHASHSEED (NMI=1.0, k=3); dense==sparse on 40 SBM + 32 weakening-coupling + tiny-eigengap (λ2~1e-3) graphs; robustness never raises; cardinal byte-identical, no eager spectral/scipy import. sonnet gates re-verified by main; full suite 2298 passed / 24 skipped. haiku docs consistent. |
| R268 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus from-scratch: SBM NMI=1.000 all seeds; 3000-node SBM + 2-regular cycle + torus on the real ARPACK path byte-identical across PYTHONHASHSEED {0,1,42,777,12345,999} × OMP_NUM_THREADS {1,4}; 120 near-threshold SBMs dense==sparse (1e-6 ramp never flips real structure); 1000 fuzz runs 0 nondeterminism / 0 raises; contract over 400+ graphs; cardinal byte-identical, reach.py untouched. 3 non-defect NITs noted (structureless-K8 auto-k, tokenizer lone-digits, unreachable dense LinAlgError). Gates re-verified; full suite 2298 passed / 24 skipped. haiku docs. Tag left to the maintainer. |

Process note: the §6 pattern from `find_chokepoints` repeated exactly — a numerically subtle new
operation whose author-written tests all passed, but an INDEPENDENT adversarial pass (opus) found a
real defect the tests never provoked. R263's lesson was "tests can encode the bug"; R266's is its
sibling: **tests can fail to exercise the failure mode at all** — the determinism tests used only the
non-degenerate planted-community graph, so the ARPACK-on-degenerate-spectrum nondeterminism went
unseen until opus threw regular graphs / hubs / rings at it. Both wins closed in 3 panels / 1 HIGH
each. §6 win 3 (POD over runtime coverage, Python-first) remains — explicitly saved for last.

## v3.19.0 — `find_chokepoints`: articulation-point criticality (§6 spectral research → package)

The first result of the §6 "system-matrix" research thread graduates into the shipped package. New
advisory operation `find_chokepoints` returns the **articulation points** (cut vertices) of the
call/reference graph — nodes whose removal disconnects the graph — each ranked by its **blast
radius** (how many nodes get cut off from the main body if it fails). A robustness / "dangerous to
touch" signal distinct from `orient`'s hub centrality: a chokepoint can have modest fan-in/out yet be
the sole bridge between two subsystems. Backed by `reach.articulation_points` (one iterative Tarjan
DFS pass, subtree sizes inline, O(V+E), deterministic, recursion-limit-guarded like the SCC core);
advisory only, never feeds `find_stale`; no new dependency.

The panel earned its keep: opus's brute-force falsification caught a real HIGH the hand-written tests
had *masked* (they encoded the buggy values) — the non-root blast radius used `sum(child subtrees)`,
which inverted the ranking on a chain (a near-leaf reported as top chokepoint). Fixed to the uniform
`(comp_total-1) - max(pieces)` definition and re-verified against brute force.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R263 | 3 | ✗ | First panel. opus brute force (6000 graphs) confirmed AP-set detection correct (0 mismatches), determinism, robustness (20000-node chain, no RecursionError), cardinal — but found ONE HIGH: non-root blast radius = `sum(child subtrees)` wrongly assumed the parent side is always the main body → ranking inversion + inflated counts (up to N-2). Fixed to `(comp_total-1)-max(pieces)`; re-verified 0/4000 vs brute force; added a chain-symmetry regression (the two original tests had encoded the buggy values). haiku 1 NIT (README "functions" → "code entities"). Both fixed. |
| R264 | 3 | ✓ | **Clean (streak 1).** opus fresh re-cert: 47,250 random graphs across 14 families, 0 AP-set + 0 blast mismatches; chain symmetric (inversion gone); determinism, robustness, cardinal, contract all pass. sonnet gates + full suite 2284 passed / 24 skipped. haiku docs clean. |
| R265 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus 14,000-graph from-scratch brute force (+self-loops/dup/dangling-dst/pseudo/non-liveness perturbations), ~56,480 APs, 0 mismatches; chain a symmetric tent; determinism across PYTHONHASHSEED; cardinal byte-identical; contract (incl. bad/bool limits) safe. Gates re-verified; full suite 2284 passed. haiku docs. Tag left to the maintainer. |

Process note: this is the first §6-spectral-research win promoted to product, and the first *new
operation* since the §5c layer work. The lesson reinforced: **tests written by the author of a subtle
algorithm can encode the bug** — R263's blast-radius defect passed the initial suite because the tests
asserted the buggy output. An INDEPENDENT brute-force reference (opus's, and then main's 4000-graph
re-check) is what caught and pinned it. §6 wins 2 (spectral-summarize → `summarize_subsystem`) and 3
(POD over runtime coverage, Python-first) remain.

## v3.18.0 — the STATEMENT layer learns Bash — the §5c sweep is COMPLETE (language 10)

The tenth and FINAL language of the STATEMENT/PDG sweep. With Bash, the statement layer now covers
**every body-matrix language** (Python + the JS family + Go + Rust + C/C++ + Java + C# + Ruby + PHP +
Bash). Bash is the **command-oriented outlier**: a command is a statement whose callee + arguments
are the reads, and — uniquely — **shell functions have no declared parameter list** (positional `$1…`
are free variables). So `ENTRY` carries no params, exactly as the value-flow builder seeds no `PARAM`
nodes. `structure_bash.pdg_source` / `_build_pdg` mirror the VFG's `ev`/`bind`/`_do` node-for-node.

Because there is no parameter to attribute, the differential oracle **seeds** the name-attributable
variable with a first assignment `v=$SEED`: the VFG makes node 0 the `FREE` node for `SEED` (created
first) and copy-props it into `v` (read ⟺ node 0 has an out-edge); the PDG's node 1 is that seed
assignment (read ⟺ any `(1,_,'D')` edge). The build was preceded by a grammar probe that pinned the
key precision point — a LITERAL command name is a free callee (never a variable read), while a
*dynamic* `$cmd`/`$(…)` name reads its expansions.

The front-loading template held even for the outlier: **2 clean panels, 0 code defects** — the one
finding was a doc LOW (a stale README status header).

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R260 | 3 | ✗ | First Bash panel. opus ~39,900-case independent falsification (~29,600 genuine VFG-reads) — 0 VFG⟹PDG violations, and *proved* the node-1 attribution axis sound (a rebind that moves `last_def[v]` also kills the VFG seed, lifting the obligation). sonnet gates: full suite 2276 passed / 24 skipped, ruff/mypy/cardinal clean, byte-identical isolation, Bash oracle 61. haiku found ONE LOW: the README `## Status (v…)` header was stale at v3.16.0 — fixed to v3.18.0 (commit fdbec2a). |
| R261 | 3 | ✓ | **Clean (streak 1).** opus fresh ~24,000-case re-cert (~15,400 genuine reads) — 0 violations; identified three SYMMETRIC under-reads (`${#v}`, `v+=x`, extglob/single-quoted trap) that miss in BOTH builders (shared soundness limits, not divergences). sonnet gates re-verified; haiku docs clean (status-header fix confirmed). |
| R262 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus 21,888-case final falsification across 6 seeds (19,232 genuine reads, both directions exercised) — 0 violations / crashes / parity / determinism failures; seed mechanics verified directly. sonnet gates re-verified; haiku docs clean. Tag left to the maintainer. |

Process note: **the §5c STATEMENT-layer sweep is complete — all 10 sweep-languages / 12 body-matrix
languages.** Bash, the command-oriented, parameter-less outlier, still closed in 2 clean panels with
0 code defects because the discipline held: build the PDG's read/write projection as a faithful VFG
mirror, probe the *actual* grammar (literal vs dynamic command names) before writing the walker, and
ship the differential oracle with panel 1 — here adapted to seed the attributable variable since
there is no parameter. Across the phase-3 sweep (JS→Go→Rust→C/C++→Java→C#→Ruby→PHP→Bash) the recurring
VFG-vs-PDG divergence class went from Rust's 17-panel/13-defect grind (pre-oracle) to a steady 2–3
panels/0–1 defects once the oracle and mirror discipline were front-loaded.

## v3.17.0 — the STATEMENT layer learns PHP (§5c sweep, language 9)

Ninth language of the STATEMENT/PDG sweep, after Python (v3.9.0), the JS family (v3.10.0), Go
(v3.11.0), Rust (v3.12.0), C/C++ (v3.13.0), Java (v3.14.0), C# (v3.15.0), and Ruby (v3.16.0). PHP is
**statement-oriented** (like Go/C/C++/Java/C#), so `structure_php.pdg_source` / `_build_pdg` mirror
the VFG's `ev`/`bind`/`do` node-for-node: the read/write projection (`collect`/`bind_place`/
`rmw_target`) reads exactly the value operands the VFG's `ev` reads and binds exactly what `bind`
binds. The build was preceded by a **grammar-reconciliation probe** — tree-sitter emits
`member_call_expression` / `nullsafe_member_call_expression` (not the `method_call_expression` the VFG
lists), so those calls flow through the *shared generic fallback* in BOTH builders (reading object +
method-name + args); `scoped_call_expression`/`function_call_expression` hit the explicit CALL handler
(object/function/args, not scope/name). Mirroring these exactly — plus the symmetric gaps (`foreach`
`$k=>$v` pair binds nothing, `Foo::$x` opaque freevar, member NAME never read even for dynamic
`$o->$v`) — kept the two builders in lock-step.

The front-loading template held: the differential oracle + name-position precision cases + the
VFG-mirror shipped WITH panel 1, so PHP became the **third consecutive language to close with ZERO
code defects** (after Java and C#) — **2 panels, 0 findings**.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R258 | 3 | ✓ | **First PHP panel — clean (streak 1).** opus INDEPENDENT falsification: ~61,000 cases (85 hand-crafted §5c-targeted + 20,000 depth-0..3 random + 29,960 param-biased, of which 26,735 were genuine VFG-reads) — ALL 26,735 also PDG-reads, 0 invariant violations, 0 raises, 0 malformed graphs, 0 key mismatches, determinism confirmed. sonnet gates (re-verified by main): full suite 2214 passed / 24 skipped, ruff/mypy/cardinal clean, byte-identical find_stale isolation, PHP oracle 161 passed. haiku docs: all 7 surfaces consistent. |
| R259 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus fresh multi-generator campaign: ~24,900 valid cases (curated + deep-random-nested + §5c-hazard-targeted + 4,000 MULTI-param differential + ~3,060 malformed), ~19,200 genuine VFG-reads (~77% non-vacuous) — 0 violations; every §5c defect-class pattern verified to read identically in both builders; all observed VFG≠PDG cases are the allowed PDG≥VFG direction. sonnet gates re-verified (ruff/mypy/cardinal/oracle clean, full suite 2214 stands). haiku docs. Tag left to the maintainer. |

Process note: **the front-loading template makes statement-oriented languages routine.** PHP is the
third statement-oriented language (after Java and C#) to ship with zero code defects across its
panels — the discipline of building the PDG's read-projection as a faithful VFG mirror, shipping the
differential oracle with panel 1, and probing the *actual* grammar node names before writing the
walker (member_call vs method_call) means the recurring VFG-vs-PDG divergence class simply does not
arise. Remaining sweep: **Bash — the last language.**

## v3.16.0 — the STATEMENT layer learns Ruby (§5c sweep, language 8)

Eighth language of the STATEMENT/PDG sweep, after Python (v3.9.0), the JS family (v3.10.0), Go
(v3.11.0), Rust (v3.12.0), C/C++ (v3.13.0), Java (v3.14.0), and C# (v3.15.0). Ruby is
**expression-oriented** (like Rust — the hard tier): every construct is an expression, a method
body's trailing expression is its implicit return, and control constructs (`if`/`case`/`while`/`for`)
appear in both statement AND value position. `structure_ruby.pdg_source` / `_build_pdg` mirror the
VFG's `ev`/`bind`/`_do` node-for-node: control becomes a control node in statement position but
**folds its reads into the enclosing statement in value position** (`x = if c then a else b end`).

The front-loading template held even for the hard tier: the differential oracle + method-name/self
guards + VFG-mirroring shipped at panel 1, so Ruby closed in **3 panels with just 1 real defect** —
versus Rust's 17 panels / 13 defects for the *same* expression-oriented shape before the oracle
existed. The one defect was the recurring class in a Ruby-specific position.

| Panel | Models | Clean | Notes |
|---|---|---|---|
| R255 | 3 | ✗ | First Ruby panel. opus (HIGH): a `case/in` guard `in P if <cond>` — the `in_clause`'s `guard` field is read by the VFG (generic fallback descends `case_match`) but the PDG hand-enumerated only `pattern`+`body`, dropping it (VFG-reads-but-PDG-drops). Fixed by reading the guard in both `collect` and `process`; extended the differential oracle with case/in guard cases. sonnet gate re-verified 1532 passed locally. haiku's `_build_pdg` 'omits Ruby' note REJECTED (predecessors-only pattern, correct). |
| R256 | 3 | ✓ | **Clean (streak 1).** opus fresh ~90-case corpus + ~14,000 fuzz (336-combo + 8000 depth-3 nested + 6000 seeded random) — 0 VFG⟹PDG divergences; the guard fix holds; precision (method name not read) holds. sonnet 1532 passed + cardinal + byte-identical isolation. haiku docs. |
| R257 | 3 | ✓ | **FINAL sign-off — streak 2, gate met, RELEASABLE.** opus re-certification with a fresh 61-case corpus + 8000 depth-3 composition + 20,000 random recursive fuzz — 0 violations, 0 spurious method-name reads; docstring's folding + symmetric gaps + parameter-default asymmetry all confirmed accurate. sonnet 1532 passed + cardinal + byte-identical isolation. haiku docs. Tag left to the maintainer. |

Process note: **the front-loading template survives the expression-oriented hard tier.** Ruby has the
same everything-is-an-expression shape that made Rust the sweep's longest grind (17 panels, 13
defects) — but with the differential oracle, the method-name/self guards, and the VFG-mirroring
discipline all in place at panel 1, it closed in 3 panels with 1 defect. That defect (a `case/in`
guard field the VFG's generic fallback covered but the PDG's hand-enumeration missed) is the exact
recurring class, caught immediately by the oracle rather than panel-by-panel. Remaining sweep: PHP,
Bash — the last two, both dynamically typed.

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
