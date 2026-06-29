# stitchgraph v2.2.1 — Bash `PROMPT_COMMAND` recall + contributor methodology

A small patch on top of the v2.2.0 milestone. No API or schema change; indexes rebuild cleanly.

## Fixed

### #95 — Bash `PROMPT_COMMAND=fn` runtime hook

```bash
__prompt() { history -a; }
PROMPT_COMMAND=__prompt          # run by the interactive shell before each prompt
```

A function registered via `PROMPT_COMMAND` (also `PROMPT_COMMAND="fn1; fn2"` and
`export PROMPT_COMMAND=fn`) is invoked by the shell with no textual call site, so it was
false-flagged dead. `_bash_callback_refs` now roots the function name(s) in a `PROMPT_COMMAND`
assignment.

- **Scoped** to the well-known `PROMPT_COMMAND` variable (high precision).
- **Cardinal-safe:** only a name that resolves to a project function is rooted; a genuinely-unused
  function still flags dead (verified).
- The generic `var=fn; $var` indirection remains a documented, deferred dynamic-dispatch gap.

## Docs

- **`CONTRIBUTING.md` → "The cardinal-hardening loop (dogfood + docs)"** — the repeatable method
  behind the entire v2.1.x → v2.2.0 line, written down so it's reusable: dogfood real repos to
  surface false-deads, read the language/runtime docs to find the *exact* form that's actually
  invoked, fix **additively** (an added root can't introduce a cardinal — it can only over-root),
  gate and pin **both** directions (the live symbol is live *and* a dead sibling still flags), and
  document over-rooting boundaries rather than risk the invariant by tightening them.

## Issue triage

GitHub issues #18–#22 (filed against v1.0.4) were verified already fixed in the shipped code and can
be closed:

- **#18** — `risk --path` defaults to the indexed root recorded in the DB.
- **#19** — `stitchgraph --version` callback; `orient`/`scan` scope reconciled in design §9.
- **#20** — `find_holes` first-index scope documented in `LIMITATIONS.md`.
- **#21** — `[project.scripts]` / `[project.entry-points]` parsed as roots.
- **#22** — bash top-level body seeded as a root and its top-level calls scanned.

## Quality gate

Full suite — 575 tests (PROMPT_COMMAND bare / quoted-multi / `export` forms root the hook; a
non-`PROMPT_COMMAND` var assignment does not; a genuinely-unused function still flags) + ruff +
mypy clean; differential oracle suite (27) green; mutation meta-oracle on `_bash_prompt_command_ref`
(4/4 killed). CI green across all four jobs (test 3.11 / 3.12 / lint / core-only).
