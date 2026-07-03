# stitchgraph v3.27.0 — release notes

**The front-door release.** No code change — this release makes the project legible to someone
(human or agent) who didn't build it.

## README, rewritten

The old README had grown into a development log: version archaeology in the status header,
§-references into the design doc, and panel jargon a newcomer can't parse. The new one leads with
an introduction and is organised by reader:

- **For humans:** install matrix (what each extra unlocks), a five-minute quickstart — every
  command in it verified against a live index before shipping — the operations as ask→operation
  tables, the behavioural-toolkit workflow (scaffold → run the kit in your own sandbox →
  `find_modes`), exit-code semantics for CI gating, and the trust model in plain words.
- **For agents:** an MCP section with the `stitchgraph-mcp` launch line, a copy-paste Claude
  Desktop / Claude Code config block, and rules of engagement distilled from `AGENTS.md` —
  query-before-grep, respect `needs_review`, never delete on `find_stale` alone, prefer
  `select_tests` when a coverage artifact exists.

The version-history narrative moved where it belongs: `docs/STATUS.md`, `CHANGELOG.md`, and the
per-version `docs/RELEASE_NOTES_v*.md` files.

## Also in this release

- `docs/RELEASE_NOTES_v3.26.0.md` — the D2 dedup release notes, missed when v3.26.0 shipped.

## Tag hygiene note (for the release history)

The tag `v3.7.0` created on 2026-07-03 pointed at the v3.25.0 merge commit; `v3.7.0` is a
documented historical release (the Ruby/PHP/Bash body-matrix release). The current line is
v3.25.0 → v3.25.1 → v3.26.0 → v3.27.0.
