# stitchgraph v3.7.0 — the body matrix completes the language sweep (Ruby, PHP, Bash)

v3.0.0 added the intra-procedural **body matrix** for Python; v3.2.0 ported it to the JavaScript
family, v3.3.0 to Go, v3.4.0 to Rust, v3.5.0 to C and C++, v3.6.0 to Java and C#. v3.7.0 adds the
final three — **Ruby, PHP, and Bash** — so the body matrix now spans **all 12 languages** the
extractor indexes (`docs/IDEAS.md` §5b). Bash is the outlier that closes the sweep: a
*command-oriented*, not expression-oriented, grammar.

A new language for an existing representation earns the MINOR bump, but it is **backward-compatible**:
schema, on-disk indexes, and every existing operation are unchanged, and the new behavior is opt-in
and advisory.

## Added

### `core/structure_ruby.py` — one walker for Ruby
Emits the **same `_VFG` vocabulary** as the other frontends and reuses the WL kernel, so a Ruby clone
with renamed locals or reordered statements fingerprints as the *same shape*. Specifics:

- **Qualname = the dotted module/class chain** (modules ARE part of the key): `M.Calc.compute`,
  singleton `def self.top` keyed `M.top`, bare top-level `free_fn` — matching the extractor.
- Expression-oriented (a trailing expression is an implicit return, like Rust). Compound assignment
  (`x += e`), `if`/`elsif`/`unless` and their statement-modifier forms, `case`/`when`, `while`/`until`/
  `for` (+ modifiers), `?:`, ranges, array/hash literals, string `#{…}` interpolation holes,
  index- and attribute-assignment, `return`/`yield`.
- **Blocks (`{ … }` / `do … end`) are opaque `NESTED` leaves** (closures).

### `core/structure_php.py` — one walker for PHP
Same `_VFG`, same kernel. Specifics:

- **Qualname = the class chain** (the `namespace` is NOT part of the key): `Calc.compute`,
  constructor `C.__construct`, bare top-level `free_fn` — matching the extractor.
- Statement-oriented. Call/method/scoped-call arguments are unwrapped from their `argument` wrappers;
  member access, subscript, compound assignment, `?:`, casts, `new`, array literals, `match`,
  `encapsed` (interpolated) strings, `for`/`foreach`/`while`/`do`, `switch`, `try`/`catch`/`finally`
  carry flow.
- **Closures and arrow functions are opaque `NESTED` leaves.**

### `core/structure_bash.py` — one walker for Bash (the command-oriented outlier)
Same `_VFG`, same kernel, but a different evaluation model — Bash has no expressions, only commands:

- A **`command` (`name arg…`) is a CALL** — the command name is the callee, its arguments flow as
  data; `$(…)`/`` `…` `` **command substitution** carries the value of the command it runs (including
  in *callee* position — `$(get_cmd) arg`); a `variable_assignment` binds (copy propagation); `$x`/
  `${x}` are variable reads; a string carries flow through its `$(…)`/`$x` holes; `$(( … ))`
  arithmetic, `[[ … ]]`/`[ … ]` tests, pipelines, and `if`/`for`/`while`/`case`/`c_style_for` are
  walked for control + data flow.
- **Functions are keyed by their bare name** (shell functions are flat) — matching the extractor.
- **Nested function definitions are opaque `NESTED` leaves.**

### `find_similar(mode="structure")` and `graph_diff` — now detect Ruby, PHP and Bash
Auto-detects the snippet's language (Python → JS/TS family → Go → Rust → Java → C# → Ruby → PHP →
Bash → C/C++) and ranks it **only against stored functions of the same language**; `graph_diff`'s
body layer reports a diverged Ruby/PHP/Bash body present in both indexes. Same-language by
construction (a node id maps to exactly one file, hence one language).

## Scope & caveats

- **Advisory and read-only** — never feeds `find_stale`, so the cardinal rule (*live code is never
  confidently flagged dead*) is structurally unaffected.
- The Ruby/PHP/Bash layer needs the optional **tree-sitter extra**; without it those paths return
  nothing (advisory degrade). The Python body matrix remains stdlib-only.
- **Cross-language body comparison stays oracle-only** — topology tracks the extractor; the features
  rank/diff within one language.
- Some grammars are permissive supersets of others, so the advisory snippet auto-detect can mis-sniff
  one *bare* snippet for a related language: the JS/TS grammar parses a bare PHP `function`/`class`
  snippet, and the C/C++ grammar parses a bare Bash/PHP `name() { … }` snippet — so Bash and PHP are
  tried before C/C++. This affects only the advisory snippet auto-detect — never the extension-keyed
  `graph_diff` body layer, which maps each file to exactly one language.
- Same structural-approximation limits as the other frontends: no alias analysis, constants are
  collapsed, Bash word-splitting/alias/exit-code semantics are not modeled. The method is in
  `docs/BODY_MATRIX_LESSONS.md`.

## Quality gate

- ruff + mypy clean; full suite passing; differential oracle suite passing.
- Three new **body-matrix completeness oracles** — Ruby
  (`tests/oracles/test_structure_ruby_completeness.py`, 45 cases), PHP
  (`tests/oracles/test_structure_php_completeness.py`, 49 cases) and Bash
  (`tests/oracles/test_structure_bash_completeness.py`, 36 cases): a `helper()`/`$(helper)` (a CALL)
  vs `0` (a CONST) in every value-bearing position must change the fingerprint, plus dedicated
  invariants (compound-assign rebind, module/namespace keying, constructor keyed, opaque
  block/closure/nested-function, Bash dynamic-callee walked). All use the **hardened exact-equality
  predicate** introduced in v3.6.0 (dodging the cosine float-rounding blind spot).
