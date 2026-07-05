# v3.37.0 + v3.37.1 — the honest-indexer release

*2026-07-05 · covers 3.37.0 and its same-day correctness patch 3.37.1 ·
full details: `CHANGELOG.md`, field evidence: `research/18-ha-pod-field-validation.md`*

The first real-coverage POD field run — Home Assistant's helpers test suite
(2,056 tests / 3,274 executed functions, captured with per-test coverage
contexts) audited against a repo-root index — pointed `audit_graph` at the
indexer itself and caught **four bugs**, all invisible from unit tests and all
shipped fixed in this line. The common thread: an index that is quietly wrong
is worse than one that fails loudly, because every downstream answer inherits
the gap without inheriting a warning.

## Fixed: ignore globs now mean what they say (3.37.0)

`[index] ignore` patterns were matched with `PurePath.match`, which anchors at
the RIGHT end of the path and (before Python 3.13) treats `**` as a single
segment. Both directions misfired on the field run: `tests/components/**`
ignored nothing below one directory deep (6,627 files wrongly indexed — a
23 GB index that should have been ~16 GB), while `script/**` swallowed any
nested `**/script/*` (6 real source files wrongly dropped).

The new matcher (`core/globs.py`, shared by both extractors) is root-anchored
gitignore-style: `/`-containing patterns anchor at the indexed root, `**` is
recursive, `*`/`?` never cross a segment, a bare name matches a basename or
directory anywhere, and a directory match covers its subtree.

**Migration:** a pattern that relied on right-anchoring (`vendor/*` meaning
"any vendor dir anywhere") becomes `**/vendor/*`.

## Fixed: no file is ever skipped silently (3.37.0)

880 Home Assistant files — 10% of the codebase, including `core.py`, holding
half the functions the test suite actually executes — use Python 3.12+ syntax
(PEP 695 `type X = ...`). Indexing under Python 3.11, `ast.parse` rejects each
file and the extractor skipped them with no count, no warning, no meta key.
Every skip is now counted and named on the reindex Result (`skipped_files`
meta + a review reason); an index missing files says so.

## Added: tree-sitter Python fallback for newer-than-interpreter syntax (3.37.0)

A `.py` file the running interpreter's `ast` cannot parse is re-parsed with
the tree-sitter Python grammar — one grammar, versioned independently of the
interpreter, tracking current syntax — and extracted at structural fidelity on
the standard id/kind conventions (class-scope defs re-kinded METHOD, call
edges, imports, test roles). Rescued files are counted in
`python_fallback_files` meta. `.py` stays out of the tree-sitter extension
map: stdlib ast owns Python; the grammar sees only the explicit fallback list.
On the field run: all 880 files rescued, zero missing.

## Fixed: rescued files participate in cross-file resolution (3.37.1)

The 3.37.0 fallback bolted rescued files onto the graph AFTER the Python
extractor finished, so a call from a normal file into a rescued one resolved
against nothing and was silently dropped (unknown call names are dropped by
design — call holes are unreliable). Home Assistant's rescued files include
`core.py`, the hub everything routes through: with an honest denominator,
audit_graph recall collapsed from 0.975 to 0.299 — a complete graph with
severed edges.

Now the rescued symbols join the Python extractor's symbol table BEFORE its
reference pass (module nodes re-ided to the dotted-qualname convention so
imports bind), and the rescued files' own unresolved references re-resolve
against the full table through the standard name-based rules (INFERRED single
candidate / AMBIGUOUS homonym fan-out; still-unknown names dropped, never
leaked as phantom holes). Cross-boundary reachability flows in both
directions.

## Fixed: streaming reindex endgame survives a failing cleanup (3.37.0)

On the field run, the glob-bloated index filled the disk during the final
`DROP INDEX`; the enclosing transaction rolled back edge-dedup with it and
ANALYZE / generation / root-meta never ran — a silently duplicate-edged index.
The dedup now commits in its own transaction; a failed temp-index drop
degrades to a warning (disk cost, never a correctness cost).

## The moral

`audit_graph` exists to measure the call graph against runtime ground truth.
Its first field deployment measured the *indexer* instead — recall 0.975 on a
half-blind graph, 0.299 on a complete-but-severed one — and both numbers were
the tool working: each round's anomaly named its bug. Round 3 (complete AND
connected) is the number that finally characterizes static reach on Home
Assistant; it lands in `research/18`.
