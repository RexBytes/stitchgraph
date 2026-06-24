# stitchgraph v1.0.2 — export-rooting & test call-receivers

A patch release fixing three cardinal false-deads (live code flagged dead by
`find_stale`) in the JS/TS/CJS and test-detection surfaces. No new operations, no
surface changes. **The cardinal invariant — live code is never flagged dead — is the
one release-blocking class, and all three fixes restore it**, verified in both
directions across four panels (FF–II) at full three-model diversity.

## How these were found — restoring panel diversity paid off immediately

1.0.1 shipped with the review panel running on two models (opus + haiku) because
sonnet's API had been unavailable. When sonnet was restored, the first full
three-model panel (FF) had opus and haiku both clean — but **fresh sonnet caught two
cardinal false-deads they had collectively missed across the entire eight-panel 1.0.1
cycle**, one of them a regression the 1.0.1 polyglot work itself introduced. The
follow-up panels (GG) surfaced a third (a sibling of the first). This is the
"diversity is the signal" lesson made concrete: a returning reviewer with no memory of
the recent cycle sees what the incumbent pair had gone blind to.

## What was wrong, and what changed

### JS/TS/CJS public exports flagged dead (precision)

`find_stale` roots the public API of a module (an exported symbol is reachable from
unknown external callers, so it is never dead for lack of internal callers). But only
two export forms were recognized — `export { X }` and inline `export class/function X`.
Every other way to make a symbol public left it flagged dead:

- **`export default Foo;`** where `Foo` is defined earlier in the file — *the* canonical
  React/Angular/Vue/Node idiom. (Pre-existing since 1.0.0.)
- **CommonJS** `module.exports = Foo`, `module.exports = { A, B }`, `exports.x = Foo`,
  `module.exports.x = Foo` — the dominant pattern in the large CJS ecosystem.
- **TypeScript** `export = Foo` (CommonJS-interop export-assignment).

**Fix:** `_reexport_names` now collects the public symbol(s) from all of these forms and
feeds them into the existing reexport→`exported` path. Matching is precise: anonymous
defaults (`export default () => {}` / `{…}`), an anonymous function/class assigned to
`module.exports`, and a local variable named `exports` are **not** rooted, so
genuinely-dead code still flags. The export-rooting class was then exhaustively
enumerated (19 forms across `export`/`export default`/`export =`/`module.exports`/
`exports.*`/barrel re-exports) and confirmed closed.

### A test's class-under-test flagged dead (precision)

This one was a regression introduced by 1.0.1's call-based test detection. In a suite
that defines no named test function — Jest/Mocha `test()`/`it()`/`describe()`, Ruby
RSpec — a class referenced as a call receiver inside a test block (`Service.run`) was
linked to its *method* but not to the *class*, so the live class was flagged dead.

**Fix:** the test-file module scan (`_module_uses`, formerly `_module_calls`) now also
collects name-references (mirroring the per-function `_direct_refs`), so the class
receiver is rooted. As part of the same fix it no longer descends into uncalled
function-expression bodies (`const helper = () => {…}` is itself a definition, scanned
on its own), so a dead class referenced only inside an uncalled helper still flags —
the precision boundary is preserved.

## Limitations (unchanged direction — err-safe)

A few obscure export indirections are intentionally not rooted because they are rare and
closing them generically risks the opposite (hiding dead code): a `module.exports = X`
assignment *buried inside a function body*, `export * from './m'`,
`Object.assign(module.exports, {…})`, and `module.exports = ns.Member` via a
locally-built namespace object. All surface only as `needs_review` advisories at 0.6
confidence — never a confident "dead" verdict — and are covered by the documented
"module-level uses aren't attributed" limitation. See `LIMITATIONS.md`.

## Verification

`pytest` 169 passed (regression tests added per finding, each pinning both directions) ·
ruff clean · mypy clean. Four confirmation panels at full three-model diversity
(opus + sonnet + haiku):

- **FF** — fresh sonnet found two cardinals (export default; test class-receiver) that
  opus + haiku missed across all of 1.0.1.
- **GG** — confirmed both FF fixes; opus + sonnet converged on a third cardinal (CJS
  `module.exports` / TS `export =`); fixed, plus an err-safe `_module_uses` tightening.
- **HH** — all three clean; sonnet enumerated all 19 export forms → export-rooting class
  declared **closed** (streak 1).
- **II** — all three clean, fresh independent confirmation (streak 2) → release gate met
  (readiness **RELEASABLE**).

Full trajectory in `REVIEW_HISTORY.md`.
