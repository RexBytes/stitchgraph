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

### Edges (typed, weighted, provenanced)
Relations: `CALLS, IMPORTS, INHERITS, READS, WRITES, QUERIES, ROUTES_TO,
RENDERS, SUBMITS_TO, MAPS_TO, RETURNS, REFERENCES, TESTS, EMITS, HANDLES,
RUNTIME_HITS`

```
Edge {
  src, dst:    node_id
  relation:    RelationType
  weight:      float          # 0..1 confidence in THIS edge
  provenance:  EXTRACTED | INFERRED | AMBIGUOUS
  location:    file:line:col
  source:      tree-sitter | lsp | heuristic | runtime | config
}
```

`EXTRACTED` = read directly from syntax/LSP (weight ~1.0). `INFERRED` = heuristic
(weight < 1.0). `AMBIGUOUS` = multiple candidates, none dominant (low weight,
alternatives recorded).

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

- **M0 — Structural base (mostly borrow).** tree-sitter + LSP extraction into the
  SQLite adjacency store; `find_symbol / get_callers / get_callees / type_at`
  across all surfaces. Stack-agnostic core + a pluggable `EntryPointDetector`.
- **M1 — House style.** Weight + provenance on every edge; the refuse-when-unsure
  envelope on every operation. Cheap, high payoff.
- **M2 — Reachability engine (the core value).** Derive CSR matrices, wire
  python-graphblas / LAGraph; ship `find_stale`, `find_holes`, `orient`,
  `impact_of`, and `scan` (structural issue flagging + urgency). Dead code,
  holes, orientation, ranked issues — the headline features.
- **M3 — Cross-language resolver + report.** Link one web framework + one ORM
  end-to-end (`trace_path`); ship `stitchgraph report`.
- **M4 — Optional / later.** Data-loop detection (🟡), git-history risk fusion,
  runtime-trace fusion, vector `find_similar`.

The one piece that can't be stack-agnostic is **entry-point detection**, and
dead-code/hole quality is entirely bounded by it (miss an entry point → flag live
code as stale, the dangerous failure). So the agnostic core + reachability engine
get built first against a pluggable detector; the first real detector needs the
target stack.

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
