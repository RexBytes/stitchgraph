# v3.36.0 — the instant-search release

One change: `find_similar` and `find_component`'s token path is now served by a
persistent similarity sidecar, closing the last "genuinely slow at scale" row in
`docs/PERFORMANCE.md`.

## The problem

Every token-mode query re-derived its world from scratch: `resolved_edges(CALLS)`
materialised every resolved call edge as a Python object (26.8M on the 106k-node
megacorpus — this, not tokenising, was the cost), then tokenised every code node,
then cosined. ~3 minutes per query at field scale; `find_component`, the newest
navigation op, was unusable exactly where it's most useful.

## The fix — the sidecar pattern, third application

`<db>.simcache/`: an **exact-vocabulary** sparse TF matrix — one L2-normalised
CSR row per FUNCTION/METHOD/CLASS node, token dimensions from a stored vocab,
callee-name tokens baked in at build time. A query tokenises the snippet, maps
tokens through the vocab (out-of-vocab tokens still count toward the query norm,
exactly like the reference cosine), and scores every node in one CSR·vector
product. No hashing trick — same maths as the reference path, so results are
identical up to float summation order (pinned by test).

Every contract is inherited from the adjacency sidecar: SQLite stays
authoritative; the sidecar is generation-gated (a `replace_file`/`reindex` bump
refuses it and the next query lazily rebuilds); numpy-gated with a guarded
import; `[index] similarity_cache = false` and `--pure`/`STITCHGRAPH_PURE=1`
force the reference path; failed builds are memoised per generation; deleting
the directory is always safe. The dense-embedder path deliberately bypasses the
sidecar — a registered embedder changes the vector space; persisting embeddings
is the recorded follow-up (STATUS.md).

## Measured (106k-node / 26.8M-edge megacorpus)

| | before | after |
|---|---|---|
| warm query | ~3 min | **<0.1 s** (>1000×) |
| results | — | identical top-k, identical scores |
| one-time build | — | 335 s (one CALLS pass + tokenise 106k nodes) |
| on disk | — | **12 MB** beside the 17 GB index |

## Tests

Ranking equivalence vs the reference path (scores within float summation order);
`replace_file` staleness → rebuild reflects the edit and drops removed symbols;
config + pure-mode gates leave no sidecar behind; `find_component` served
transparently. Full suite green.
