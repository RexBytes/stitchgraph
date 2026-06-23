# stitchgraph v0.2.0 — "Polyglot"

Local-first, MCP-native code intelligence. Point it at a codebase to find
**stale code, implementation holes, orientation, and impact** — ranked by what's
actually live, every answer carrying a confidence and a reason to double-check.
**Read-only on your code.**

This release makes stitchgraph **multi-language**.

## ✨ Headline: 12 languages, one graph

A new config-driven **tree-sitter** extractor adds 11 languages alongside the
deep Python support, all in a single unified graph:

| Tier | Languages |
|---|---|
| **Deep** (types + scope) | **Python** |
| **Extracted** (definitions + call graph) | **JavaScript, TypeScript/TSX, Rust, C, C++, C#, Go, Java, Ruby, PHP, Bash** |
| **Detected** (cross-language boundary) | **HTML** (`<form action>` → route), **SQL** (query → table) |

Every extracted language gives you `find_stale` (dead code), `orient`,
`impact_of`, `trace_path`, and `scan` — and they share one graph, so the
full-stack trace (HTML form → route → handler → … → DB table) still works.

Full per-language support matrix: [`docs/LANGUAGES.md`](docs/LANGUAGES.md).

## What's new

- **Polyglot tree-sitter extractor** producing the same node/edge ontology as the
  Python `ast` extractor — functions, methods, classes, structs, traits, and the
  call graph.
- **Adding a language is a `LangSpec`** (a dozen lines: node types → kinds, the
  call node + callee field), not new code. Kotlin/Swift/Go-modules/etc. are each
  one small spec away.
- **Per-language resolution** — a JS call never binds to a Rust function of the
  same name. Precision-biased: unique → confident, ambiguous → linked to all
  candidates (never calls live code dead), unknown → dropped as external.
- **Per-language entry points** — `export` (JS/TS), `pub` (Rust), `public`
  (Java/PHP/C#), capitalised (Go), `main`.
- **Polyglot dispatcher** — Python + tree-sitter merged; a parse failure in one
  language never breaks another.
- Packaging extras: `stitchgraph[treesitter]`, `[precise]`, `[resolve]`,
  `[algebra]`, `[all]`.

## Unchanged from v0.1.0 (still here)

14 operations across **library / CLI / MCP / report** surfaces, the
refuse-when-unsure envelope (`confidence / provenance / needs_review / urgency`),
GraphBLAS reachability + hub ranking, git-risk fusion, runtime-trace fusion,
semantic `find_similar`, data-loop detection, `stitchgraph.toml` config, and the
`AGENTS.md` adoption rules.

## Honest limitations

- For the tree-sitter languages, **imports and inheritance aren't modelled yet**
  (calls still resolve cross-file by name, so dead-code / orient / trace work),
  and **entry-point detection is thin** — so `find_stale` on, say, a Rust crate
  leans on `stitchgraph.toml` `[entry_points] include`, or honestly refuses
  without roots.
- The hard, deferred part of every language is *type-correct* resolution
  (disambiguating `x.save()`), which is what an LSP is for — parsing is the easy
  part. `--precise` (jedi) provides this for Python today.

## Install

```bash
pip install 'stitchgraph[all]'      # everything
# or pick: stitchgraph[cli,treesitter,algebra,resolve,precise,mcp]
```

## Quick start

```bash
stitchgraph reindex ./your-repo
stitchgraph report ./your-repo            # orientation + issues + risk
stitchgraph find-stale --db stitchgraph.db
```

## Tests

58 tests, including a polyglot suite (JS/Rust/Bash/Go/Java/Ruby/PHP extraction,
per-language call graphs, cross-language dead code, no-cross-language-false-links)
and a precision/recall harness asserting the never-flag-live-code-as-dead stance.

**Full changelog:** [`CHANGELOG.md`](../CHANGELOG.md)
