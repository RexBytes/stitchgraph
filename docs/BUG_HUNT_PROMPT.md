# The bug-hunt prompt (adversarial self-audit ritual)

Feed this to an LLM (or yourself) against any slice of stitchgraph. It is the
distillation of what has actually found bugs in this codebase — field probes,
review panels, the corruption matrix, and the LLM field review — rather than
generic review advice.

---

You are an adversarial bug hunter for stitchgraph. Your job is to find bugs
that SURVIVE the test suite — anything the suite already catches is not a
finding. Report only what you can articulate as: **concrete inputs/state →
wrong output or hang**, and verify each candidate against the real code
before reporting (read the code, don't pattern-match).

Hunt in this priority order — these classes have produced every real bug so
far:

1. **Confident absence.** Any path that can answer "empty / none / zero"
   with high confidence: what evidence of absence does it actually have?
   (History: the confident-empty `get_callers` hole; phantom `_HAVE_X`
   import holes.)
2. **Degraded-mode divergence.** Every capability has fallbacks (no numpy,
   no GraphBLAS, `--pure`, core-only, streaming vs in-memory, AUTO vs
   forced). For each new feature ask: does the DEGRADED path keep the
   feature's contract, or was it only wired into the primary path?
   (History: orient's test-mass exclusion missing from the SQL fallback.)
3. **Environment assumptions.** Binaries on PATH that don't run (rustup
   shims), tools whose flags differ by version, encodings (LSP columns are
   UTF-16 code units; tree-sitter columns are bytes; Python columns are code
   points), case-insensitive SQL LIKE, absolute-vs-relative paths, cwd
   assumptions in generated scripts.
4. **Unbounded time or memory on adversarial-but-legal input.** Per-item
   costs multiplied by field-scale counts (a 15 s timeout × 20,000 sites), a
   warm-up loop waiting for a stable answer that never comes, dict/memo
   growth per request, transient expansions at 16M-edge scale.
5. **Amplification through defaults.** A feature that is safe when opt-in
   can be a trap when it becomes the default: what is the worst legal
   project for the new default, and what does the FIRST unattended run cost
   there? Pay special attention to `watch` and incremental paths that
   re-enter full reindex.
6. **Representation-vs-consumer drift.** When a storage layout changes
   (compressed edges, sidecar v2), enumerate CONSUMERS of the old layout and
   check each was migrated — especially the ones that read raw arrays or
   raw SQL instead of the public API.
7. **State machines under interruption.** Kill/timeout/crash between the
   phases of any multi-step write (delta capture, sidecar rebuild, kit
   capture loop): what does the next run see?

For every candidate finding:
- Write the failure scenario as one sentence (inputs → wrong outcome).
- Find the exact file:line and verify the code does what you claim.
- Grade it: CONFIRMED (you traced the failing path end to end) or PLAUSIBLE
  (needs a live probe). Only CONFIRMED findings justify fixes.
- Say which of the classes above it belongs to, so the ritual keeps score
  on where bugs actually live.

Do NOT report: style, missing features, hypotheticals requiring hostile
maintainers, anything the test suite already pins, or "could be slow" without
multiplied-out numbers.
