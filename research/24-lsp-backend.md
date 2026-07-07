# 24 — The LSP backend: type-grade resolution for the tree-sitter languages

*2026-07-07 · the last roadmap item · what jedi is to Python, a language
server is to everything else.*

## The gap, precisely

The tree-sitter languages resolve calls by name and scope: one candidate →
confident, several → AMBIGUOUS to all, unknown → dropped. Every downstream
discount (hub ranking, scan certainty, edge compression) exists to manage
that ambiguity honestly. Python already has the upgrade path: the jedi
resolver (`--precise`) asks go-to-definition per call site and adds
EXTRACTED edges. The LSP backend is the same move for TS/JS, Rust, Go,
C/C++ — an external language server speaking JSON-RPC over stdio instead of
an in-process library.

## Probe findings (2026-07-07, this environment)

Both pilot servers were driven by hand with raw JSON-RPC before designing
the client (`scratchpad/lsp-probe/probe.py`):

- **typescript-language-server 5.x** (npm): initialize in 0.2 s. Definition
  on an imported call answers the **import binding** until the project
  finishes loading (~2 s on a 2-file project), then answers the real
  definition in the source file. So readiness cannot be assumed after
  `initialize` — the client needs a **warm-up stabilisation loop** (repeat
  one query until two consecutive answers agree), not a blind sleep.
- **rust-analyzer 1.94** (rustup component): initialize in 0.1 s; definition
  on `util::greet("…")` answers `util.rs` with the name's exact range;
  hover carries the full signature (`pub fn greet(name: &str) -> String`).
  Caveat found the hard way: the `rust-analyzer` on PATH may be an
  **uninstalled rustup shim** that dies on first write — the client must
  treat immediate exit/broken pipe as "server unavailable", never as an
  error that reaches the pipeline.
- Response shapes: `Location | Location[] | LocationLink[]` depending on
  `linkSupport`; the client does not advertise `linkSupport` and handles
  all three defensively. Servers emit unsolicited notifications
  (diagnostics, progress, log) interleaved with responses — the reader must
  route by `id` presence.

## Design

### Client (`core/resolve/lsp.py`) — stdlib only

`LspClient(cmd, root)`: subprocess + JSON-RPC framing (`Content-Length`
headers), a reader thread routing responses by id, and hard deadlines on
everything (`initialize` 30 s, per-request 15 s default). The external
surface is four methods: `start()`, `did_open(rel, language_id)`,
`definition(rel, line, char) -> list[(rel, line, char)]` (project-relative,
1-based lines; results outside the root are dropped), `hover(rel, line,
char) -> str | None`, `stop()`. **Every failure returns None/[] — nothing
raises past the client**, mirroring the jedi resolver's "precision can only
add, never break" contract. `is_ready()` runs the warm-up stabilisation:
re-issue the first site's definition until two consecutive non-empty
answers agree or the readiness deadline (default 30 s) passes.

### Server registry + config

Defaults, keyed by extension group (only spawned if the binary is on PATH):

| languages | command | languageId |
|---|---|---|
| .ts/.tsx/.js/.jsx/.mjs/.cjs | `typescript-language-server --stdio` | typescript/javascript |
| .rs | `rust-analyzer` | rust |
| .go | `gopls` | go |
| .c/.h/.cpp/.hpp/.cc | `clangd` | c/cpp |

`stitchgraph.toml` overrides/extends:

```toml
[lsp]
enabled = false          # master gate (the --lsp flag also enables)
timeout = 15.0           # per-request seconds
[lsp.servers]            # extension -> command, overrides the defaults
".ts" = "typescript-language-server --stdio"
".py" = ""               # empty = disable a default
```

### Resolver (`LspResolver`), driven from the extracted edges

