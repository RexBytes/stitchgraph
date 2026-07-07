# 27 — The adversarial self-audit (v3.50.0)

**Question.** After five releases in one arc (v3.45–v3.49), what breaks if we stop
building and hunt our own bugs the way we hunt other projects'? The ritual is
codified in `docs/BUG_HUNT_PROMPT.md` (seven bug classes, CONFIRMED vs PLAUSIBLE
grading, exoneration required for dropped suspicions); this note records the run
of 2026-07-07: one self-pass over the freshest code (LSP arc + incremental
paths) and two parallel agent passes (LSP slice; coverage kits + calibrations
slice).

**Verdict.** 14 findings survived verification — 1 by the self-pass, 6 by the
LSP agent, 7 by the coverage/calibrations agent. All 14 are fixed with pinned
tests. The clustering is itself the lesson: 12 of 14 sit in the two *youngest*
subsystems (LSP backend v3.46, turnkey kits v3.49), and 9 of 14 are class-3
(environment assumptions) — code that had simply never met a hostile machine.

## Findings and fixes

### The convergence break (self-pass; class 5, amplification through defaults)

Under the AUTO LSP default (v3.48.0) a fresh index carries `source="lsp"`
edges, but `reindex_incremental` and `reindex_singlefile` (the watch loop)
never ran the LSP pass — an edit **silently stripped** the LSP edges from the
edited file, so a watch session slowly degraded to tree-sitter quality
file-by-file. Incremental-equals-fresh is the whole contract of the
incremental path. Fixed with `_lsp_edges_for(...)` in `operations.py` — the
same scoped pass both incremental paths now run for changed files only;
pinned by `test_incremental_keeps_lsp_edges` (store rows must equal a
fresh-index twin's, byte for byte).

### The LSP slice (agent 1: 5 CONFIRMED + 1 PLAUSIBLE, all fixed in `lsp.py`/`lsp_resolver.py`)

1. **Mute-server amplification (class 4).** A server that answers `initialize`
   but never definitions cost the full per-request timeout per site — 15 s ×
   up to 20k sites is *days*, silently, under a default-on feature. A dead
   process fails fast; an alive-but-silent one did not. Fix: consecutive-
   timeout counter on the client + a circuit breaker in the resolver (3
   consecutive full timeouts → decline the server, report
   `"server stopped answering (circuit breaker)"`). Framing loss (unparseable
   `Content-Length`) now kills the process outright so `available` flips
   False and every waiter fails fast — one glitch no longer converts a
   healthy server into a mute one.
2. **UTF-16 columns (class 6, representation drift).** LSP positions default
   to UTF-16 code units; `_columns` yielded Python `str` indices (code
   points). Any astral character before the callee on the line — an emoji in
   a log string — shifted the query column and the definition answer. Fix:
   `len(text[:m.start()].encode("utf-16-le")) // 2`.
3. **Warm-up burned the deadline on honest emptiness (class 4).** A fast,
   healthy server whose true answer at the probe position is "nothing
   in-root" never produced two equal *non-empty* answers, so `warm_up` spun
   the whole 30 s deadline on every reindex. Fix: 4 consecutive empty answers
   (~2 s) also count as ready.
4. **AUTO gate was per-command, not per-extension (class 5).** Disabling
   `.ts` in `[lsp.servers]` removed `typescript-language-server` from the
   probe set entirely — killing AUTO for `.js`/`.jsx`/`.mjs`/`.cjs`/`.tsx`
   siblings that share the command. Fix: `any_server_available` computes
   effective commands per extension.
5. **Late replies leaked into `_pending` (class 7).** A reply arriving after
   `_wait` gave up sat in `_pending` forever (slow leak) — and a hypothetical
   id-reuse would mis-deliver it. Fix: `_abandoned` set; late replies are
   dropped on arrival.
6. **Server→client requests went unanswered (class 3, PLAUSIBLE→fixed).**
   `workspace/configuration` / `client/registerCapability` requests were
   silently dropped; a server that synchronously awaits the reply (gopls
   does) would deadlock into the mute-server path. Fix: polite
   `-32601 method not supported` error reply from the reader thread.

All six are pinned in `tests/test_self_audit.py` against `fake_lsp_server.py`,
which grew three modes for the purpose: `slow` (late replies), `needy` (blocks
on a server→client request), `corrupt_frame` (framing loss mid-session).

### The coverage-kit slice (agent 2: 6 CONFIRMED + 3 PLAUSIBLE; 6+1 fixed)

The theme: **the kits were validated on the machine that built them** (fd, the
Go corpus, jest/vitest fixtures — research/26) and inherited that machine's
environment as silent assumptions.

1. **Python kit: confidently-empty artifact without pytest-cov (classes 1+3).**
   `_PY_RUN` ran `pytest --cov=. --cov-context=test` but installed only
   `coverage pytest`. `--cov*` are pytest-cov flags — pytest exits 4 *before
   running any test*, `|| true` swallowed it, and the converter wrote
   `{"tests": {}}` with exit 0 and a success message. The worst shape a bug
   can take in this project: a wrong answer delivered confidently. Fix:
   install pytest-cov, guard on `.coverage` existing, and **both converters
   now refuse to write a 0-test artifact** (exit 1, no file) — the loud-
   failure backstop for the whole class.
2. **Docker option dead on arrival (class 3).** `read_only: true` rootfs vs.
   capture loops that write `covdata/`, `tests.txt`, `target/` into `/work`;
   `network_mode: "none"` vs. dependencies fetched at *runtime* (go modules,
   npm packages, rustup components). No language's compose run could succeed.
   Fix: dependencies bake at image-build time (network exists there; the run
   stays network-less), the container fs is writable-but-disposable (`--rm`,
   non-root, caps dropped), `chown -R runner /work`.
3. **Undocumented copy step (class 3).** Scripts referenced
   `to_canonical.py`/`spans.json` by bare name and the README said
   `bash run_coverage.sh` — the repo's own e2e test quietly copied three kit
   files into the project root first. Fix: scripts self-locate
   (`KIT="$(dirname "${BASH_SOURCE[0]}")"`), the Docker build context is the
   project root (computed at generate time), and the e2e test no longer
   copies anything — `bash kit/run_coverage.sh` from the root just works.
4. **Stale `covdata/` misattribution (class 7).** No loop cleaned `covdata/`;
   a re-run after removing/renaming tests attributed the *previous* run's
   captures to whatever test now occupied that index — plus phantom
   `"200.out"` test ids past the end. Fix: `rm -rf covdata` at loop start.
5. **JS filters over-matched (class 3).** Test-file paths were passed as
   jest's unanchored `testPathPattern` regex / vitest's substring filter —
   `api/user.test.js` also ran `webapi/user.test.js`, merging two files'
   coverage under one id. Fix: jest `--runTestsByPath` (exact), vitest gets
   the absolute path.
6. **`sed "s|^$PWD/||"` (class 3).** Interpolating the cwd into a sed program:
   a `|`, `[`, or `.` in the project path broke enumeration or silently kept
   absolute paths as test ids. Fix: shell parameter expansion
   (`${p#"$PWD"/}`), no regex anywhere near a path.
7. **Monorepo root mismatch (class 3, PLAUSIBLE→fixed).** `spans.json` keys
   are index-root-relative; a capture running in the Go-module/JS-package
   subdirectory produced paths without the prefix — every lookup missed and
   the artifact was 0-test "success". Fix: unique-suffix fallback in the
   converter's span lookup (+ the 0-test refusal above turns the residual
   ambiguous case into a loud failure).

