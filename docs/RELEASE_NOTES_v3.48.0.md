# v3.48.0 — reviewed by its other audience

*2026-07-07 · an LLM field review, archived verbatim and acted on ·
`docs/LLM_REVIEW.md` · details: `CHANGELOG.md`*

## The premise

stitchgraph is built for humans and LLM agents, and until now only humans had
written up the experience. A Claude Opus 4.8 session drove it end-to-end on a
real Rust project and reviewed it. The verdict validated the core bet — *"that
'how much should I trust this' signal is worth more to an agent than the raw
finding"* — and found exactly one hole in it, plus two adoption gaps. This
release closes the hole and the cheaper gap; the review itself is archived
verbatim in `docs/LLM_REVIEW.md`.

## Fixed: the confident-empty problem

The reviewer's `verify_path` case: `get-callers` answered a **confident
empty** while nine macro-wrapped uses pointed at the function as REFERENCES —
*"an LLM that trusts a confident 'no callers' will delete a live function."*

Now, an empty CALLS answer checks every other resolved relation touching the
symbol (REFERENCES from macros/dispatch tables/decorators, ROUTES_TO from
framework wiring, TESTS, …). Anything found demotes the result to
`needs_review` at confidence ≤ 0.6, reports the counts in
`meta.non_call_uses`, and says outright: **do NOT treat it as unused**. A
symbol nothing touches keeps its honest confident empty — pinned in both
directions.

## Changed: the best available analysis is the default

The reviewer: *"recommending `--lsp` loudly for compiled languages (or
defaulting to it when a server's present) would raise the floor."* We took
the stronger option. `reindex` is now tri-state:

- **AUTO (default)** — the language-server pass runs whenever a matching
  server binary is installed (typescript-language-server, rust-analyzer,
  gopls, clangd, plus `[lsp.servers]`); machines without servers fall back
  silently to the name-based graph.
- `--no-lsp` / `[lsp] enabled = false` / `STITCHGRAPH_NO_LSP=1` opt out.
- `--lsp` forces the pass and reports missing servers loudly.

This is the same contract as the install story since v3.31.0: full power by
default, graceful degradation, explicit opt-outs. Note the honest cost:
first-index time on server-covered languages rises with project size
(hono: 6.5 s name-based → ~6.5 min with the full LSP pass) — the trade the
review argued for, and `--no-lsp` is one flag away.

## Scheduled: turnkey Rust coverage

The review's remaining gap — ~150 lines of hand-wiring between
`scaffold_coverage`'s Rust template and a usable per-test coverage matrix —
is now a named roadmap item (STATUS.md). It is the difference between the
behavioural toolkit being *"useful when a human sets it up"* and *"an LLM can
drive the whole thing itself."*

## Compatibility

No schema change. The only behaviour change on default installs: reindex on
TS/JS/Rust/Go/C/C++ projects uses an installed language server automatically
(slower first index, strictly better edges; opt out with `--no-lsp`), and
`get_callers`/`get_callees` empty answers may now carry `needs_review` — which
is the point.