No re-parse and no extraction change: the resolver walks the graph the
extractor already produced. Sites = name-based CALLS edges from
`source="tree-sitter"` files whose extension has a server. Tree-sitter edge
locations are `rel:line:0` (line-only), so the callee's **column is
recovered by searching the source line for the symbol's last path segment**
(`util::greet` → `greet`; `a.b.c(` → `c`); every occurrence on the line is
tried until one definition maps. Per (file, line, symbol) site, one
definition query; the answer maps to a node via a **line-containment
index** (innermost node whose `[location line, end_line]` span contains the
answer's line — LSP answers name positions, tree-sitter nodes may start on
a decorator/annotation line, so exact-line equality is too brittle).
A mapped target that differs from the site's src emits
`Edge(CALLS, dst_id=target, provenance=EXTRACTED, source="lsp")` — the
same shape jedi emits, deduped by the same pipeline, discounted nowhere.
**Monotone: the AMBIGUOUS arms stay** (removing them would make an LSP
mis-answer silently drop true edges; the ranking discounts already treat
EXTRACTED > AMBIGUOUS).

Budget honesty: sites are deduped per (file, line, symbol); a per-server
site cap (default 20,000) plus the per-request timeout bound the pass; the
resolver reports `lsp_sites`, `lsp_resolved`, and per-server declines in
the reindex report rather than pretending completeness.

### Surfaces

- `reindex(store, path, lsp=True)` / CLI `stitchgraph reindex --lsp` /
  `[lsp] enabled = true`. Independent of `--precise` (jedi) — they compose.
- **`type_at(file, line, col)`**: a new registry operation answering hover
  type info on demand (spawns the file's server, warm-up, one hover,
  shutdown). Refuses honestly when no server covers the file or the binary
  is missing — the same refusal discipline as every other operation.

### What this deliberately does not do (v1)

- No recall of **dropped externals** (calls whose name matched nothing) —
  those sites aren't in the graph; recovering them means walking sources
  again. Deferred until the precision pass proves itself in the field.
- No LSP on the **incremental path** (`replace_file`/watch) — same status
  as jedi, which is also reindex-time only. Documented.
- No long-lived server daemon: one server per reindex pass, then shutdown.

## Field results (2026-07-07)

Two real repos, baseline vs `--lsp`, same machine as the probes:

| | hono (TypeScript, 307 files) | fd (Rust, 28 files) |
|---|---|---|
| sites queried | 10,904 | 976 |
| server resolved | 7,162 (66%) | 717 (73%) |
| **new confident edges** | **+497** (EXTRACTED 1,130 → 1,627) | **+147** (132 → 279) |
| ambiguous CALLS rows | 3,264 → 2,941 | 1,742 → 1,597 |
| reindex cost | 6.5 s → 397 s | 0.3 s → 52 s |

Hand-verified samples: `EventV2Processor.getHeaders → EventV2Processor.
getCookies` (hono) is the marquee type-grade case — the server resolves to the
**correct override** (V2's `getCookies`, line 429 `this.getCookies(event,
headers)`), which name matching cannot distinguish from V1's or the abstract
declaration. `DirEntry.stripped_path → DirEntry.path` (fd, line 60
`self.path()`) likewise.

Cost honesty: ~36 ms per site, linear in sites. Confirmations of already-
confident single-candidate edges dedup away (same weight), so most of the
paid sites change nothing — they exist to catch the rare wrong single-match.
The site cap (20,000/server) plus AMBIGUOUS-first ordering means a capped run
spends its budget where precision pays. An `ambiguous_only` config knob (skip
confirmations entirely, roughly halving cost) is the obvious follow-up if
field use finds the full pass too slow — deferred until someone actually asks.

## Gates

- **Fake-server suite**: a deterministic Python LSP server (speaks real
  JSON-RPC over stdio, answers scripted definitions) pins the client
  framing, the warm-up loop, timeout/garbage/crash declines, and the
  end-to-end resolver mapping — CI-safe, no real binary needed.
- **rust-analyzer integration test**, gated on `shutil.which`: a two-file
  cargo project must gain the cross-module EXTRACTED CALLS edge.
- Cardinal safety: LSP edges are EXTRACTED CALLS — they can only make more
  code live, never flag live code dead. The existing convergence and
  compression oracles are unaffected (source="lsp" edges ride the same
  dedup/compression machinery jedi edges already exercise).
