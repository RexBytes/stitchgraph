# stitchgraph v2.1.31 — Bash function-export recall (#73); closes the #70–#89 backlog

A Bash function exported for subshells, or invoked under a `time` brace group, is no longer flagged
dead. This is the final release of the post-sweep cardinal-safe follow-up backlog.

## The bug (#73)

```bash
exported_fn() { echo hi; }
declare -xf exported_fn        # exported for subshells — flagged dead

typeset_fn() { echo world; }
typeset -fx typeset_fn         # same, ksh spelling — flagged dead

timed_fn() { echo timed; }
time { timed_fn; }             # invoked under `time` — flagged dead
```

`export -f FUNC` was already rooted; the equivalent `declare -fx` / `declare -xf` / `typeset -fx`
spellings were not, and `time { fn; }` (a brace group, which tree-sitter mis-parses so `{` becomes a
word argument of `time`) lost its target.

## The fix

- `_bash_export_decl` now roots functions for `declare`/`typeset` when the flag combines `f` and `x`
  (either order), alongside `export -f`. A plain `export VAR=…`, `declare -r`, or `declare -f`
  (print only, no export) still roots nothing.
- `_bash_time_target` skips a leading `{`/`}` token and takes the first real word, so `time { fn; }`
  roots `fn`.

Cardinal-safe: both are additive rootings. A genuinely-unused function still flags dead (verified).

## Resolved without a code change

- **#72** — `trap SIGNAL` (one-word reset) over-roots the signal name. Over-rooting is the
  cardinal-SAFE direction; a one-word trap argument is ambiguous (signal vs handler), so removing
  the root would risk flagging an intended handler dead. Left intentionally.
- **#84** — narrowing Go selector-field references to method-kind resolution would un-mask the broad
  v2.1.12 selector rooting and risk a cardinal. Deferred.
- **#79** — the `_unwrap_ts_value` `seen < 8` cap is a runaway guard; 9+ literally-nested TS value
  wrappers do not occur in real code. Theoretical, no action.
- **#82** — a decorator on a `const X = class{}` expression is not valid TypeScript. No action.
- **#85** — added a regression test pinning that `nodes.file` is populated after a plain reindex.

## The backlog is closed

The post-sweep follow-up backlog (#70–#89) is complete: #70, #74, #76, #78, #86, #89, #73 fixed
behind the full gate; #71, #72, #75 (earlier), #77, #80–#83, #84, #87, #88, #79, #82, #85 either
already handled, resolved without code change, or documented as deliberate cardinal-safe boundaries.

## Quality gate

Full suite — 565 tests (declare -xf / declare -fx / typeset -fx / typeset -xf root the function;
`time { fn; }` roots its target; a `_bash_export_decl` unit-pin; an unused function still flags; a
`nodes.file` coverage test) + ruff + mypy clean; differential oracle suite (27) green; mutation
meta-oracle on `_bash_export_decl` / `_bash_time_target` (functional behavior pinned; two surviving
guard-relaxation mutants are cardinal-safe equivalents on node-type combinations bash never emits).
Two-round full-diversity multi-model adversarial review — no in-scope cardinal, no crash.
