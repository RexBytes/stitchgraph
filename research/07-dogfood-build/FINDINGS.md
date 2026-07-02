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

**Setup.** A neutral builder agent first produced a larger base simulator (22 modules, ~1000 LOC:
R/L/C/V/I + `.op`/`.tran`/`.dc`, with a **registry-based extension pattern** — element + analysis
registries, base-class extension points, and an inline "how to add an element" docstring). Verified:
5/5 regression circuits pass, VCVS cases fail (no `E` element). Frozen and copied **byte-identically**
into two dirs. Task (`round2/CHANGE.md`): add a voltage-controlled voltage source (SPICE `E`) end to
end — parser + element type + an MNA branch-unknown stamp + all three analyses — without regressing.
Grader: `round2/acceptance_phase2.py` (5 regression circuits incl. RL transient + DC sweep, must
still pass; 2 new VCVS circuits). Designed cross-file gotcha: the netlist parser had node arity `2`
hardcoded, so a 4-node `E` line silently mis-parses unless the author finds that call site.

**Result — both PASS everything, control finished ~35% sooner:**

| Metric | A — with stitchgraph | B — control |
|---|---|---|
| Regression (5) + VCVS (2) | ✅ 7/7 | ✅ 7/7 |
| Wall-clock | 411 s | **304 s** |
| Tool calls | 43 | 43 |
| New tests added | 14 (→71 total) | 11 (→68 total) |
| Found the hidden call site (parser node-arity) | ✅ yes | ✅ yes |
| Regressions introduced | 0 | 0 |

**Both** correctly generalized the parser (`num_nodes`), added the VCVS branch-unknown stamp with the
right control coupling, and preserved all 57 pre-existing tests. **Both found the designed cross-file
gotcha** — but *not* primarily via structural queries: both leaned on the base's **own inline
extension-point docstring and the prior developer's DEVLOG** to orient, then B traced the parser's
token-slicing by reading and A confirmed the hubs with `orient`.

**Where stitchgraph changed A's process** (from A's own honest reflection, `round2/DEVLOG_A_stitchgraph.md`):

- ✅ **`orient` gave orientation-in-seconds** — pointed straight at `Element` / `AnalysisContext` /
  `MNASystem` as the load-bearing hubs *before reading a line*, matching what actually mattered.
- ✅ **`impact-of` as a regression net** — flagging the whole suite as blast radius was a forcing
  function against under-testing (A added 14 tests vs B's 11); **`find-holes`/`find-stale`** gave a
  fast clean post-edit signal.
- ➖ **`find-similar` added confirmation, not discovery** — surfaced the same files A had already
  found via the inline docstring (small, self-documented codebase).
- ⚠️ **A real limitation surfaced:** `get-callers "nodes"` **failed** because `nodes` isn't a unique
  symbol — bare common names need a qualified id or fall back to grep. *(Actionable product feedback.)*

---

## Cross-round conclusion

Across **both** rounds (build-from-scratch and extend-an-unfamiliar-codebase), the outcome was the
same: **both agents produced fully correct, acceptance-passing results, and the control finished
sooner** (4% round 1, 35% round 2 — the gap is stitchgraph's query overhead). No speed win in either
regime; stitchgraph's consistent, real contribution was **assurance and orientation, not speed or
discovery**:

- **find-stale** caught genuine dead code a reader would keep (round 1);
- **orient** collapsed "where do I start" to seconds in unfamiliar code (round 2);
- **impact-of** acted as a coverage/regression forcing-function (both rounds; A wrote more tests);
- **find-holes** gave a cheap "nothing dangling" wiring check after edits.

**Why the discovery win kept not materialising** — and the key methodological lesson: both codebases
were **small and self-documenting** (clean layering, inline extension-point docstrings, a prior
DEVLOG). Good documentation and clean structure substituted for structural queries, so direct reading
reached the same answers about as fast. stitchgraph's *discovery* edge (find every caller, trace
non-local coupling, spot dead/dangling code the author can't see) should dominate only when those
substitutes are **absent**: a **large, poorly-documented codebase with non-obvious cross-file
coupling** — precisely the conditions a controlled greenfield experiment keeps failing to create,
because agents build clean, legible code.

**Honest bottom line (n=1 per round):** for an *LLM agent* building or extending *well-structured,
in-context-sized* code, stitchgraph is a **safety net and orientation aid** (fewer missed dead-code /
wiring issues, faster onboarding, a nudge toward broader tests) at a **small time cost** — not an
implementation-speed multiplier. That matches stitchgraph's actual design pitch (honest, advisory
code intelligence), and it is a *different* value proposition from "makes the agent faster."

**To actually catch a discovery win**, a future experiment should: (a) use a **much larger,
deliberately under-documented** codebase with tangled cross-file coupling; (b) pose a change whose
call sites are **not** discoverable from a docstring (e.g. rename a widely-used symbol with
same-named decoys); (c) run **n ≥ 5** trials per arm to see past single-run noise; and (d) consider a
weaker/faster base model, where holding the whole codebase in context is less feasible.

**One concrete product bug for stitchgraph itself:** `get_callers`/`get-callees` on a **non-unique
bare name** (`nodes`) failed instead of returning the candidate set or asking for a qualified id —
worth a usability fix (disambiguate, or match all and label by qualified id).

_Artifacts: `round1/` and `round2/` hold the specs, held-out graders, and both agents' DEVLOGs._
