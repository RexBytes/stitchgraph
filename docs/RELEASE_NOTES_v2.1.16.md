# stitchgraph v2.1.16 — Bash callback/invocation argument recognition

Four Bash cardinal/recall fixes from the doc-driven + dogfood manual pass: commands that invoke a
function whose name sits in an **argument** position (not the command head), which the generic
command scan — keyed on the head — misses. The function (and what it reaches) was false-flagged dead.

## The bugs & fix

The old `_bash_trap_handlers` rooted only top-level `trap HANDLER` arguments. Generalized to
`_bash_callback_refs`, now covering:

- **`trap HANDLER SIGNAL…` inside a function body** — the old pass skipped function bodies
  (top-level only), so a `trap cleanup EXIT` registered inside `main()` left `cleanup` flagged dead.
  The shell invokes the handler regardless of where it was registered, so it is now rooted.
- **`complete -F FUNC cmd` / `compgen -F FUNC`** — `FUNC` is the completion callback the shell
  invokes on TAB; it had no in-tree caller.
- **`export -f FUNC…`** — exports a function for subshells (invoked via `bash -c 'FUNC'` in a child
  shell — otherwise invisible). Parses as a `declaration_command`, handled accordingly.
- **`time FUNC`** — the `time` keyword runs `FUNC`, but tree-sitter parses `time` as the command and
  `FUNC` as a plain word, so the call was lost.

Each named function is routed through `_ref`, so it is rooted **only if it resolves to a project
function** — a non-matching name (or an external binary) roots nothing. Cardinal-safe: a genuinely-
dead function and a plain `export VAR=…` are still flagged (asserted in the regression). Descending
function bodies over-roots a handler in a dead function (cardinal-safe) — correct, since the shell
invokes trap/complete handlers independent of the enclosing function's own liveness.

## Compatibility

No API or schema change; indexes rebuild cleanly.

## Quality gate

Full suite (incl. an end-to-end regression for all four mechanisms with dead-stays-dead assertions,
the existing trap-parsing matrix, and a `_bash_callback_refs` parser unit test) + ruff + mypy clean;
differential oracle suite green; mutation meta-oracle over the new parsers (all mutants killed);
two-round full-diversity multi-model adversarial review.
