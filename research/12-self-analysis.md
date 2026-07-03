# stitchgraph ⊳ stitchgraph — full self-analysis (v3.23.0)

Turned the whole 30-op toolset on stitchgraph's own source. Static ops on the current tree; runtime/POD
ops on a **fresh** per-test coverage capture of the v3.23.0 suite (2338 tests × 792 functions, 2329
passed). Two genuinely actionable findings, plus confirmation the architecture is healthy and the new
POD code shipped under test. This is a dogfood in the strongest sense — the tool auditing its own author.

## Actionable findings

### 1. `scan` false-positive: decorator-registered callback with an idiomatic empty body flagged RED (precision bug)
`scan` reports `adapters/cli.py::build_app._root` as a **RED `live_stub`** — "unimplemented body on a
reachable path." But `_root` is a Typer `@app.callback(invoke_without_command=True)` whose body is
intentionally `pass`: all its behaviour is declared through the `--version` option + its eager callback.
An empty body on a decorator-registered callback/route is idiomatic (Typer/Click/Flask), not an
unimplemented stub. **Fix:** the `live_stub` oracle should not RED-flag a function whose body is empty
when it is registered via a decorator that carries the behaviour (callback/route/command). Found by
dogfooding — no panel had caught it.

### 2. `find_gaps`: real coverage gaps (reachable but never executed)
12 `untested_live` functions under `src/stitchgraph` (1 `untested_dead`). The genuine ones worth a test:
- `model.py::Edge.to_dict`, `model.py::Edge.resolved` — model methods no test exercises directly.
- `entrypoints.py::EntryPointDetector.detect` — the base-class method (subclasses are tested).
- `structure_{cpp,js,ruby}.py::_first`/`_last` — tiny cursor helpers never hit on the tested paths.
`operation` / `operation.deco` show as gaps because the decorator runs at import time, not inside any
per-test context — a measurement artifact, not a real gap (worth noting as a `find_gaps` caveat).

## Confirmations (healthy, no action)

- **Architecture is stable and clean** (`find_modes`): 10 behavioural modes, identical shape to the
  v3.21 snapshot — Python extraction (45.9%), tree-sitter polyglot (11.9%), one mode per body-matrix
  language (Rust/C++/C#/Java/Ruby), shared VFG/store core. The 30-op growth did **not** distort the
  behavioural structure.
- **`find_holes`: 0** — no dangling references anywhere.
- **`find_stale`: 1 candidate** (`treesitter.py::supported_languages`) — genuinely uncalled internally
  (public API); the op stays honest (needs_review, conf 0.6, never asserts dead).
- **`find_core` = store + envelope**: `Store.__init__`/`_migrate`/`_canonical_columns` (~60%),
  `Result.__post_init__`, `ok` — every test builds a store and wraps a Result. Expected.
- **`runtime_risk` hotspots**: `treesitter.py` (churn 123 × beh 12125), `python.py` (62 × 16203),
  `store.py`, `operations.py` — the extraction core is where churn meets breadth. Accurate and unsurprising.
- **`find_coupling`**: the one strong cross-file signal remains `config._load ⇄ envelope.set_review_threshold`
  (463 shared tests, no static edge) — config drives the envelope's global review threshold; real
  implicit coupling the call graph can't see, and a candidate for making that dependency explicit.
- **`coverage_drift` (pre-v3.22 → v3.23): 28 gained, 0 lost** — the gained set is exactly the new
  `coverage_query.py` + POD functions. The toolkit's own additions came in **under test**, and nothing
  lost coverage. A clean, self-demonstrating release-health check.
- **Test-suite shape**: `test_order` → 66/924 tests are a minimal function cover (93% add no *new*
  function coverage — but that tail is mostly parametrized/edge-case tests, valuable beyond coverage);
  `redundant_tests` → 128 identical-profile groups (biggest 18, parametrized); `find_outlier_tests` →
  most tests are unique-behaviour (few smoke), i.e. a focused suite, not a broad-smoke one.

## `scan` precision note

193 scan findings, but **182 are GREEN `god_object`** flags — a very sensitive detector across ~30
files. All low-urgency (correctly ranked green), but the volume is low-signal; a threshold review would
cut noise. 9 cycles (mostly green) + 1 data_loop + the 1 red live_stub (finding #1) round it out.

## Verdict

The dogfood surfaced **one real precision bug** (`scan` live_stub on decorator callbacks) and **a short
list of true coverage gaps** (`Edge.to_dict`/`Edge.resolved` etc.) — both concretely fixable — while
confirming the codebase is structurally healthy and the v3.22/3.23 additions are tested and
regression-free. Net: the POD toolkit, pointed at its own author, produced findings a careful reader
would have missed (the coupling, the drift, the minimal cover) — exactly the LLM-complementary value the
research thread predicted.

## Outcome — findings acted on (v3.23.1)

The loop closed the same day: both actionable findings were fixed and re-gated.
- **Finding #1 (scan live_stub FP)** → fixed in `_is_stub` (`extract/python.py`): an empty
  (`pass`/`…`/docstring-only) body under a **call/attribute decorator** (`@app.callback()`,
  `@app.route()`, `@foo.register`) is no longer a stub — the decorator carries the behaviour. Bare
  `pass`/`@property pass` and `raise NotImplementedError` (even decorated) stay stubs. Whole-repo
  differential: **exactly one** verdict changed (`cli.py::build_app._root`, the intended target),
  zero collateral; `scan` now reports **0 RED live_stubs** on stitchgraph.
- **Finding #2 (coverage gaps)** → tests added for `Edge.to_dict` / `Edge.resolved`
  (`tests/test_selfaudit.py`). (`EntryPointDetector.detect` = Protocol `…` stub and the `@operation`
  decorator = import-time artifact — not real gaps, documented as `find_gaps` caveats, not "fixed".)

Shipped as **v3.23.1** through the same discipline as every other change (full suite + ruff/mypy + two
clean adversarial panels). That is the point of the exercise: stitchgraph found a real bug in
stitchgraph, and the fix went back through stitchgraph's own gate.
