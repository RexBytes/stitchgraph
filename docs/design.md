# stitchgraph — Design & Capability Map

A local-first, open-source code-intelligence system you point at a codebase to
get a **human-readable report** and an **MCP / CLI / library** interface for an
LLM to query. It builds a multi-relational graph of the code, derives sparse
matrices on demand, and uses reachability + path algebra to answer the questions
a programmer actually asks when landing on an unfamiliar repository.

The north star: **stale code, implementation holes, orientation, and impact —
ranked by what's actually live, every answer carrying a confidence and a reason
to double-check.**

---

## 1. Purpose

Point `stitchgraph` at a codebase and it tells you:

1. **What's live and what's stale** — code reachable from real entry points vs.
   code nothing reaches (dead/unreachable). Stale code wastes an LLM's and a
   human's attention; surface it so it can be removed or ignored.
2. **Where the implementation holes are** — references that expect something
   that isn't there (or is only a stub), ranked by whether they're on a live path.
3. **How to understand it fast** — entry points, the most-depended-on hubs to
   read first, the natural subsystems, the reading order.
4. **What a change will hit** — blast radius, which tests to run, full-stack paths.
5. **Options, not edicts** — it hands an LLM ranked candidates with confidence
   and provenance; the model/human decides. It never auto-deletes or asserts
   "this is dead" as fact.

---

## 2. Design principles

1. **Borrow the 80%, build the 20%.** Call graphs, symbol tables, live types,
   incremental parsing, semantic search — solved and free. Stand on them. Spend
   effort only on the cross-language edges and the confidence-weighted path algebra.
2. **Adjacency lists are the source of truth; matrices are derived.** Cheap
   incremental updates (matrices are bad at that) + algebra on demand (matrices
   are good at that). Never store the matrix as primary state.
3. **Confidence and provenance on every edge and every answer.** Boolean edges
   throw away the most useful signal. Confidence is *load-bearing*: it gates
   `needs_review` and propagates through the algebra — not decoration.
4. **Refuse when unsure.** Every result can say "needs review" with named
   reasons rather than return a confident wrong answer. For a dead-code / hole
   tool this is the single most important property — a false "delete this" is
   destructive.
5. **Live types beat inferred types.** Use a language server for
   types/definitions/references. Never re-derive what an LSP gives free and current.
6. **Your stack first, not 158 languages.** Depth on the stack you actually run
   beats breadth you'll never touch.
7. **Few coarse tools, not forty.** The MCP schema list is a per-turn context
   tax on a local model. Expose ~8 task-level operations.

---

## 3. Architecture — one core, four surfaces

The library API **is** the core. The CLI, the MCP server, and the report are
thin renderers over the same typed operations. There is exactly one definition
of each operation, so the surfaces stay in sync by construction.

```
stitchgraph/
  core/
    operations.py     # THE public library API — the ~8 typed functions
    envelope.py       # Result[T] dataclass (confidence/provenance/needs_review/...)
    store.py          # SQLite adjacency store (source of truth)
    extract/          # tree-sitter + LSP -> nodes/edges        (stack-specific)
    resolve/          # cross-language edges                    (stack-specific)
    algebra.py        # derived CSR + python-graphblas          (reachability/cycles)
  adapters/
    cli.py            # Typer  — renders envelope to terminal
    mcp.py            # FastMCP — serializes envelope to MCP tool result
    report.py         # renders a full Markdown report
  __init__.py         # re-exports core.operations as the package API
```

**Rule that keeps it clean:** `core/` never imports Typer or MCP. Anyone who
`import stitchgraph` as a dependency gets a pure, light library. Adapters import
core, never the reverse.

### The "obvious mapping" guarantee

Each operation is defined once as a typed, docstringed function. Both Typer and
the MCP SDK derive their schema from the *same* type hints + docstring, so:

| Library API | CLI | MCP tool |
|---|---|---|
| `find_symbol("User")` | `stitchgraph find-symbol User` | `find_symbol` |
| `find_stale()` | `stitchgraph find-stale` | `find_stale` |
| `trace_path(src, sink)` | `stitchgraph trace-path SRC SINK` | `trace_path` |

Same name, same params, same order, everywhere — because there's one definition.
The surfaces differ only in *rendering*, never in *what exists*.

```python
# core/operations.py — pure, no CLI/MCP imports
def find_stale(path: str | None = None) -> Result[list[Node]]:
    """Code reachable in the graph from no entry point. Each result carries a
    confidence and review reasons; this never asserts 'dead' as fact."""
    ...
```

---

## 4. Data model

### Nodes
`File, Module, Package, Class, Function, Method, Variable, Route, Endpoint,
Handler, Template, HTMLElement, ORMModel, DBTable, DBColumn, Query, Test`

Each node: stable id, kind, name, source location (`file:line:col`), optional
embedding, optional summary, optional payload flags (e.g. `is_stub`, arity).

#### Node identity (stable ids)
The id is `path::qualified.name` — e.g. `app/users.py::UserService.save` — **not**
line-based. Overloads / duplicate names within the same scope get a `#n`
disambiguator (`...save#2`). Consequences, by design:

- Reformatting, comment edits, and line moves **do not** change the id, so edges
  survive a re-parse. This is what makes incremental updates cheap.
- A genuine rename **does** change the id (old id disappears, new one appears) —
  correct, because a rename *is* an identity change for our purposes.

#### Granularity (default vs opt-in)
Default node granularity is **File / Module / Package / Class / Function /
Method** plus the cross-language nodes (`Route, Endpoint, Handler, Template,
ORMModel, DBTable, Test`). Variable/field-level nodes (`Variable, DBColumn`) are
**opt-in**, switched on only for the 🟡 data-flow features (data loops, argument
provenance), because variable-level granularity multiplies graph size and isn't
needed for M0–M3. Keeps the common-case graph small.

### Edges (typed, weighted, provenanced)
Relations: `CALLS, IMPORTS, INHERITS, READS, WRITES, QUERIES, ROUTES_TO,
RENDERS, SUBMITS_TO, MAPS_TO, RETURNS, REFERENCES, TESTS, EMITS, HANDLES,
RUNTIME_HITS`

```
Edge {
  src:         node_id
  dst_id:      node_id | null   # null = unresolved (a dangling reference)
  dst_symbol:  str              # the raw name we tried to resolve
  relation:    RelationType
  weight:      float            # 0..1 confidence in THIS edge
  provenance:  EXTRACTED | INFERRED | AMBIGUOUS
  location:    file:line:col
  source:      tree-sitter | lsp | heuristic | runtime | config
}
```

`EXTRACTED` = read directly from syntax/LSP (weight ~1.0). `INFERRED` = heuristic
(weight < 1.0). `AMBIGUOUS` = multiple candidates, none dominant (low weight,
alternatives recorded).

#### Unresolved edges *are* the hole substrate
An edge always records `dst_symbol` (the name at the call/reference site) and a
nullable `dst_id`. **`dst_id IS NULL` means the reference didn't resolve to any
known node — i.e. a dangling reference, the raw material of `find_holes`.** This
makes hole detection a single indexed query rather than a separate analysis, and
it gives the incremental updater a worklist (see *Incremental updates* below):
when a new node appears, re-resolve any unresolved edge whose `dst_symbol`
matches.

#### Confidence weights & thresholds
Confidence must be load-bearing, not decoration, so the source→weight mapping is
pinned (all values overridable in `stitchgraph.toml`):

| Edge source | Default weight | Provenance |
|---|---|---|
| Syntax-extracted (tree-sitter, unambiguous) | 1.0 | EXTRACTED |
| LSP-resolved (def/ref/type) | 0.95 | EXTRACTED |
| Single-candidate heuristic | 0.7 | INFERRED |
| Cross-language inferred (form→route, model→column) | 0.6 | INFERRED |
| Multiple candidates, none dominant | 0.3 × (1 / n_candidates) | AMBIGUOUS |

