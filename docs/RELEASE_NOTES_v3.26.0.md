# stitchgraph v3.26.0 — release notes

**The D2 dedup.** The 2026-07-03 external review's biggest maintainability finding (D2) was that
the nine tree-sitter body-matrix frontends (`structure_js.py` … `structure_bash.py`) are
hand-synchronized near-copies — and F5a/F5b proved the copies drift: fixes landed in Java that the
same code shape in JS and Bash never received. The v3.25.x dogfood then made it a structural fact
(`find_subsystems` isolates the frontends as their own 329-node cluster; `orient` lists seven
byte-identical `_walk.text` helpers at fan_in 122). This release extracts the mechanically shared
core so the next fix lands **once, not nine times**. Behaviour-preserving; net **−387 lines**.

## What moved into `core/structure_common.py`

- **Stage 1 — leaf helpers:** `make_parser` (the tree-sitter parser factory with the
  advisory-degrade contract), the comment-safe positional child selectors `nc`/`first`/`last`
  (the R197 repeated-field hazard class), and the operator-token reader `op_text`. Frontends keep
  thin local delegations, so no call sites changed; Java and Rust pass their grammars' split
  comment-type tuples (`line_comment`/`block_comment`).
- **Stage 2 — builder scaffolding:** every `_build_vfg`'s opening block (graph/env/free/`freevar`)
  and every `_build_pdg`'s (nodes/edges/counter/`new_id`) now come from the `vfg_state()` /
  `pdg_state()` factories, plus `node_text` and the `parse_tree` walk guard. The R205-hardened
  **sorted data-edge emission** — previously nine copies, comment and all — is the single shared
  `data_from`.
- **Stage 4 — one corpus iterator:** `similar.py`'s nine near-identical per-language
  `_*_fn_fingerprints` iterators are one generic `_ts_fn_fingerprints(store, mod)` plus
  `functools.partial` bindings (every frontend's `fingerprint_source` accepts the grammar
  uniformly; the JS family threads its per-extension grammar through the same path).

## Deliberately NOT unified (stage 3)

The per-language `process`/`ev`/`do` dispatchers — the construct→graph mappings where twenty-plus
panel rounds of per-grammar fixes live. That is semantic per-grammar knowledge; a config-object
DSL would be harder to review than the duplication it removes. This boundary is the design line:
**plumbing is shared, semantics stay per-language.**

## Verification

- **Byte-identical differential:** fingerprint + VFG + PDG outputs over a 50-function, 9-language
  corpus (including comment-in-positional-site snippets), identical before/after the refactor.
- **The full oracle battery** (1,618 tests: per-language completeness batteries, PDG⇄VFG
  differentials, comment invariance) green — and it earned its keep: the first draft treated
  Rust's `_nc` as the plain single-`comment` variant, and the comment-invariance oracle caught it
  within one CI cycle (Rust splits comment trivia like Java). Exactly the class of drift this
  release exists to end, caught by exactly the oracle built to catch it.
- ruff + mypy clean; no API or schema change; all three surfaces (library/CLI/MCP) unaffected.
