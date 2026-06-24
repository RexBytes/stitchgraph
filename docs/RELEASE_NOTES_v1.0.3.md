# stitchgraph v1.0.3 — offline-by-default grammars (issue #12)

A patch release that restores the **local-first** promise for polyglot extraction:
`pip install 'stitchgraph[treesitter]'` is once again **self-contained and works
offline**, with no network touched at runtime. No new operations, no behaviour change
for anything that already worked — the extraction graph is byte-identical.

## What was wrong (issue #12)

`tree-sitter-language-pack` changed its install model at **v1.0.0**: it stopped shipping
the compiled grammars in its wheel and switched to **downloading them from a GitHub
release on the first `get_language()` call**, caching to `~/.cache`. In any environment
that can't reach GitHub at runtime — air-gapped, corporate proxy, CI with restricted
egress, sandboxed runner — that download fails, and (combined with the now-fixed #7) the
result was a silent near-empty graph. A default install was no longer self-contained,
contradicting the README's headline.

The original 1.0.1 "dependencies bounded" change set `<2`, which does **not** fix this —
the download line (1.10.7) is `<2`. The real cutoff, verified from PyPI wheel sizes, is at
1.0.0: **0.1.2 – 0.13.0 bundle grammars in the wheel; 1.0.0+ download them.**

## What changed (Option 1: offline default + get grammars the easiest way)

- **The `[treesitter]` extra now pins the bundled line** —
  `tree-sitter-language-pack>=0.7,<1.0` and `tree-sitter>=0.25.2,<1` (what 0.13.0 requires;
  we already run 0.25.2). The wheels carry the parsers, so the default install is fully
  offline. Verified in a clean venv: all 12 supported grammars load and a full
  Rust/JS/TS/Java/Go/Python reindex runs with the network forced off.
- **New opt-in `[treesitter-download]` extra** — the `1.x` line (smaller wheel, newest
  grammars, fetched on demand) for users who want the latest grammars and have runtime
  network.
- **Adaptive loader** — stitchgraph uses whichever pack is installed: a bundled grammar
  loads directly; on the download line a missing grammar is fetched on demand (and the
  loader explicitly downloads-and-retries if the pack supports it). A genuine failure is
  surfaced as the #7 warning and that language's files are skipped — never a silent empty
  graph, never a false "dead". The change is behaviour-preserving (byte-identical graph).
- **`stitchgraph doctor`** — a self-check that reports the pack version, whether it's the
  bundled or download model, the cache dir, and which grammars load. `stitchgraph doctor
  --strict` exits non-zero if any supported grammar can't load — a CI gate that the
  polyglot graph will actually be complete.

## Licensing note

All 12 grammars stitchgraph uses (plus tree-sitter core and the language-pack) are
**MIT-licensed**. Because the `[treesitter]` extra *depends on* the bundled grammar wheels
rather than vendoring them into stitchgraph's own distribution, each grammar's license
travels with its own wheel — stitchgraph takes on no redistribution or notice obligation.

## Verification

`pytest` 172 passed (4 regression tests for #12, pinning the probe, the download-retry
loader, the `--strict` gate, and the #7 warn-on-failure) · ruff clean · mypy clean.
Confirmed by a full three-model panel (JJ: opus + sonnet + haiku, all `FINDINGS: none`),
which verified the loader swap is byte-identical, all four adaptive-load paths, the doctor
self-check, the pin bounds, and no regression (dogfood `src/`: 3 advisory / 0 holes). Offline
operation was independently verified in a throwaway venv on bundled 0.13.0 with the network
off. Full trajectory in `REVIEW_HISTORY.md`.
