# LIMITATIONS

Deliberate design decisions that produce behaviour a reviewer might mistake for a
defect. Each entry is a decision we would make again, not an accident we haven't
fixed. Reviewers must read this first and not re-report what it covers (you may
argue an entry is wrong, with reasoning).

**Maintenance rule:** when a limitation is fixed, delete its entry — git history
carries the past; this file describes only the current state.

Each entry: **Concern** (what looks wrong) / **Decision** (what we chose) /
**Rationale** (why the alternative is worse) / **Escape hatch** (the override).

---

## Fundamental ambiguity (no correct answer without type/content understanding)

### Dead-code findings are advisory, not type-grade
- **Concern:** `find_stale` returns `needs_review` at 0.6 confidence and can list
  a symbol that is actually used.
- **Decision:** resolution is name/scope-based; dead-code results are advisory
  (`needs_review`), never asserted as fact.
- **Rationale:** type-correct resolution of `x.save()` needs full type inference
  (an LSP per language). Over-claiming "dead" invites a destructive false delete —
  the one outcome the whole design refuses (precision over recall). Honest low
  confidence is correct, not a bug.
- **Escape hatch:** `reindex --precise` (jedi, Python); pin roots in
  `stitchgraph.toml [entry_points]`; `ingest_trace` a coverage run to raise
  confidence to 0.78 and seed liveness from what actually executed.

### Test roots are detected per-language by convention, attribute, or call site
- **Concern:** `find_stale` keeps idiomatic tests (and the helpers they reach) live by
  recognizing tests across languages (v1.0.1, issue #8 + polyglot generalization):
  the `test*`/`Test*`/`Benchmark*`/`Example*` **name** convention (Go, pytest,
  Minitest, PHPUnit `testFoo`); **attributes/annotations** — Rust `#[test]`/
  `#[tokio::test]`/`#[cfg(test)]`, Java `@Test`/`@BeforeEach`/… (JUnit/TestNG), C#
  `[Fact]`/`[Theory]`/`[Test]`/… (xUnit/NUnit/MSTest), PHP `#[Test]` (PHPUnit); and,
  for **call-based** suites that define no named test function (JS/TS Jest/Mocha/
  Vitest, Ruby RSpec), the module-level `test()`/`it()`/`describe()` call sites in a
  test file, rooted from the module node. A test driven *only* by a runner the
  detector doesn't know — a third-party Rust macro whose attribute path doesn't end in
  `test` (`#[rstest]`, `#[test_case]`, `#[googletest::gtest]`), or a custom
  Java/C# annotation not on the allowlist — can surface as a stale candidate.
- **Decisions / safe direction:** attributes match the annotation **path/name**
  against an allowlist (not a raw `"test"` substring); annotation tests propagate the
  `test` role to the enclosing class (`_seed_test_classes`) so a package-private
  fixture isn't flagged while its methods are live; call-based rooting fires only for
  files under strongly test-conventional dirs (`test`/`tests`/`spec`/`__tests__`) or
  name patterns (`_test.`/`.test.`/`_spec.`/…). A test **helper reached by no test
  still flags** — that is intended (a dead helper in a test file is still dead).
- **Rationale:** the substring form (v1.0.0→1.0.1 dev) over-matched
  `#[cfg(feature="testing")]`, `#[doc="...test..."]`, features like `latest`, *hiding*
  genuinely-dead production code (Panel W); the dir set excludes ambiguous `testing`/
  `specs` (plausible production dirs) for the same reason (Panel Y). The set of
  third-party runners is open-ended; recognizing them all would re-introduce that
  over-match. These cases surface as `needs_review` advisories, never confident verdicts.
- **Residual tradeoffs:** (1) a *production* file that happens to live under a `test`/
  `tests`/`spec`/`__tests__` directory has its module-level calls rooted — over-marking
  (hides some dead code) in the precision-safe direction, never a false "dead".
  (2) A test base class placed in a *non-test* directory (e.g. shipped test scaffolding
  in `src/`) whose subclass — in a test location — declares **no own test method** is not
  recognized: the base never gets the `test` role, so the inheriting subclass can surface
  as a stale candidate (Panel CC/DD). Rare (it requires both the inverted layout and a
  zero-own-test subclass); closing it generically would re-open the `testing`/`specs`
  over-match, so it is left as a documented limitation. The subclass declaring even one
  own test, or the base living under a test dir, resolves it.
- **Escape hatch:** pin the test in `stitchgraph.toml [entry_points]`, or
  `ingest_trace` a real test run to seed liveness from what actually executed.

### A few obscure JS/TS/CJS export indirections aren't rooted
- **Concern:** `find_stale` roots a module's public exports (v1.0.2 closed the common
  forms — `export {}` / inline `export class/fn` / `export default <ident>` / CommonJS
  `module.exports`/`exports.*` / TS `export =`). A handful of indirect forms are still
  not rooted, so a symbol exported *only* that way can surface as a stale candidate:
  a `module.exports = X` assignment buried *inside a function body*; `export * from
  './m'`; `Object.assign(module.exports, {…})`; and `module.exports = ns.Member` via a
  locally-constructed namespace object.