### The calibration gap (agent 2; class 2, degraded-mode divergence)

**scan's god-object detector counted test-suite edges as coupling mass** — the
exact failure orient fixed in research/25 ("Store.close crowned by 1,117
tests"), never applied to scan. A production helper with 2 src callers, 10
test callers and 6 callees flagged ORANGE on suite mass alone (verified by
constructed store). Fix: candidates discovered on raw bulk degrees are
re-checked against **non-test fan-in** (same per-candidate SQL that already
computes the confident share, one temp-table probe extra); coupling that melts
away without the suite never flags, reported as
`god_objects_test_mass_suppressed`.

## Exonerations (the suspicions that dissolved)

Recorded per the prompt's rule — a dropped suspicion must say why. The agents'
notable ones: `while read` loops vs. names-with-spaces (quoted, `IFS=` — fine);
the estimator's sampling/scale arithmetic (`np.linspace` step ≥ 1, self-hit
subtraction verified in the sidecar); `get_callees` confident-empty "missing"
unresolved calls (call holes are dropped at extraction *by design*, live-probed);
Go coverprofile filename parsing (`rsplit(" ", 2)` handles spaces); `rel_of`'s
`..` check (only misfires on a component literally starting `..`).

## Lessons

- **Field validation is not environment validation.** Every kit ran end-to-end
  on real corpora before shipping — on one machine, with its toolchain, from
  its directory. Nine class-3 findings later: the checklist now includes *the
  hostile-machine pass* (missing plugin, weird cwd, second run, monorepo).
- **`|| true` needs a loud floor.** Three findings shared the shape
  "best-effort step failed → success message anyway". Best-effort is right for
  per-test steps; the *artifact* write is where the loudness belongs
  (0-test refusal).
- **A calibration fixed in one consumer is a bug report against its twins.**
  The orient test-mass exclusion sat next to an identical scan gap for three
  releases. Grep for the principle, not the symptom.
- **Defaults amplify.** The mute-server cost existed opt-in since v3.46; AUTO
  (v3.48) turned it into a default-on liability. Flipping a feature default
  re-opens its failure-mode review.

**Cost.** ~2 agent-hours of hunting, one day of fixes, 11 new tests
(`test_self_audit.py` ×7, turnkey ×2, calibration ×1, llm_review ×1), three
fake-server modes. Fourteen real bugs for it — the ritual pays; scheduled to
re-run after each major arc.
