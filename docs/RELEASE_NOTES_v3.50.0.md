# v3.50.0 — the adversarial self-audit

*2026-07-07 · fourteen of our own bugs, hunted, verified, fixed, pinned ·
ritual: `docs/BUG_HUNT_PROMPT.md` · full record: `research/27-adversarial-self-audit.md` ·
details: `CHANGELOG.md`*

## Turning the tool's ethos on itself

After five releases in one arc, this release ships no new feature. We wrote
the bug-hunt prompt stitchgraph's own reviews are held to (seven bug classes,
CONFIRMED-vs-PLAUSIBLE grading, mandatory exonerations) and ran it against
ourselves — one self-pass plus two parallel agent passes over the youngest
subsystems. Fourteen findings survived verification. All fourteen are fixed,
each pinned by a test.

## The headline fixes

**Watch no longer bleeds LSP edges.** Under the v3.48 AUTO default a fresh
index carries `source="lsp"` edges — but the incremental/watch paths never ran
the LSP pass, so every edit silently stripped them from the edited file.
Incremental now equals fresh, verified store-row-for-store-row against a twin.

**A mute language server costs ~3 timeouts, not days.** A server that answers
`initialize` but nothing else used to eat the full per-request timeout on
every one of up to 20k sites. The client now counts consecutive timeouts and
the resolver circuit-breaks at three; framing loss kills the process so
everything after it fails fast; warm-up stops after ~2 s of stable-empty
answers instead of burning its 30 s deadline.

**LSP columns are UTF-16 code units.** An emoji in a string before the call
site shifted every query column — the LSP wire default is UTF-16, not Python
code points.

**Coverage kits survive machines that aren't ours.** The Python kit produced
a *confidently empty* artifact wherever pytest-cov wasn't preinstalled (pytest
rejects `--cov` before running a single test; `|| true` ate the evidence).
The Docker option couldn't succeed anywhere (read-only rootfs vs. loops that
write into `/work`; network-less runtime vs. runtime dependency fetches). The
shell option needed an undocumented copy step our own e2e test performed
silently. Re-runs misattributed stale captures. JS paths were passed as
unanchored regexes/substrings (`api/user.test.js` also ran
`webapi/user.test.js`). A `|` in your project path broke `sed`. All fixed:
deps bake at image build, scripts self-locate, `covdata` is wiped per run,
jest gets `--runTestsByPath` / vitest the absolute path, path-stripping uses
shell expansion — and **both converters now refuse to write a 0-test
artifact** (exit 1, no file): the loud floor under every best-effort step.

**scan stops crowning well-tested helpers.** The god-object detector counted
test-suite callers as coupling mass — the exact failure orient fixed in
research/25, never ported to scan. Candidates are now re-checked against
non-test fan-in; coupling that melts away without the suite never flags
(reported as `god_objects_test_mass_suppressed`).

## The lesson we're keeping

Twelve of fourteen findings sat in the two youngest subsystems, and nine were
environment assumptions — code field-validated on real corpora, but always on
*the machine that built it*. Field validation is not environment validation;
the release checklist now includes the hostile-machine pass, and the ritual
re-runs after every major arc.

## Compatibility

No schema change, no API change, no new dependency. Regenerate any existing
coverage kit to pick up the fixed scripts. `scan` may flag *fewer* god
objects on heavily-tested codebases — that is the fix working.
