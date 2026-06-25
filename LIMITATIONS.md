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

## Cost-of-fix exceeds value

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

### Incremental `replace_file` matches a full reindex for homonyms (fixed)
- **Was:** an edge uniquely resolved by an earlier `replace_file` was not re-expanded
  when a *later* update introduced a new same-named definition, leaving the new
  definition with no inbound edge (a false dead on the incremental path).
- **Now:** `replace_file` runs a `_rewiden_resolved` pass that rebuilds any
  `(src, relation, dst_symbol)` group whose name has more than one project node as
  AMBIGUOUS over *all* candidates — so an incremental update sequence converges to the
  same edges a full `reindex` of the final state produces. (A surviving edge after an
  ambiguous→single disambiguation keeps its AMBIGUOUS weight until the next reindex —
  honest, slightly under-confident, never a false dead.)

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
