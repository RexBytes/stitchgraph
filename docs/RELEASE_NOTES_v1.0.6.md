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

## Verification

`pytest` 187 passed (3 new regression tests: console-script root + module-precision guard;
bash top-level/`$(...)`/`trap` rooting with a still-flagged orphan) · ruff clean · mypy
clean against **both** the dev pack (1.10.6) and the pinned bundled 0.13.0. Confirmed by
full three-model panels (opus + sonnet + haiku). Dogfood `src/`: unchanged. Full trajectory
in `REVIEW_HISTORY.md`.