**`needs_review` fires when confidence < 0.80 OR provenance = AMBIGUOUS.** A
result's confidence is the propagated path confidence (max-× over its edges,
§13.2), so a chain of strong edges stays high and one weak link drags it toward
review.

### The universal result envelope (every operation, every surface)

```
Result {
  ok:             bool
  result:         <payload>
  confidence:     0.0..1.0
  provenance:     extracted | inferred | ambiguous
  needs_review:   bool
  review_reasons: [str]
  urgency:        green | orange | red | null   # issue results only (see §7)
  alternatives:   [<payload>]
  meta:           {...}
}
```

`needs_review` is true whenever confidence is below threshold or an ambiguity is
named. Payloads stay compact — the token tax is real on a local model.

### Store & incremental updates

SQLite is the source of truth. Reindexing one file is *not* just "delete its rows
and re-insert," because edges cross file boundaries: an edge is **owned** by the
source file but **points** into another. The rule:

1. Delete all nodes and all edges **owned by** (originating in) the changed file.
2. Re-extract that file → insert its nodes and edges. New edges resolve against
   the current node table; unresolved ones store `dst_id = null` + `dst_symbol`.
3. **Re-resolve the unresolved worklist:** any existing edge with `dst_id IS NULL`
   whose `dst_symbol` now matches a newly-inserted node gets relinked.
4. **Invalidate stale inbound edges:** any resolved edge pointing *into* this file
   whose target id no longer exists reverts to `dst_id = null` (becoming a hole
   candidate until/unless re-resolved).

This keeps the graph correct under edits without a full rebuild, and it means
deletes and renames surface as holes automatically rather than as silent
corruption. Edges are indexed by both `dst_id` and `dst_symbol` to make steps 3–4
cheap.

### Entry-point detection contract

Dead-code and hole *liveness* are entirely bounded by the entry-point set, so this
is the linchpin — and no static detector catches every dynamic root. Two parts:

- **Detector plugin:** `detect(repo) -> set[node_id]`, one implementation per
  stack. Pluggable; M0 ships the interface + the first real detector (below).
- **User override (the escape hatch):** a `stitchgraph.toml` `[entry_points]`
  allowlist (and `[entry_points.ignore]`) lets a human pin "these are roots even
  if they look dead" and "this really is dead." Without this, the first
  false-positive stale flag on live code burns trust permanently — so the override
  ships in M0 alongside the detector, not later.

