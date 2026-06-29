# stitchgraph v3.1.0 — semantic-path test hardening + a clearer README

A small, safe release on top of v3.0.0. **No source, API, or schema change** — every operation
behaves exactly as in 3.0.0, and indexes don't need rebuilding. v3.1.0 closes a mutation-coverage
gap in `find_similar`'s optional dense path and makes the README say plainly what the package
delivers.

## Why

v3.0.0 shipped the body matrix with its own core mutation-pinned (`structure.py` 15/15, `graphdiff`
9/9). The release-readiness work flagged that `core/similar.py`'s *optional* semantic/dense
(`model2vec`) retrieval path still had ~15 surviving mutants — un-pinned behaviour in the dense
ranking and the offline model-load — and parked it as a follow-up (`docs/IDEAS.md` §5d). This is
that follow-up.

## Hardened — `core/similar.py` mutation coverage

The in-house differential mutation meta-oracle (`scripts/mutate.py`) injects one operator/boolean
mutation at a time and checks the suite *kills* it. New unit tests pin the previously-unpinned
behaviour:

- **Dense ranking is strict.** The old dense test tied at the top (two nodes both scored 1.0), so
  the `reverse=True` sort and the `> 0` filter weren't actually pinned. A 3-axis fake embedder now
  gives a strict, tie-free ranking with one exactly-zero node — so sort direction and the
  drop-non-positive filter are both observable.
- **Zero-norm embeddings degrade, never divide-by-zero.** A zero-magnitude query *or* node
  embedding must score 0.0; the two `_dot_cos` norm guards (`… or 1.0`) are now pinned against the
  `and` flip that would raise `ZeroDivisionError`.
- **`model2vec` auto-load, tested offline.** A fake `model2vec` module + fake config exercise the
  auto-load without a network: the success path wires an embedder and selects the
  configured-or-default model; the load is attempted **at most once** (the `_M2V_TRIED` latch); and
  an import failure returns cleanly and stays on the token path (never calling `_dense` with no
  embedder).

Result: **29/32 mutants killed**. The 3 survivors are documented as **justified-equivalent** (not
test gaps) in the `tests/test_similar.py` module docstring — an `or`→`and` flip absorbed by a
downstream `if dot == 0`, a defensive `zip(strict=False)` that only differs on contract-violating
ragged vectors, and an `or`→`and` whose extra-permissive branch yields nothing downstream.

## Docs

- **README** now opens with *what stitchgraph delivers* — the question each operation answers —
  before the internals, with the v3.0.0 body matrix as the headline and a consistent language count.

## Cardinal-safety & scope

Unchanged from 3.0.0: the body matrix and `find_similar` are **advisory and read-only**; they never
feed `find_stale`, so the cardinal rule (*live code is never confidently flagged dead*) is
unaffected. This release adds **tests only** — no runtime code changed.

## Quality gate

- ruff + mypy clean; full suite **702** passing; differential oracle suite **85**.
- Mutation meta-oracle: `structure.py` 15/15, `graphdiff` 9/9, `similar.py` 29/32 (3 justified
  equivalent) — kill-signals named in the CHANGELOG.
- **Two-round full-diversity adversarial panel** (opus / sonnet / haiku), clean.

## Upgrading

Nothing to do — no behaviour, API, or schema change from 3.0.0.
