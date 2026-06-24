# stitchgraph v1.0.6 — entry-point coverage (issues #20, #21, #22)

A field-fix patch closing two cardinal-class false-dead gaps in entry-point detection
(both in the same family as #8), plus a documentation correction. The two code fixes only
ever *add* roots — they are precision-safe and can never newly flag live code dead.

## What changed

### CARDINAL: a tree-sitter framework-subclass's class stays live

The one release-blocking class of bug — live code flagged dead — caught by the final
confirmation panel. The tree-sitter `_seed_callback_roles` marked a framework subclass's
callback *methods* with the `callback` role (so they're roots) but never marked the enclosing
*class*; the Python extractor's `_apply_callback_roles` has a `classes_with_callbacks` second
pass that the tree-sitter side was missing. So a framework subclass in any tree-sitter
language (a Rails `ApplicationController`, a React `Component`, an Express middleware class)
that wasn't otherwise exported or constructed had its **class** flagged dead while its hook
methods stayed live. The class-rooting pass is now mirrored on the tree-sitter side, tied to
*having* callback methods (a bare unused subclass with no overrides still flags).

A follow-up confirmation panel found this was one instance of a **systematic** Python↔tree-sitter
rooting gap, and uncovered two more cardinal false-deads in the same family:

- **C/C++** map every `function_definition` to FUNCTION (no separate method node), so *all
  five* method-based class-rooting passes (exported/test/callback/main/constructor) — which
  key on METHOD — silently skipped every C++ method, flagging a live Qt/framework subclass and
  its methods dead. In-class member functions are now normalized to METHOD, fixing all five at
  once for every language.
- **C#** `internal class Program { static void Main }`: `Main` isn't public so the class never
  gets the `exported` role, and the tree-sitter extractor (unlike Python) had no pass to root
  the enclosing class of a `main`-role method. A new `_seed_main_classes` pass mirrors the
  Python rescue.

### `reindex` survives a pathologically deep source file

A huge flat expression (`X = a + b + c + …`, realistic in generated SQL/HTML/string-builder
code) overflows the recursive AST walk with `RecursionError`, which wasn't in the per-file
`except` and ran outside the `try` — so one bad file aborted the **entire** reindex and left
an empty DB, defeating the "skip the one file, never abort" contract. `RecursionError` is now
caught per file in both the Python and tree-sitter extractors and the resolver `parse()`
helper — and, since the route resolvers (express/jsfetch/spring) run their own recursive
descent that bypasses `parse()`, in `run_resolvers` too (a deep `.js` degrades to "no extra
edges"). (Also defensive: `Result` now flags `needs_review` for an out-of-range/NaN
confidence; the SCC passes restore `sys.setrecursionlimit`; the tree-sitter skip no longer
leaves an orphan module node — all latent/hygiene, no current caller hits them.)

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
now guards with `is_file()` (matching `config.py`'s already-safe `stitchgraph.toml` read).
For completeness the user-named coverage-trace read in `load_coverage` (reached via
`ingest_trace`) got the same guard, so a FIFO trace path returns empty (its documented
"empty on any problem" contract) rather than blocking. A full audit confirms every file
read in `src/` is now behind an `is_file()` guard or a stat-only call (`watch.py`); there
are no remaining `exists()`-then-read or unguarded discover-then-read sites.

### `ingest_trace` no longer crashes on a malformed coverage report

The content-shape twin of the FIFO fixes. `_parse_json` (the coverage.py JSON path, reached
via the public `ingest_trace`) guarded `executed_lines` *values* but assumed the `files`
object — and each per-file entry — was a dict. Valid JSON of the wrong *shape* (`files` a
list, an entry a string/null, `executed_lines` a dict) raised an uncaught `AttributeError`
straight through the op and CLI. It now isinstance-gates the shape and degrades to empty,
honouring the function's "empty on any problem" contract and matching the already-tolerant
LCOV and Go parsers.

A proactive sweep of the rest of the external-input surface (the same exercise that closed
the FIFO class) hardened the one remaining gap: `config._load` chained `.get().get()` over
`stitchgraph.toml`'s sections, so a hand-edited config with a non-table section, a
non-numeric `threshold`, or a non-list `include` crashed **every** CLI command (config is
read on each one). Each section/value is now shape-guarded with a default fallback. The
audit confirms the remaining parsers — `pyproject.toml` (`_console_script_targets`) and the
LCOV/Go coverage paths — were already isinstance-gated.

### `risk` counts unicode-named source files

`gitrisk._commits` scraped `git log --name-only`, but git octal-escapes and double-quotes
non-ASCII paths under the default `core.quotepath=true` (`"caf\303\251.py"`), so the trailing
quote defeated the source-extension filter — unicode-named files silently vanished from
churn, co-change, and `risk` hotspots (a quiet metric deflation, not a crash or a false-dead).
It now runs git with `-c core.quotepath=false` and strips any residual quoting, so those files
are counted.

## Verification

`pytest` 208 passed (new regression tests: console-script root + module-precision guard;
C++ framework-subclass class+methods live; C# internal `Main`-class live; deep-expression
no-abort in the tree-sitter resolver pipeline;
bash top-level/`$(...)`/`trap` rooting with a still-flagged orphan; FIFO-skip across the
extractors, the resolver pipeline, the route-gated resolvers, the `pyproject.toml` read,
and the coverage-trace read; malformed-coverage-JSON-shape no-crash; malformed-`stitchgraph.toml`
no-crash; unicode-filename churn counted; tree-sitter callback *class* stays live;
deep-AST reindex no-abort) · ruff clean · mypy clean against **both** the dev pack (1.10.6) and the pinned
bundled 0.13.0. Confirmed by full three-model panels (opus + sonnet + haiku); both FIFO
hangs (resolver-pipeline, then the `pyproject.toml` fixed-path read) were caught by opus
reviewers across two panel rounds and fixed before release. Dogfood `src/`: unchanged. Full
trajectory in `REVIEW_HISTORY.md`.
