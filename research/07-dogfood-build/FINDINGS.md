# Dogfood experiment: does stitchgraph help an agent *build* software?

**Question (maintainer, 2026-07-02):** build a package from requirements with two agents — one
with access to stitchgraph on its own growing codebase, one without — and see whether code
intelligence enhances the *implementation* process. Document each agent's process; compare.

Method: identical spec, same model (Sonnet), isolated build dirs, a **held-out acceptance test**
the agents never see (`roundN/acceptance.py`), first-to-pass is the race result, then compare the
`DEVLOG.md` each kept. Single trial per round (anecdotal, not a benchmark) — read as a mechanism
probe, not a measurement.

---

## Round 1 — greenfield build (~1000 LOC, 6 modules)

**Task:** a SPICE-subset circuit simulator (netlist parser → Modified Nodal Analysis → `.op` DC +
`.tran` RC via Backward Euler), CLI printing JSON. Spec: `round1/SPEC.md`. Grader: `round1/acceptance.py`
(3 hidden circuits: a 3k/1k divider, a 3-resistor series chain, a 2 ms-τ RC transient).

**Result — both PASS, control finished ~4% sooner:**

| Metric | A — with stitchgraph | B — control |
|---|---|---|
| Held-out acceptance (3 circuits) | ✅ ALL PASS | ✅ ALL PASS |
| Wall-clock | 415 s | **400 s** |
| Tool calls | 35 (6 stitchgraph) | 29 |
| Modules / LOC | 7 / 1065 | 6 / 870 |
| Own tests | 53 | 29 |
| RC numeric | 3.1601 | 3.1601 (identical) |

Same model + spec → both chose the same architecture (elements → parser → MNA → solver →
analyses → CLI), both derived the MNA stamps on paper, both used the worked examples as an early
smoke test. Both stayed oriented the **same primary way**: strict bottom-up build + a test suite
mirroring the module layout. The control edged the race by 16 s — partly *because* of A's 6 extra
stitchgraph queries.

**Where stitchgraph changed A's process** (from `round1/DEVLOG_A_stitchgraph.md`):

- ✅ **`find-stale` caught real dead code** — a speculative `NodeMap.node_names_in_order()` A had
  left unused; confirmed with grep and deleted. A: *"would not have noticed this by reading the file
  since it looked like a reasonable API to keep."* stitchgraph's core competency doing its job.
- ✅ **`find-holes = 0`** gave cheap wiring-integrity confidence before writing tests; **`orient`**
  confirmed the intended hub structure; **`impact-of assemble`** verified the numeric core was
  broadly exercised (13/53 tests).
- ⚠️ **`scan` false-positive** — flagged the 3 orchestration functions as "god objects" (expected
  fan-in for a tiny CLI); A triaged and dismissed it. A small noise cost.

**Verdict:** at ~1000 LOC / 6 modules / one session, **the codebase fits in the agent's working
memory, so the navigation benefit is muted** — no speed win (a wash, slightly negative from query
overhead), but a **modest quality/assurance benefit** (one genuine dead-code removal + cheap wiring/
coverage confidence). Matches the pre-registered "may not move the needle at this size."

**Caveats:** n=1; and A's larger test count (53 vs 29) is **not attributable to stitchgraph** — the
tool doesn't write tests; that's behavioural variance. The mechanism that *did* help (surfacing
forgotten code the author can't see by reading) is what should **compound on larger, longer-lived,
multi-session codebases** where the agent can't hold everything in context — which round 1 sits below.

---

## Round 2 — multi-session extension of an unfamiliar codebase

**Rationale:** round 1 was below the "can't hold it in context" threshold, so it under-tests the
hypothesis. Round 2 forces re-orientation: a **fresh agent** (no memory of the build) is dropped into
a larger, pre-built codebase it did not write and asked to make a **cross-cutting change** — the
scenario where `orient` / `get-callers` / `impact-of` should earn their keep. Both extender agents
get an **identical** frozen base; the only variable is stitchgraph access.

_Status: in progress — results appended below when the run completes._
