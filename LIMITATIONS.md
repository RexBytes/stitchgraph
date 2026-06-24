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
- **Residual tradeoff:** a *production* file that happens to live under a `test`/
  `tests`/`spec`/`__tests__` directory has its module-level calls rooted — over-marking
  (hides some dead code) in the precision-safe direction, never a false "dead".
- **Escape hatch:** pin the test in `stitchgraph.toml [entry_points]`, or
  `ingest_trace` a real test run to seed liveness from what actually executed.

### Ambiguous calls link to *all* same-named candidates
- **Concern:** one call produces several `AMBIGUOUS` edges, inflating reachability
  and `impact_of` blast radius.
- **Decision:** when a name resolves to multiple symbols, edge to *all* of them.
- **Rationale:** under-counting reachability would flag live code dead
  (destructive); over-counting only under-reports dead code (safe). The
  confidence/provenance on each edge records the uncertainty.
- **Escape hatch:** `--precise` disambiguates via go-to-definition.

## Cost-of-fix exceeds value

### Module-level uses aren't attributed
- **Concern:** a decorator or constructor applied at import (e.g. `@operation(...)`,
  a `SPECS = {... LangSpec(...) ...}` table) isn't a call inside any function, so a
  symbol used only that way can surface as a stale candidate.
- **Decision:** attribute call/reference edges only within function/method bodies.
- **Rationale:** modelling module-level execution order fully is disproportionate
  to the value; these cases surface as `needs_review`, not confident verdicts.
- **Escape hatch:** pin the symbol in `stitchgraph.toml`, or `ingest_trace`.

### Incremental `replace_file` resolves holes against the nodes present *now*
- **Concern:** `Store.replace_file` (the experimental single-file incremental
  updater) over-approximates an ambiguous hole to *all* candidates that exist when
  it runs — but if a *later* single-file update introduces a new same-named
  definition, an edge already uniquely resolved by an earlier update is not
  retroactively widened to include it.
- **Decision:** the worklist over-approximates at resolution time; it does not
  re-open already-resolved edges when a homonym appears in a subsequent update.
- **Rationale:** `replace_file` is not wired into any product path — `watch` does a
  full `reindex`, which always sees the complete symbol table and links to all
  candidates. Tracking enough provenance to retroactively re-expand individual
  resolved edges across updates is disproportionate for an experimental method
  whose wired alternative is already exact.
- **Escape hatch:** run a full `reindex` (the supported path) — it is authoritative
  for ambiguity; or call `replace_file` for the affected files once all definitions
  exist.

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
