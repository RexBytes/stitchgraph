# stitchgraph v1.0.6 — entry-point coverage (issues #20, #21, #22)

A field-fix patch closing two cardinal-class false-dead gaps in entry-point detection
(both in the same family as #8), plus a documentation correction. The two code fixes only
ever *add* roots — they are precision-safe and can never newly flag live code dead.

## What changed

### `[project.scripts]` console entry points are roots (#21)

`design.md` §4 lists `[project.scripts]` / `[project.entry-points]` console-script targets
as roots, and `PythonLibraryDetector` already collected a `script` role — but nothing ever
parsed `pyproject.toml` to set it. So a CLI's `main` with no internal caller was flagged
dead out of the box, on any package that exposes its CLI the standard way (including
stitchgraph itself: `stitchgraph = "stitchgraph.adapters.cli:main"`).

The Python extractor now reads `[project.scripts]`, `[project.gui-scripts]`, and every
`[project.entry-points.*]` group, and tags each target (`"pkg.mod:func"`) with role
`script`. The match requires **both** the object's leaf name and the module path, so a
same-named function in an unrelated module isn't mis-rooted (precision over recall). A
genuinely-unused private function still flags.

### A bash script's top-level body is a root (#22)

The bash analogue of #8 (Rust `#[test]`). stitchgraph had no representation of a bash
script's top-level body — bash's `__main__`. Two reinforcing gaps: top-level statements
weren't scanned for call sites, and the only bash roots were functions literally named
`main` (or test scripts). So a script that "just runs the commands" (idiomatic shell, no
`main()`) got every function mis-flagged — verified on a real repo where 7 of 8 flagged
bash functions were false positives.

Now each bash script's module node is seeded as a root (the script's `__main__`) and its
top-level calls are rooted: direct calls, calls inside `$(...)` command substitution, and
the function argument of `trap NAME SIGNAL`. The five real call-shapes from the issue
(top-level call, `$(...)`, `trap`) all become correctly live, while a function called from
nowhere (including its own top level) **stays** flagged — exactly the desired precision.

### `find_holes` scope documented (#20)

`find_holes` returns empty on a freshly-indexed project even for a textbook call to an
undefined function, which can read as "no broken wiring." That's because both extractors
deliberately **drop** unresolved calls (recording a hole for every `len()` / stdlib call
would be overwhelming noise — precision over recall). So `find_holes` is an
*edit-orphaned-reference* detector (a delete/rename orphaning an edge), not a first-index
dangling-call detector. Importantly, the headline `is_stub ∧ reachable` "landmine" from
design §6.D **is** delivered — by `scan`, as its `live_stub` finding (🔴 on a confident
path, 🟠 via an inferred one). `LIMITATIONS.md` and `AGENTS.md` now document this and point
to `scan`. No behaviour change — expectation-setting only.

### `reindex` no longer hangs on a FIFO / special file

A named pipe (or other non-regular file) in the tree would hang `reindex` forever: `open()`
on a FIFO with no writer blocks, and the `except OSError` guards never fire because the open
doesn't error — it blocks. The fix skips non-regular files via `path.is_file()` in **every**
file walk that reads bytes/text: the Python and tree-sitter extractors, the resolver
`parse()` helper, and — caught by the release panel — the four route/template resolvers that
do their own `rglob` walk (`express`, `jsfetch`, `spring`, `html`). The Express and Spring
resolvers run on **every** `reindex`, so a FIFO named `*.js`/`*.java`/`*.html` would hang the
primary CLI/MCP entry point even though the extractors were already guarded. `is_file()`
returns `False` on a FIFO without blocking, so the guard is safe.

One more instance of the same class, in a *fixed-path* read rather than a walk: the #21
console-script parser (`_console_script_targets`) read `<root>/pyproject.toml` behind an
`exists()` guard — but `exists()` is `True` for a FIFO, so `read_text()` blocked forever. It
now guards with `is_file()` (matching `config.py`'s already-safe `stitchgraph.toml` read). A
full audit confirmed this was the only `exists()`-then-read site in `src/`; every other
file read is either behind an `is_file()` walk guard or a stat-only call (`watch.py`).

## Verification

`pytest` 199 passed (new regression tests: console-script root + module-precision guard;
bash top-level/`$(...)`/`trap` rooting with a still-flagged orphan; FIFO-skip across the
extractors, the resolver pipeline, the route-gated resolvers, and the `pyproject.toml`
read) · ruff clean · mypy clean against **both** the dev pack (1.10.6) and the pinned
bundled 0.13.0. Confirmed by full three-model panels (opus + sonnet + haiku); both FIFO
hangs (resolver-pipeline, then the `pyproject.toml` fixed-path read) were caught by opus
reviewers across two panel rounds and fixed before release. Dogfood `src/`: unchanged. Full
trajectory in `REVIEW_HISTORY.md`.
