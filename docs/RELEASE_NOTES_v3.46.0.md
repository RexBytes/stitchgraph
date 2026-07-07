# v3.46.0 — the LSP backend: the roadmap closes

*2026-07-07 · type-grade resolution for the tree-sitter languages · design
story: `research/24-lsp-backend.md` · details: `CHANGELOG.md`*

## The last item

Since the roadmap was first written, one deferral has anchored its far end:
*type-correct* resolution — knowing **which** `save` a call `x.save()` means —
needs a real language server per language. Everything else shipped first.
This release ships that item, and with it the roadmap as it has existed is
complete.

## What it does

```bash
stitchgraph reindex src/ --lsp        # or [lsp] enabled = true in stitchgraph.toml
```

For every name-based call site the tree-sitter extractor produced, the file's
language server is asked go-to-definition; when the answer maps to a known
node, the true target gains a **confident EXTRACTED edge** — exactly what
jedi's `--precise` has done for Python since v1. The name-based AMBIGUOUS
arms stay (monotone: a server mis-answer can never silently drop a true
edge); every hub ranking, scan certainty pass, and liveness discount already
prefers EXTRACTED where it lands.

Servers are auto-detected on PATH — **typescript-language-server** (TS/JS),
**rust-analyzer**, **gopls**, **clangd** — and `[lsp.servers]` overrides,
extends, or disables per extension. A missing or broken server declines
honestly in the reindex result's `lsp` report; nothing here can fail an
index build. The client is stdlib-only JSON-RPC over stdio: the only new
requirement is the server binary itself, and only if you opt in.

Also new: **`type_at(file, line, col)`** — hover-grade type information on
demand, as a first-class operation (CLI and MCP surfaces generated as
always), refusing honestly when no server covers the file.

## Field numbers (research/24)

| | hono (TypeScript) | fd (Rust) |
|---|---|---|
| confident call edges | 1,130 → **1,627** (+44%) | 132 → **279** (+111%) |
| sites the server resolved | 66% of 10,904 | 73% of 976 |
| reindex cost (opt-in) | 6.5 s → 397 s | 0.3 s → 52 s |

Hand-verified highlight: `EventV2Processor.getHeaders →
EventV2Processor.getCookies` — the server resolves the call to the **correct
override** among three same-named candidates (V2's, V1's, the abstract
declaration). That distinction is the whole reason this backend exists;
name matching cannot make it.

## Also in this release

- The README now opens with a **supported language-version table** (oldest
  and newest verified syntax per language), enforced in CI by parse probes
  so a grammar-pack upgrade cannot silently falsify it.

## Compatibility

No schema change, no new Python dependency, fully opt-in. Without `--lsp`
(and without `[lsp] enabled`) nothing changes at all.