- **The adversarial panel earned its keep — 8 dropped value-flow positions found and fixed**, none
  caught by the generic fallback (only the value-bearing metamorphic probe surfaces these), all now
  oracle-pinned:
  - Bash, building the frontend: a **dynamic-callee drop** — a command whose *name* is a `$(…)`
    substitution (`$(resolve) arg`) was collapsed to an opaque free word, dropping the inner CALL.
  - Bash, panel: a **command substitution in an array-subscript index** (`${arr[$(helper)]}` read,
    `arr[$(helper)]=x` LHS) was dropped on both the expansion-read and assignment-LHS paths.
  - Ruby, panel: a `begin/rescue/**else**` clause body was never walked, and a
    **parenthesized multi-statement group** (`(sink(helper()); 0)`) kept only its trailing statement.
  - PHP, panel: **anonymous-class constructor arguments** (`new class(helper()) {}`) were dropped —
    the args live *inside* the `anonymous_class` node, not as a direct `arguments` child.
  - PHP, panel (round-1 confirm): **heredoc interpolation holes** (`<<<E…{$o->m(helper())}…E`) were
    dropped — `heredoc` was bucketed with non-interpolating `nowdoc` as a CONST, even though heredoc
    interpolates exactly like a double-quoted string (which was already walked). Now walked; `nowdoc`
    stays opaque.
  - C#, panel (certification round): a **constructor initializer** (`: this(helper())` /
    `: base(helper())`) had its arguments dropped — they run before the body but live in a
    `constructor_initializer` sibling of the body that the walker never visited (the C# analogue of
    the C++ member-initializer-list, already handled there). Now walked.
  - This is language diversity as an adversarial probe again: the Bash outlier exercised a
    callee/subscript position the seven prior expression-oriented frontends never could.
- **Cross-cutting fix — default parameter-value expressions are walked.** A `helper()` CALL vs a `0`
  CONST in a parameter's default value (`def f(b = helper())`) produced an identical fingerprint — the
  parameter-seeding loop registered only the parameter *name* and never walked its default-value child.
  Found across **every language with default-argument syntax**: latent in **C++, C# (shipped)** and
  **Python, JS/TS (shipped — the original frontends)** plus the new Ruby/PHP (Go/Rust/Java have no
  default arguments). A genuine CALL-vs-CONST completeness violation — it survived in every *body*
  position yet vanished in the default-value slot. All now walk the default (incl. destructured
  defaults like JS `function f({a = helper()})`), pinned by a cross-language oracle.
- **Invariant fix — Python lambdas are opaque.** A `lambda` in expression position leaked its body's
  value flow into the enclosing fingerprint (`ev` had no `ast.Lambda` branch → generic fallback
  recursed into the body), breaking the documented "closures are opaque `NESTED`" invariant. Python
  was the lone diverging frontend — all 11 tree-sitter frontends already return a single `NESTED` leaf
  for an expression-position closure. Now Python matches (the lambda's default-arg values still carry
  flow). Behaviorally pinned in the Python completeness oracle (which previously only *classified*
  Lambda as opaque without testing it).
- **Cross-cutting fix — assignment-target subscript index is walked.** A `helper()` CALL vs a `0`
  CONST in the *index of an assignment target* (`d[helper()] = v`) produced an identical fingerprint:
  the read path always walked the index, but the write (`bind`) path linked only the written value and
  the container, never the index. Latent in **Python, JS/TS, Go, Rust and C/C++** (Java/C#/PHP/Ruby
  already walked it). Now walked on the write path too, pinned by the same cross-language oracle
  (`tests/oracles/test_param_and_index_invariance.py`).
- **Cross-cutting fix — comments are trivia in every tree-sitter frontend.** A confirmation-panel
  sweep found a `comment` node leaking into the value-flow graph via each walker's generic fallback:
  a pure no-op comment edit changed a body fingerprint, down-ranking commented clones and surfacing
  comment-only edits as `graph_diff` body changes. It was **latent in Go, Rust, C/C++, Java and C#
  (shipped v3.3.0–v3.6.0)** as well as the new Ruby/PHP/Bash; Python (its `ast` discards comments) and
  JS/TS were already immune. All eight affected frontends now skip comment nodes as trivia, pinned by a
  new cross-language oracle (`tests/oracles/test_comment_invariance.py`) which also guards against
  over-pruning live flow. Textbook "a defect in one frontend is a one-shot audit of the family."
- Two Bash positions are **documented structural blind spots, not fixable in-AST**: a
  `${var#$(cmd)}`/`${var%…}` strip pattern is lexed by tree-sitter as one opaque `regex` token (the
  inner command substitution isn't a walkable child), and a single-quoted deferred action
  (`trap '$(cmd)' EXIT`) is a `raw_string` whose expansion only happens at `eval` time. Both are
  advisory-only mis-rankings, never cardinal.
- Mutation meta-oracle: the new Ruby/PHP/Bash fingerprint corpora are mutation-pinned by `graph_diff`
  body tests.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku) clean on the post-fix HEAD.

## Upgrading

Nothing to do — no schema/API/behavior change to existing operations; indexes don't need
rebuilding. To try the Ruby / PHP / Bash body matrix (with the tree-sitter extra installed):

```python
import stitchgraph as sg
with sg.Store("stitchgraph.db") as store:
    sg.reindex(store, "src")          # a Ruby / PHP / Bash project
    print(sg.find_similar(store, open("some.rb").read(), mode="structure"))
    print(sg.graph_diff(store, "other_index.db"))   # body-aware across all 12 languages
```