#### First detector: Python **library + CLI** package
The initial target shape (and stitchgraph's own shape). Roots:

- **Public API** — names exported from each package's `__init__.py` / listed in
  `__all__`. **Critical for libraries:** the export surface *is* an entry set,
  because external code imports it. A public, exported symbol with no *internal*
  caller is **not dead** — it's the product. Never flag the public API as stale
  for lack of internal callers; that's the canonical trust-burning false positive.
- **`[project.scripts]`** console entry points and `[project.entry-points]`
  plugin registrations from `pyproject.toml`.
- **`if __name__ == "__main__"`** blocks.
- **Tests** (pytest collection) — secondary roots.

"Dead" then means *private/internal* code reachable from none of the above — the
genuinely useful answer for a package.

**Dogfood target:** stitchgraph is itself a Python library + CLI package, so M0's
first real fixture is stitchgraph pointed at its own `core/` + `adapters/` — a
codebase whose structure we know cold and can eyeball for correctness.

**Read-only invariant.** Dogfooding is for *observation*, not *action*:
stitchgraph **never mutates analyzed source** — it only ever writes to its own
index DB. Every operation is advisory; it returns ranked options for a
human/LLM to act on, never edits or deletes code itself. Acting on stitchgraph's
own suggestions about its own code stays off the table until the tool's precision
is trusted on independent fixtures first.

---

## 5. The three regimes

Every capability falls into one of three buckets. This is the most important
honesty in the whole design — it tells you where the matrix earns its place and
where it doesn't.

- 🟢 **Matrix-native** — reachability, transitive closure, centrality, cycles.
  The matrix genuinely *is* the answer.
- 🟡 **Matrix + enriched edges** — works only if edges/nodes carry extra payload
  (arity, types-from-LSP, git churn, test links, data-flow). The matrix is the
  substrate; the payload makes the question answerable.
- 🔴 **Not the matrix** — types, values, runtime, business logic. Delegate to
  LSP / runtime / the LLM. The matrix can only *locate and rank*, never *decide*.

**Scope decision: stitchgraph implements 🟢 and 🟡. It does not implement 🔴 —
for 🔴 it surfaces what the LSP/runtime already found and ranks it by structural
importance.**

---

## 6. Capability catalog

### A. Orientation — "where do I start?" (🟢, highest value)
Turns a 500-file repo into a ranked ~20-node spine.

| Need | Computation |
|---|---|
| Entry points | seed detection (stack-specific rules) |
| Most-depended-on nodes ("read these first") | transitive fan-in / PageRank |
| Layers (foundational → leaf) | topological levels |
| Subsystems / modules | community detection (Louvain / connected components) |
| Critical/fragile nodes | articulation points / cut vertices |
| Minimal read-set for feature X | dominator tree |
| Deepest call chains | longest path |

### B. Reachability & impact (🟢)

| Need | Computation |
|---|---|
| Dead / stale code | `all − reachable(entry_points)` |
| Blast radius of changing X | forward transitive closure |
| Everything X depends on | backward closure |
| "Can A reach B, and how?" | reachability + shortest path |
| Full-stack cross-language path | relation-matrix product |
| Everything that reaches a sink (DB write, etc.) | masked reachability to sink set |

### C. Structure smells / health (🟢)

| Need | Computation |
|---|---|
| Circular dependencies / recursion | strongly-connected components > 1 |
| God objects / coupling hotspots | high fan-in ∧ high fan-out |
| Layering violations | edges pointing "up" against intended direction |
| Instability / abstractness (Martin metrics) | per-module fan ratios |
| Over-connected functions | out-degree outliers |

### D. Implementation holes — the dual of dead code (🟢/🟡)
**Dead code = a node nothing reaches (dangling source). A hole = a reference
that expects a node that isn't there or is only a stub (dangling destination).**
Same machinery, opposite direction.

| Hole type | How the graph sees it | Regime |
|---|---|---|
| Dangling call (no definition) | `CALLS` edge with unresolved `dst` | 🟢 |
| Stub bodies (`NotImplementedError`/`pass`/`...`/`TODO`) **with inbound edges** | node flagged `is_stub` | 🟡 |
| Unimplemented interface/abstract method | concrete subclass `INHERITS`, no overriding member | 🟡 |
| Route with no handler / handler with no route | one side of `ROUTES_TO` missing | 🟢 |
| Emitted event with no listener (or vice-versa) | asymmetric `EMITS`/`HANDLES` | 🟡 |
| Config key referenced but never defined | `REFERENCES` to missing config node | 🟡 |
| ORM field mapping to nothing | `MAPS_TO` with no `DBColumn` | 🟡 |
| DI binding requested but not provided | unresolved dependency edge | 🟡 |
| Incomplete CRUD / symmetry (create+read, no delete) | naming/pattern gap over a node set | 🟡 (low confidence) |

**Killer query:** `is_stub ∧ reachable(entry_points)` — "find unimplemented code
on a live path." Not the 200 stubs nobody calls; the 4 that are landmines.
Reachability turns a flat list of holes into a *ranked list of risks*.

**Honest boundary:** the matrix finds holes that leave a trace (referenced but
unfulfilled). It cannot find a **silent omission** — a feature nobody wrote and
nobody called for — because there's no edge to dangle. Those need intent/spec
(the LLM), not the graph. The tool finds **broken wiring, not missing ideas.**
Many holes are also intentional (plugin stubs, framework-injected deps), so
holes go through the same `needs_review` envelope.

### E. Argument / signature flow (🟡 — the "arg parse" question)

| Need | Regime |
|---|---|
| Arity mismatch (wrong arg count) | 🟡 record arity → scan |
| Unused parameters | 🟡 param node with no inbound `READS` |
| Argument provenance ("where does this value come from?") | 🟡 partial via data-flow edges |
| Type mismatch (int where str expected) | 🔴 **delegate to pyright**; matrix only ranks severity by blast radius |

### F. Loops (🟢 for code, 🟡 for data)

| Need | Regime |
|---|---|
| Call / dependency cycles (recursion, circular imports) | 🟢 SCC on `CALLS`/`IMPORTS` |
| **Data loops** (write→read→write feedback, circular state, value depends on itself) | 🟡 cycles over `READS`/`WRITES` at variable/field granularity |
| Precise, path-sensitive value loops | 🔴 needs real dataflow analysis |

### G. Testing (🟡 — needs `TESTS` edges)

| Need | Computation |
|---|---|
| Untested-but-important code | high centrality ∧ no inbound `TESTS` |
| "Which tests must I run for change X?" | reverse reachability to `Test` nodes |
| Critical untested paths | centrality + coverage overlay |

### H. Change / risk fusion (🟡 — needs git history, optional/late)

| Need | Computation |
|---|---|
| Hidden coupling | files that co-change in git but have **no** structural edge |
| Risk hotspots | high churn ∧ high centrality |
| Review priority | recently changed ∧ large blast radius |

### What the matrix can't decide (🔴 — surface & rank only)
Type correctness, null-safety → LSP. Runtime behavior, which branches fire →
runtime traces. Business-logic correctness, invariants, security intent → the
LLM over semantic payloads. For each, the matrix's contribution is the same:
it can't judge, but it can locate and prioritize ("here are the 6 type errors,
ranked by how many things depend on them").

---

## 7. Issue flagging & urgency model

stitchgraph can flag issues and rank them — but two color scales are in play and
**must not be conflated**, so they are separate fields:

- **Regime** (🟢/🟡/🔴, §5) = *what compute a capability needs* (build-time).
- **Urgency** (🟢/🟠/🔴, this section) = *how alarming a flagged issue is*
  (per-issue, on the result envelope as `urgency`). Orange, not yellow, to keep
  it visually distinct from the regime scale.

### What the matrix can flag *purely structurally* (no LSP, no runtime)

| Issue (matrix-only) | Default urgency |
|---|---|
| Dangling call reachable from an entry point (calls something undefined) | 🔴 |
| Live stub on a critical path (`is_stub ∧ reachable ∧ high fan-in`) | 🔴 |
| Required wiring missing on a live path (route→handler, etc.) | 🔴 |
| Circular dependency (SCC > 1) among modules | 🟠 |
| Data loop / feedback (cycle over READS/WRITES) | 🟠 |
| God object / coupling hotspot (high fan-in ∧ fan-out) | 🟠 |
| Layering violation (edge points "up") | 🟠 |
| Articulation point with no tests (fragile ∧ untested) | 🟠 |
| Possibly-stale, but maybe dynamically reached (AMBIGUOUS) | 🟠 |
| Dead code on no live path | 🟢 |
| Unused parameter / unused private helper | 🟢 |
| Deep-but-isolated chain, minor coupling | 🟢 |

### Suspicion, not diagnosis

The matrix flags **anomalous shapes that correlate with defects**; it never
asserts a bug as fact. Scope it honestly: **stitchgraph finds wiring/structure
defects, not logic bugs.** An off-by-one, a wrong condition, a bad calculation
leave no structural trace and are invisible to the graph — do not promise them.

### Urgency = severity prior × impact

```
urgency = severity_prior(issue_type)  ×  impact(reachability, centrality)
```

`severity_prior` is a fixed prior per issue type. `impact` is what the matrix
computes best — and so **the matrix can rank the urgency of issues it didn't
find.** A type error the LSP reported (🔴 regime, not the matrix's to detect)
still gets its *urgency* from the matrix: on a function 14 things depend on → 🔴;
in dead code → 🟢. The urgency engine is the unifying layer over every issue
source, structural or delegated.

Modulators that move an issue up or down: reachability from entry points (live ↑,
dead ↓), centrality/fan-in (more dependents ↑), test coverage (untested ↑), churn
(recently changed ↑, once git fusion exists).

### Provenance gates the ceiling (no confident-wrong reds)

A wrong 🔴 destroys trust faster than anything, so urgency is gated by the same
confidence machinery:

- `EXTRACTED` + live + high-impact → may be 🔴
- `INFERRED` → caps at 🟠
- `AMBIGUOUS` → caps at 🟠 **and** forces `needs_review`

Nothing low-confidence can ever shout red. Urgency stays coupled to confidence —
"refuse when unsure" applied to severity.

### Operation

`scan(path?)` → a ranked issue list, each with `urgency` + `confidence` +
`review_reasons`. It is the backbone of the report's "Fix now / Look closer /
Cleanup" structure.

---

## 8. The report

Point at a repo, get a Markdown report — `report.py` is a renderer that composes
the operations and prints their envelopes as prose:

```
stitchgraph report ./myrepo > report.md
```

Because every result carries `confidence` + `needs_review`, the report
self-organizes:

```
# Code report: myrepo

## Orientation
- Entry points: ...
- Read these first (top hubs): ...
- Subsystems: ...

## 🔴 Fix now (live, high-impact, high-confidence)
- Dangling calls reachable from entry points: ...
- Live stubs on critical paths: ...

## 🟠 Look closer (anomalies / ambiguous / tech debt)
- Circular dependencies, god objects, layering violations: ...
- Possibly-stale (could be dynamically reached): ...   [reasons]

## 🟢 Cleanup (low risk, informational)
- Stale code on no live path (safe to remove): ...
- Unused parameters / private helpers: ...

## Structure
- Circular dependencies: ...
- Coupling hotspots: ...

## Impact reference
- Blast-radius table for top hubs: ...
```

The report and the MCP server are the same operations rendered for two different
consumers: a human reading Markdown, and an LLM reading compact tool results.

---

## 9. Operation surface (~8 task-level)

| Operation | Returns | Bucket |
|---|---|---|
| `orient(path?)` | entry points + top hubs + layers + subsystems | A |
| `find_stale(path?)` | unreachable nodes + reasons | B/D |
| `find_holes(path?)` | dangling references / live stubs + reasons | D |
| `impact_of(symbol)` | blast radius + which tests | B/G |
| `trace_path(src, sink, relations?)` | full-stack cross-language path + confidence | B |
| `structure_smells(path?)` | cycles, god objects, layering violations, data loops | C/F |
| `scan(path?)` | ranked issue list with `urgency` + confidence + reasons | §7 |
| `get_matrix(scope, relation)` | **bounded** sparse submatrix for deep reading | — |
| `find_symbol / get_callers / get_callees / type_at` | structural primitives | A |
| `reindex(path)` | incremental update (admin) | — |

**On handing the LLM "the full matrix":** never dump a repo-scale N×N matrix —
it's the dense anti-pattern, token-expensive and unreadable. `get_matrix` returns
a *bounded* submatrix (e.g. a 12×12 `CALLS` matrix for one subsystem the user has
already narrowed to), which an LLM reads and reasons over well. The whole-repo
answer is always a derived summary (hubs, layers, clusters), never the raw matrix.

Ship an agent rule file (`CLAUDE.md`-style) alongside, teaching the coding LLM
when to call what ("before editing, call `impact_of`; to understand a request,
call `trace_path`; query the graph before grepping"). Without it the tools get
ignored in favor of grep muscle memory.

---

## 10. Open-source components & licensing

Goal: ship 100% open-source under MIT with **zero copyleft obligations**.

### Borrow (all permissive)
| Component | License | Role |
|---|---|---|
| tree-sitter + grammars | MIT | syntax, incremental reparse |
| pyright / python-lsp-server | MIT | live types / defs / refs (subprocess) |
| python-graphblas / SuiteSparse:GraphBLAS | Apache-2.0 | semiring path algebra |
| LAGraph | Apache-2.0 | BFS / reachability / centrality patterns |
| SQLite | public domain | adjacency store (source of truth) |
| sqlite-vec / hnswlib | MIT / Apache-2.0 | vector index (`find_similar`, optional) |
| sqlglot | MIT | SQL read/write extraction |
| MCP Python SDK / FastMCP | MIT | MCP adapter |
| Typer | MIT | CLI adapter (function → command) |
| networkx / scipy | BSD | prototyping / sparse helpers |
| jedi | MIT | name resolution (if needed) |

A language server is a **separate subprocess** (stdio/JSON-RPC), the same
boundary as shelling out to `git`. So even a GPL LSP wouldn't taint MIT code —
the LSP layer is license-flexible.

### Watch-list — keep these OUT for a headache-free MIT release
- **CodeQL** — *not* open source; license forbids most automated/commercial use.
  Use **Joern (Apache-2.0)** if taint analysis is ever wanted.
- **astroid** — LGPL-2.1, and unnecessary: tree-sitter + a pyright subprocess
  covers structure + live types; jedi covers name resolution.
- **Neo4j (GPLv3) / Memgraph (BSL)** — copyleft / source-available. SQLite +
  derived GraphBLAS matrices is the license-clean choice; do not reintroduce a
  graph DB.

### Borrow patterns, not MCP servers
Borrow *libraries*, never proxy another MCP server. The product is the
confidence/provenance envelope and the cross-language edges — you can't bolt
those onto another server's data model, and MCP-on-MCP doubles the context tax.
Read an LSP-backed MCP (e.g. Serena, MIT) for its LSP-client plumbing; don't run
it as a backend.

---

## 11. Build order

- **M0 — Structural base (mostly borrow).** *Implemented.* Extraction into the
  SQLite adjacency store; `find_symbol / get_callers / get_callees / orient /
  find_holes / find_stale / impact_of / trace_path / scan / reindex` across all
  three surfaces + report. Stack-agnostic core + a pluggable `EntryPointDetector`
  (Python library+CLI detector shipped). *Implementation note:* the M0 Python
  extractor uses the stdlib `ast` module — exact for Python, zero-dependency,
  keeping the core stdlib-only; reachability is a pure-Python frontier BFS.
  tree-sitter + an LSP (live types, incremental, polyglot) and python-graphblas
  (whole-graph sweeps at scale) are the documented drop-in upgrades — same
  contracts, so they swap in without touching the store or operations.
- **M1 — House style.** Weight + provenance on every edge; the refuse-when-unsure
  envelope on every operation. Cheap, high payoff.
- **M2 — Reachability engine (the core value).** *Implemented.* `find_stale`,
  `find_holes`, `orient`, `impact_of`, `scan` (issue flagging + urgency). The
  **GraphBLAS algebra layer** (`core/algebra.py`) runs whole-graph reachability
  sweeps and PageRank hub ranking via python-graphblas, with the pure-Python
  frontier BFS as the reference fallback (the two agree by test). Resolution is
  scope-aware (self / locally-typed receivers) with an optional jedi precision pass.
- **M3 — Cross-language resolver + report.** *Implemented.* A post-extraction
  resolver pipeline (`core/resolve/`): web-route resolver (decorator → Route →
  handler), sqlglot SQL resolver (query → table, READS/WRITES), and ORM resolver
  (model → table/column, MAPS_TO). ORM and SQL converge on the same `db::<table>`
  node, so full-stack `trace_path` crosses route → handler → … → table → column.
  `stitchgraph report` shipped. Next resolver: HTML template → route.
- **M4 — Optional / later.** Data-loop detection (🟡), git-history risk fusion,
  runtime-trace fusion, vector `find_similar`.

The one piece that can't be stack-agnostic is **entry-point detection**, and
dead-code/hole quality is entirely bounded by it (miss an entry point → flag live
code as stale, the dangerous failure). So the agnostic core + reachability engine
get built first against a pluggable detector; the first real detector targets the
**Python library + CLI** shape (§4, *First detector*) and is validated by
dogfooding stitchgraph on its own source.

---

## 12. Traps to avoid

- **Dense `C^k` powers.** Use frontier SpMV over a semiring (the GraphBLAS/LAGraph
  pattern). The difference between fast and unusable.
- **Matrices as primary state.** Adjacency lists are the truth; derive matrices.
- **Rebuilding the structural 80%.** It's solved and free.
- **Asserting "dead"/"hole" as fact.** Always `needs_review` with reasons; a
  false delete is destructive.
- **Dumping the full matrix into context.** Bounded submatrices or summaries only.
- **Forty MCP tools.** ~8 task-level; the schema list is a per-turn context cost.
- **Confidence as decoration.** It gates `needs_review` and propagates through the
  algebra, or it isn't worth carrying.

---

## 13. Open decisions & defaults

Resolved-but-revisitable choices. Each has a working default so implementation
isn't blocked; revisit if reality disagrees.

### 13.1 Evaluation & a precision stance
A tool that suggests deletions must answer "is it *right*?" in the spec, not after.

- **Golden fixtures:** `tests/fixtures/` holds tiny repos with *known* dead code,
  holes, and entry points; every operation is asserted against them.
- **Stance — precision over recall on 🔴:** never flag live code as dead. A missed
  dead function is acceptable; a wrongly-flagged live one is not. Red is reserved
  for unambiguous, live, high-impact structural certainties (§7).

### 13.2 Semiring per query + cycle convergence
Different questions want different semirings; pin them:

| Query | Semiring (⊕, ⊗) |
|---|---|
| Reachability / dead code | boolean (OR, AND) — LAGraph BFS |
| Confidence-weighted path | (max, ×) — best path wins, confidence multiplies |
| Blast-radius size / fan-in | (plus, ×) or count — how many, not how sure |

**Cycle convergence:** under (max, ×) with all weights ≤ 1, products only shrink,
so reachability/confidence closures **converge** even through SCCs — cycles don't
blow up the algebra. Boolean reachability converges trivially (fixed point).

### 13.3 Config file & CLI exit codes
- **`stitchgraph.toml`** is the single config home: confidence weights/threshold,
  `[entry_points]` overrides, `ignore` globs, granularity toggle, urgency tuning.
- **`scan` exit codes:** `0` = no 🔴, `1` = 🔴 issues present, `2` = error. Makes
  `stitchgraph scan` drop into CI / pre-commit, useful beyond the LLM loop.
- **Output:** human text by default; `--json` emits the raw envelope on every
  command (same object the library returns), keeping CLI↔API parity exact.

### 13.4 Scale target
Design target: **correct and snappy to ~100k nodes / ~10k files** on a single
machine. This is the line that justifies GraphBLAS over networkx for the
whole-graph sweeps (reachability, centrality, `scan`); below a few thousand nodes
networkx would do, but the sweeps are where the matrix design earns its place at
the stated scale.