- **Decision / rationale:** match the idiomatic top-level forms by exact shape; these
  indirections are rare, and rooting them generically (e.g. any member-expression RHS)
  would over-mark and *hide* genuinely-dead code — the precision-unsafe direction. They
  surface only as `needs_review` advisories at 0.6 confidence, never a confident verdict.
- **Escape hatch:** pin the symbol in `stitchgraph.toml [entry_points]`, or re-export it
  with an idiomatic form (`module.exports = { Member }`).

### JS/TS member-assigned functions are rooted, not dead-code-eligible
- **Concern:** a function/class defined by **assignment to an object member** —
  `app.render = function(){…}` (CommonJS prototype augmentation, e.g. Express),
  `Foo.prototype.m = () => {…}`, `module.exports.x = function(){…}`, `this.h = function(){…}`
  — is now modeled (its body is walked, so calls inside it are visible — closing the old gap
  where Express's `tryRender`/`logerror` were flagged dead). But because there is no static
  caller for such a member (it is invoked externally, dynamically, or by a framework), a
  non-underscore one is **rooted** rather than treated as a dead-code candidate.
- **Decision / rationale:** rooting is the cardinal-safe direction — the same stance as
  "every public method of an exported class is public API." The cost is that a *genuinely*
  dead member-assigned method is not reported (an underscore-prefixed one still is). A bare
  `const f = function(){}` / `const f = () => {}` declaration is fully modeled and remains
  dead-code-eligible. Still not modeled: a computed member (`obj['x'] = …`) and re-aliased
  receivers (`var a = module.exports; a.x = …`) for the *export* signal specifically.
- **Escape hatch:** pin or exclude via `stitchgraph.toml`.

### A receiver call to a *single* same-named symbol is `INFERRED`, not `EXTRACTED`
- **Concern:** `obj.save()` resolves to the one project `save` — obviously correct — yet
  its CALLS edge is labelled `INFERRED` (a guess), not `EXTRACTED` (issue #10). Looks like
  the extractor is under-claiming confidence on an unambiguous call.
- **Decision:** a *receiver-based* call whose receiver type isn't known is marked
  `INFERRED` even when exactly one project symbol matches the name. In the **tree-sitter**
  extractor (no type model) that is *every* receiver call (`obj.save()`, `Class::save()`,
  `x->save()`); only a direct call (`save()`) or a constructor (naming a type directly)
  stays `EXTRACTED`. In the **Python `ast`** extractor, scope-aware resolution still applies
  first — `self.save()` and a locally-typed `r = Repo(); r.save()` resolve to the real
  method and stay `EXTRACTED`; only a receiver of *unknown* type (`x.save()` for an
  external/parameter `x`) falls back to the name-only bind and is demoted to `INFERRED`.
- **Rationale:** without type inference the receiver's type is unknown, so a lone same-named
  match may be a homonym `save` on a *different* (stdlib/third-party) class — asserting
  `EXTRACTED` there would over-claim. The **weight stays 1.0**, so the edge still counts
  fully for reachability / `find_stale` (it never under-counts a live caller — the
  cardinal-safe direction); only the asserted confidence is lowered. Over-claiming
  confidence on a possibly-wrong target is the worse error.
- **Escape hatch:** `reindex --precise` (Python) adds a confident go-to-definition edge to
  the true target; trust the per-edge `provenance`/`confidence`.

### Ambiguous calls link to *all* same-named candidates
- **Concern:** one call produces several `AMBIGUOUS` edges, inflating reachability
  and `impact_of` blast radius.
- **Decision:** when a name resolves to multiple symbols, edge to *all* of them.
- **Rationale:** under-counting reachability would flag live code dead
  (destructive); over-counting only under-reports dead code (safe). The
  confidence/provenance on each edge records the uncertainty.
- **Escape hatch:** `reindex --precise` (Python) adds a confident go-to-definition
  edge to the *true* target, so a consumer can prefer the `EXTRACTED`/`jedi` edge and
  filter the `AMBIGUOUS` ones by provenance. It is **additive** — it does NOT prune the
  competing `AMBIGUOUS` candidates, so it doesn't deflate `impact_of` / `find_stale`
  reachability on its own (issue #15). That's deliberate: pruning the losing siblings
  would let a single jedi mis-resolution drop a live symbol's only caller and flag it
  dead — the cardinal sin — so the safe additive design is kept.
- **Side effect — direct-degree inflation on dense homonym clusters (`fan_in` fallback
  only):** several same-named inner helpers (e.g. a `rec` recursion helper repeated in
  many modules) cross-link into a dense `AMBIGUOUS` sub-graph, so each one's *direct*
  in-degree (`fan_in`) counts every sibling's call site, not just its real caller (panel
  R19B). This is purely a **direct-degree** artifact: the GraphBLAS `transitive_fan_in`
  used by `orient` counts distinct reachable *sources* and is unaffected, so the standard
  install ranks hubs correctly; only the no-GraphBLAS `fan_in` fallback can let such a
  cluster crowd the hub list. `scan` surfaces the cluster as `GREEN`/`needs_review`
  `god_object`/`cycle` artifacts (confident edges = 0) — never a false `RED`/`ORANGE`, so
  no cardinal or urgency impact. Tightening this needs lexical-scope name resolution
  (binding a bare call to the enclosing-scope definition), which is a broad change kept
  out of the 1.0.x line; install `python-graphblas` for accurate hub ranking meanwhile.

### A method call through a declared type keeps *all* same-named methods live (Python)
- **Concern:** a call bound to a declared type (`def go(b: Base): b.run()`, or a method's
  own `self.step()`) marks **every** same-named `run`/`step` in the project live — including
  one on a class that is never instantiated — so a genuinely-dead method of that name is not
  flagged.
- **Decision:** the scope-aware resolver still emits a precise `EXTRACTED` edge to the
  declared type's method, but a binding via an *annotation* type (parameter/variable) is
  additionally widened with `AMBIGUOUS` edges to all same-named methods; a `self`/`cls`
  binding is widened to the enclosing class's subclasses (transitive `INHERITS`).
- **Rationale:** a type annotation is only a hint — the runtime object behind `b: Base` may
  be a subclass, a **structural** `typing.Protocol` implementer (no `INHERITS` edge), or an
  unrelated **duck-typed** class that merely provides the method (panels R19A, R20A). Any of
  them is what actually executes, so without widening the live implementation gets **no**
  inbound edge and is confidently flagged dead — the cardinal sin. Without type inference we
  cannot know which class the object is, so all same-named methods must be kept live.
  Over-keeping an unused same-named method live only under-reports dead code (the safe
  direction); the precise `EXTRACTED` edge still records the statically-declared target.
- **Escape hatch:** trust the per-edge `provenance` — the concrete-dispatch candidates are
  `AMBIGUOUS`; the `EXTRACTED`/`--precise` edge identifies the declared target.

### Implicitly-invoked dunder methods are rooted to their class (Python)
- **Concern:** a class's dunder (`__call__`, `__get__`/`__set__`/`__delete__`,
  `__getitem__`, `__enter__`, operators, …) is invoked by the interpreter with no explicit
  call site, so a helper it alone calls would be orphaned and flagged dead (panel R20A).
- **Decision:** seed a `REFERENCES` edge from each class to its dunder methods, so that when
  the class is reachable its dunders — and their callees — are reachable too.
- **Rationale:** dunders are real, implicitly-reachable entry points whenever instances of
  the class are used. Tying the edge to the *class* (not rooting the dunder unconditionally)
  keeps a dead class's dunders dead, so this rescues only genuinely-live callees. Dunders are
  already excluded from stale candidates, so this changes only their callees' liveness.

### Language implicit-hook methods are rooted by name (Ruby/Java/PHP/C++)
- **Concern:** every language has methods the runtime/interpreter invokes *implicitly*, never
  by name — Ruby's `inherited`/`included`/`extended`/`method_missing`, Java's serialization
  `writeReplace`/`readObject`/… and `equals`/`hashCode`/`toString`, PHP's `__call`/`__get`/…
  magic methods, C++ operator overloads/destructors. The name-based call graph can't see the
  use, so they (and their callees) were flagged dead — `Sinatra::Base.inherited`, arguably
  sinatra's core mechanism, is the headline example (multi-language false-positive hunt).
- **Decision:** root these by name per language (role `callback`), the cross-language analogue
  of skipping Python dunders. Constructors are handled separately (`initialize`/`__construct`/
  `constructor`).
- **Rationale:** these names are interpreter/runtime contracts, so a definition is a genuine
  implicit entry point. Rooting by name only ever *adds* roots (cardinal-safe); over-rooting a
  genuinely-dead hook is the documented precision-over-recall trade-off.

### PHP string callables: array form is covered; bare-string and module-scope are not (yet)
- **Covered (v2.0.1):** the 2-element array callable `[$this, 'method']` / `[self::class, 'method']`
  / `['Class', 'method']` inside a function/method body — the `usort` / `uasort` /
  `preg_replace_callback` / `array_map` comparator idiom — emits a REFERENCES edge to the method
  (Magento dogfood, panel R53). So a protected/private method invoked only this way is no longer
  flagged dead.
- **Not yet covered (known recall gaps, panel R57):** (a) a **bare-string function callable**
  `usort($x, 'topcmp')` / `call_user_func('topcmp')` — a project *global function* used only via a
  plain string name is still flagged dead; (b) an array callable at **module/file scope** (not
  inside a def), since the callable scan runs over def bodies (`_direct_refs`), not module-level
  code (`_module_uses`). In practice (b)'s targets resolve to public/exported symbols, so a
  false-dead is unlikely; (a) bites only a project global function referenced *exclusively* by a
  bare-string callback.
- **Decision / rationale:** ship the array form (the confirmed, high-value idiom) cardinal-safe
  and precise; defer bare-string function callables (noisier — every string literal matching a
  function name would over-root) and module-scope callables to a follow-up. Documented here so a
  flagged-dead function used only via a bare-string callback is a known gap, not a silent error.
- **Escape hatch:** treat a `find_stale` hit on a function you know is used via `'name'`-string
  `call_user_func`/`usort` as a false positive until the follow-up lands.

### Framework annotations/attributes/decorators are rooted (Java/C#/JS/TS)
- **Concern:** a method/class the framework invokes by *reflection or routing* — marked by an
  annotation (`@PostConstruct`, `@EventListener`, JPA `@PrePersist`, JMH `@Setup`), an
  attribute (`[OnSerializing]`, `[ModuleInitializer]`, BenchmarkDotNet `[Benchmark]`), or a
  JS/TS decorator (NestJS `@Controller`/`@Get`, Angular `@Component`/`@HostListener`, TypeORM
  `@Entity`) — has a free-form name the convention misses, so it (and its callees) was flagged
  dead (multi-language hunt: gson `@PostConstruct`, Newtonsoft `[OnSerializing]`, NestJS route
  handlers).
- **Decision:** root by a curated per-language set of framework annotations/attributes/
  decorators (role `callback`). The sets cover the dominant frameworks, not every library.
- **Rationale / gaps:** these markers denote framework contracts, so a definition is a genuine
  entry point; rooting only ever *adds* roots (cardinal-safe). NOT yet covered: a *custom*
  user-defined decorator/annotation, C#'s named serialization callbacks discovered only via a
  `[method]` reference, and JS/TS *metadata-only* decorators (`@Version`) that enhance but
  don't invoke. Pin those in `stitchgraph.toml [entry_points]`.

## Cost-of-fix exceeds value

### An exported name shared by an unrelated class over-roots that class (incl. its inherited methods)
- **Concern:** Python export tracking (`__all__` / re-exports) is by **unqualified name** — a
  flat `exported_names` set. If `pkg/__init__.py` exports `Widget`, then an *unrelated*
  `class Widget` in another module is also treated as exported: its public methods, and (since
  the inherited-public-method rescue was added) its first-party ancestors' public methods, get
  the `exported` root and are hidden from `find_stale`.
- **Decision:** keep the flat name match; it is **cardinal-safe** (over-rooting only ever
  *masks* dead code, never flags live code dead). The precise fix — module-qualified export
  resolution (which re-export/`__all__` entry binds to which class id) — is a real refactor.
  A naive "skip on name collision" guard is *rejected*: it would fail to root the genuinely
  exported class's inherited methods, re-introducing a cardinal false-dead. So the safe
  direction is to over-root the rare collision, and this is the motivating case for the
  future qualified-export work. (Cosmetic sibling: a `@Controller export class` records only
  the `exported` role, not also `callback` — liveness is identical, the role label differs.)

### A console-script target maps to its module by path suffix (a shadow copy is over-rooted)
- **Concern:** a `[project.scripts]` target `pkg.mod:main` (issue #21) is matched to the
  node by module-path **suffix** (`…/pkg/mod.py`), so a *second* copy of that module —
  `vendor/pkg/mod.py`, a `tests/pkg/mod.py` fixture — also gets the `script` root, hiding a
  genuinely-dead `main` in the copy.
- **Decision:** match by suffix and tag **all** files that match (over-mark), rather than
  resolving the true import path.
- **Rationale:** distinguishing the real module from a vendored/test copy needs full
  import-path resolution (which dir is on `sys.path`), which is disproportionate. Crucially,
  tightening toward picking *one* file risks **failing to root the real entry point** when
  the layout is unusual (`src/`, namespace packages) — which re-introduces the exact #21
  false-dead (a live CLI `main` flagged dead). Over-rooting a shadow copy is the
  precision-safe direction; under-rooting the real one is not. A `Class.method` target *is*
  matched exactly (by qualified name), and a same-named function in an unrelated module is
  not matched — only same-suffix path copies are.
- **Escape hatch:** pin the real target in `stitchgraph.toml [entry_points]`; the
  over-marked copy surfaces only as *not flagged*, never as a confident verdict.
- **Related (same precision-safe class):** a class instantiated in an `if __name__ ==
  "__main__"` block is rooted by **bare name** (it is rooted as a `main` entry, then
  resolved by name), so a same-named class in another module is
  also kept live. Name-based is required here (a `__main__` block legitimately instantiates
  classes imported from other modules), so this is deliberate over-marking, not tightened.
  The rescue is bounded — it roots the class and the methods it *invokes* (names in the
  `__main__` block), not every public method — so an unrelated class's uninvoked methods
  still flag.
- **Related (a `[project.scripts]` target that is a bare callable class):** a spec of the
  form `cmd = "pkg.mod:MyClass"` (a class whose `__call__` is the entry point, no
  `.method`) does **not** get a `script` role — `_apply_script_roles` builds its lookup
  from `FUNCTION`/`METHOD` nodes only. If `MyClass` is neither exported (`__all__`) nor
  instantiated in `__main__`, its methods surface as **advisory** stale candidates
  (confidence 0.6, `needs_review=True`) — never a confident dead verdict, so the cardinal
  invariant holds. This pattern is rare (virtually all console-script targets are
  functions). **Escape hatch:** export the class, or pin it in
  `stitchgraph.toml [entry_points]`.
- **Related (an `__all__` export name shared across modules):** `exported_names` is a
  project-wide name set, so if `api.py` declares `__all__ = ["process"]`, a same-named
  `process` in an unrelated module also receives the `exported` role and is treated as a
  root. The effect is a **false negative** (a genuinely-dead `process` elsewhere isn't
  flagged), never a false-dead — the same precision-safe over-rooting class as the `__main__`
  and console-script cases above. Scoping `exported_names` per-file is deferred because the
  package `__init__.py` re-export pattern (`from .sub import X` then `__all__ = ["X"]`)
  legitimately needs the cross-module match. **Escape hatch:** rely on the advisory's
  `needs_review`, or pin the real roots in `stitchgraph.toml [entry_points]`.

### `find_holes` reports edit-orphaned references, not first-index dangling calls
- **Concern:** `find_holes` returns empty on a freshly-indexed project even when there's
  a textbook call to an undefined function — so "0 holes" can read as "no broken wiring"
  when the dangling-call detector simply didn't fire (issue #20).
- **Decision:** both extractors **drop** a call whose name resolves to no project symbol
  (no `dst_id=NULL` edge is recorded), so the hole substrate `find_holes` reads
  (`unresolved_edges()`) is only populated by the incremental updater orphaning an edge on
  a later delete/rename. `find_holes` is therefore an *edit-orphaned-reference* detector,
  not a first-index "call to an undefined/stdlib name" detector.
- **Rationale:** recording every unresolved call would emit a hole for every
  `len()` / `os.path.join()` / third-party call — overwhelming noise in the
  precision-unsafe direction. The dangling-call cost isn't worth that. Crucially, the
  *reachable-stub* "landmine" from design §6.D **is** delivered — by `scan`, which flags a
  reachable `NotImplementedError`/`pass` body as a `live_stub` (🔴 when its liveness rests
  on confident edges, 🟠 via an inferred path). So the headline `is_stub ∧ reachable`
  signal is available today; it just lives in `scan`, not `find_holes`.
- **Escape hatch:** use `scan` for reachable stubs and structural issues; pin expected
  roots in `stitchgraph.toml [entry_points]`; `find_holes` after edits surfaces references
  a delete/rename orphaned.

### Module-level uses are attributed; only purely-runtime binding isn't
- **Now attributed (kept live):** every statically-visible module-level use — a
  dispatch/registry literal (`HANDLERS = {"a": handle_a}`), a table of constructed
  objects (`SPECS = {... LangSpec(...) ...}`), a top-level instantiation
  (`REGISTRY = Builder()`), a subscript assignment (`REGISTRY["a"] = handle_a`), any
  top-level call, AND a **module-level decorated def** (`@register("a") def handle_a`,
  `@app.get(...) def view`) plus the decorator name itself — is edged to the module node
  and propagates liveness when the module is loaded. The decorated-def edge is INFERRED
  (the decorator certainly runs, but whether it registers vs merely wraps is heuristic),
  so a decorated *stub* stays ORANGE under the provenance ceiling, not RED.
- **Still not traced (rare):** a symbol bound into a registry *purely at runtime* with no
  syntactic module-level use — e.g. `register(handler)` called from inside another
  function that runs later, or attribute reassignment (`obj.method = patched`). Same class
  as dynamic dispatch / monkeypatching; surfaces as `needs_review`, not a confident verdict.
- **Escape hatch:** pin the symbol in `stitchgraph.toml`, or `ingest_trace`.

### Plugin-loader frameworks that dispatch by string name (Salt, pluggy, entry-point registries)
- **Concern:** some frameworks invoke functions purely by **string name through a loader** —
  Salt's loader resolves `state.apply` / `pkg.install` to module functions by name at runtime;
  pluggy/`importlib.metadata` entry-point systems do the same. There is no syntactic call site
  for the loader to follow, so nearly every public function of such a project looks dead to a
  static call graph. The multi-repo Python hunt measured this directly: `find_stale` on Salt
  3008 flagged **3,907** functions — overwhelmingly real, loader-dispatched execution-module
  functions, not dead code.
- **Decision:** do **not** special-case individual frameworks' loaders (Salt's `__virtualname__`,
  pluggy's hookspecs, …) in the core — that is unbounded and brittle. The static graph reports
  what it can see; the loader edge is genuinely invisible without modeling each framework.
- **Escape hatch:** pin the public surface as roots — `stitchgraph.toml [entry_points]` globs
  (e.g. every `salt/modules/*` function), or feed a runtime `ingest_trace` from a test run so the
  loader-invoked functions are marked `runtime`-live. Treat a bare `find_stale` on a
  loader-driven project as "internal-only candidates" rather than a dead-code verdict.

### A coverage trace from an unrelated tree sharing a path tail can mis-attribute
- **Concern:** `ingest_trace` matches a coverage file's paths to indexed nodes by exact
  root-relative path first, then — only when *no* file matched exactly (the coverage was
  recorded under a different root, e.g. CI vs local absolute paths) — by path **suffix**.
  A single coverage file from an *unrelated* project that has zero exact matches but shares
  a trailing path (`.../subdir/a.py` vs the indexed `subdir/a.py`) is, from paths alone,
  indistinguishable from a legitimate cross-root ingest, so its lines can be attributed to
  the look-alike file (marking it `runtime`, raising `find_stale` confidence to 0.78).
- **Bounded:** the common case — coverage generated *for this project* (paths align with the
  index root) — is fully precise: any exact match disables the suffix fallback, so a
  non-matching node is treated as genuinely uncovered (panel R35A). The residual requires a
  trace whose paths align with *no* indexed file yet coincidentally tail-match one.
- **Direction:** the mis-attribution marks code *live* (suppresses dead-code findings — the
  precision-over-recall direction), never flags live code dead.
- **Escape hatch:** ingest a coverage report generated for the indexed tree (its paths then
  match exactly); don't point `ingest_trace` at another project's report.

### A function named identically to its own module can spawn a spurious within-file edge
- **Concern:** when `compute.py` defines `def compute()`, the MODULE node and the FUNCTION
  node share one id (`compute.py::compute`). Module-level executable code (e.g. a
  `__main__` block) is attributed to the module node, so a call made there is mis-attributed
  to the same-named function — which, combined with a real call back, can show a spurious
  `cycle` in `scan` (ORANGE). Call resolution itself is deduped, so this no longer mislabels
  call provenance or demotes a stub's urgency; only the structural-cycle artifact remains.
- **Decision:** accept the narrow artifact rather than rework the module-id scheme.
- **Rationale:** cardinal-safe (no live code flagged dead; it only *adds* an edge), and it
  needs the exact coincidence of a function named like its file plus a within-file
  `__main__` call chain. The `main.py` + `def main()` case produces only a harmless
  self-loop (single-node SCCs aren't reported).

### Incremental `replace_file` matches a full reindex (homonyms, dispatch, dunders)
- **Was:** the incremental updater could not reconstruct the extractor's resolution
  semantics, so an `(src, relation, dst_symbol)` group drifted from what a full `reindex`
  produces — a later homonym left a new definition dead (false dead), or a precise
  import/`self`-dispatch edge was widened across unrelated same-named members (inflation),
  or an incrementally-added subclass override of a dispatched member was orphaned (false
  dead) — panels R18–R22.
- **Now:** each edge records whether it was resolved **by name** (`name_based`) or
  **precisely** (by import path, scope, declared type, or structural seeding). On
  `replace_file`, `_rewiden_resolved` re-normalizes only the name-based edges (rebuilding
  them AMBIGUOUS over all current candidates, or to a single edge when one remains), while
  precise edges are kept bound to their target exactly as a full reindex would; and
  `Store._propagate_overrides` re-derives inheritance-aware override edges from the store's
  INHERITS graph, mirroring the extractor. An incremental sequence therefore converges to a
  full reindex: `find_stale` is identical, and metrics (fan_in/fan_out/edges) match.
- **Forward references (handled):** a *precise* import/call to a file indexed *after* the
  importer is a forward reference, not a deletion. `_invalidate_dangling` keeps such a
  precise edge bound to its target id (which resolves once that file is indexed) instead of
  nullifying it and re-resolving by name — the latter would bind it to an unrelated
  same-named symbol and inflate that symbol's `fan_in` (panel R24A). Only genuine deletions
  (the target lived in the file being replaced) and name-based edges revert to holes, so an
  incremental sequence converges to a full reindex on `find_stale` AND on degree metrics,
  in any file order.
- **Residual — deleting an imported module (non-blocking, library-only):** when a file
  whose symbol was imported elsewhere (`from util import helper`) is *deleted* via
  `Store.replace_file(file, [], [])`, the now-dangling precise import is reverted to a hole
  and `_resolve_worklist` may re-bind it by name to an unrelated same-named symbol in
  another module, over-approximating that symbol's `fan_in` by one. It is **non-cardinal**
  (over-approximation — never flags live code dead), affects only the incremental
  `replace_file` deletion path (the shipped CLI/MCP always full-reindex), and self-corrects
  on the next full reindex. It is left as-is because the precise-import module context is
  not recoverable once the target is gone, and the candidate fixes (suppressing the
  re-bind) would break legitimate forward-reference import resolution — risking a far worse
  *false-dead* (cardinal). Trust the per-edge `provenance` (the phantom is `ambiguous`/
  name-based) and prefer a full reindex when exact deletion-time metrics matter.
- **Residual — `find_holes` count after an incremental delete (non-blocking, library-only):**
  deleting a file via `Store.replace_file(file, [], [])` leaves the *precise* (import-by-path)
  edges that targeted it dangling at their exact id (kept, by design, so they auto-revalidate
  if the file is re-added — panels R24A/R29A). `find_holes` counts each such dangling edge, so
  after a delete it can report MORE holes than a full reindex of the same end state, which
  re-resolves the importing file's references and dedups them to one unresolved symbol (panel
  R37A). This is **non-cardinal** and arguably more complete — the deleted target's importers
  genuinely contain broken references — but it diverges from full-reindex `find_holes` count.
  Same root and trade-off as the `fan_in`-on-delete residual above (nullifying precise edges
  to converge the count would lose the precise re-add revalidation and risk a false-dead).
  Affects only the incremental `replace_file` delete path; the shipped CLI/MCP full-reindex,
  and it self-corrects on the next full reindex.

### Cross-language resolvers are heuristic
- **Concern:** route / HTML-form / JS-fetch / SQL / ORM / event edges are
  `INFERRED` with confidence < 1 and can mis-link.
- **Decision:** detect the common framework shapes by pattern; mark every such
  edge `INFERRED` and propagate the lower confidence into `trace_path`.
- **Rationale:** a full parser/semantic model per framework is enormous; the
  pattern detectors cover the shapes people actually write, and the honest
  confidence lets a consumer weigh the result.
- **Escape hatch:** trust the `confidence`/`needs_review`; add a resolver for your
  framework (a small plugin in `core/resolve/`).

### tree-sitter languages: imports/inheritance are partial
- **Concern:** some languages model the call graph but not imports or inheritance
  (see the matrix in `docs/LANGUAGES.md`).
- **Decision:** ship the call graph first for every language; add imports /
  inheritance per language incrementally.
- **Rationale:** calls already resolve cross-file by name, so dead-code,
  `orient`, and `trace_path` work without import modelling; the rest is additive
  per-language work, not a correctness gap.
- **Escape hatch:** contribute the relevant `LangSpec` fields (`imports`,
  `heritage`).

### Very large monorepos (RESOLVED in v2 by the streaming indexer)
- **Was:** `reindex(store, path)` extracted the whole tree into one in-memory graph and held
  all nodes + edges before writing, so peak RAM scaled with total nodes+edges. Magento 2.4.7
  (24,401 PHP files) and even its `app/code` subtree exceeded ~12 GB. The hog was the edge
  list — name-based ambiguous fan-out produces ~15.5M edges on a single Magento module.
- **Now:** `reindex(store, path, streaming=True)` streams the graph to SQLite instead of
  building it in Python first — each file's AST/parse-tree + source are dropped after pass 1,
  and edges are deduplicated per-source on the fly and written in committed batches, so peak
  RAM tracks one file's working set, not the repo. Measured on the Magento `Framework` core
  (4,304 files): **3,183 MB → 269 MB (~12×), byte-identical output** (verified row-for-row),
  ~40% slower. The streamed index is pinned equal to the in-memory one by a differential
  oracle (`tests/oracles/test_streaming_differential.py`). See
  [`docs/V2_STREAMING_DESIGN.md`](docs/V2_STREAMING_DESIGN.md).
- **Note:** streaming realises the saving only with an **on-disk** `Store` (a `:memory:` DB
  necessarily holds the rows in RAM). It is currently opt-in via `streaming=True`; making it
  the default above a file-count threshold is the final v2.0.0 step.

## Behaviour is the contract (changing it would silently break callers)

### Read-only on analyzed source
- **Concern:** stitchgraph reports dead code and holes but never removes them.
- **Decision:** every operation is advisory and writes only to its own index DB;
  it never edits or deletes analyzed source.
- **Rationale:** a confident-but-wrong "delete this" is destructive and
  irreversible; the human/LLM must make the call. This is a safety contract, not
  a missing feature.
- **Escape hatch:** act on the ranked candidates yourself.

### Matrices are derived, never stored
- **Concern:** there is no persistent matrix; sweeps recompute it each call.
- **Decision:** adjacency lists in SQLite are the source of truth; relation
  matrices are derived on demand.
- **Rationale:** storing the matrix as primary state makes incremental updates
  expensive (the one thing matrices are bad at). Deriving keeps single-file
  reindex cheap.
- **Escape hatch:** none needed.

### `get_matrix` refuses a broad scope
- **Concern:** `get_matrix` won't return a whole-repo matrix; it errors above a
  node limit.
- **Decision:** return a bounded sparse submatrix (plus a small dense grid for ≤12
  nodes); refuse broader scopes.
- **Rationale:** a dense N×N matrix is token-expensive and unreadable by an LLM —
  the explicit dense anti-pattern. The whole-repo view is a *summary*
  (`orient`, `summarize_subsystem`), not a raw matrix.
- **Escape hatch:** narrow the scope to a file/class; use `orient`.

### No embedding model is bundled
- **Concern:** `find_similar` ranks by token similarity, not neural embeddings,
  out of the box.
- **Decision:** token similarity is the default; a dense embedder is pluggable.
- **Rationale:** bundling a model means shipping torch / large weights with
  license and offline-download headaches, for a feature most queries don't need.
- **Escape hatch:** `similar.set_embedder(fn)` with any backend, or install
  `model2vec` / `sentence-transformers`.
