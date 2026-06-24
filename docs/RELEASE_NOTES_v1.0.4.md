# stitchgraph v1.0.4 — confidence honesty for receiver calls & structural findings (issues #10, #11, #15)

A patch release that closes the last of the field-audit batch. Three reporting/confidence
fixes, **none of which change reachability**: each one keeps the cardinal invariant — live
code is never flagged dead. The point throughout is that *a finding's confidence should be a
function of the evidence it rests on*, end to end.

## What changed

### Receiver calls to a single same-named symbol are `INFERRED`, not `EXTRACTED` (issue #10)

A call like `obj.save()` whose name matched exactly one project definition was asserted at
full `EXTRACTED` confidence. But without type inference the receiver's type is unknown, so
the lone match might be a homonym `save` on a *different* (stdlib/third-party) class —
over-claiming.

- **tree-sitter extractor** (no type model): every receiver-based call (`obj.save()`,
  `Class::save()`, `x->save()`) is now labelled `INFERRED` even when only one name matches,
  detected receiver-aware across every language (member / field / selector / scoped access;
  plus the Java `object` and Ruby `receiver` fields). Direct calls (`save()`) and
  constructors (which name a type directly) stay `EXTRACTED`.
- **Python `ast` extractor**: scope-aware resolution still wins first, so `self.save()` and
  a locally-typed `r = Repo(); r.save()` resolve to the real method and stay `EXTRACTED`.
  Only the *unknown-receiver* fallback — `x.save()` for an external/parameter `x` that the
  name-only path matched to a lone project `save` — is demoted to `INFERRED`. This removes
  the Python↔tree-sitter asymmetry a reviewer flagged during the panel.

**The edge weight stays 1.0**, so the edge still counts fully for reachability and
`find_stale` — the demotion lowers only the asserted *confidence*, never the *liveness*.
That is the cardinal-safe direction: it can never turn a live symbol's only caller into a
dropped edge and flag it dead.

### `scan` structural findings reflect the provenance of their edges (issue #11)

`scan`'s cycles and god-objects were computed over the adjacency graph with no regard for
edge provenance, so a cycle or high-coupling node that existed *only* because of `AMBIGUOUS`
(over-approximated homonym) or `INFERRED` (heuristic) edges was reported at the same
🟠 urgency as one backed by confident `EXTRACTED` edges. On a language without type
resolution that made most structural findings indistinguishable artifacts — exactly the
place the "every answer carries a confidence" promise wasn't being kept.

Now each cycle / god-object:

- carries a `confidence` and a `needs_review` flag derived from its participating edges;
- reports its **confident-only degree** (`confident_edges` for a cycle; `confident_fan_in` /
  `confident_fan_out` for a god-object) alongside the raw degree;
- is **capped to 🟢** (so it sinks in the ranking and never shouts) when the coupling is
  dominated by name-ambiguous / heuristic edges, with a reason string like *"rests mostly on
  name-ambiguous/heuristic edges — verify before acting."*

A confidently-linked cycle or god-object keeps its 🟠 "look closer." This reuses the
provenance the graph already stores; it's a reporting/ranking change in the `scan` path, not
a new analysis.

### `LIMITATIONS.md` `--precise` wording corrected (issue #15)

The escape-hatch note implied `--precise` "disambiguates" by pruning the `AMBIGUOUS`
siblings. It does not — it is **additive**: it adds a confident go-to-definition edge and
leaves the competing candidates in place, so it never deflates `impact_of` / `find_stale`
reachability on its own. That is deliberate: pruning the losing siblings would let a single
jedi mis-resolution drop a live symbol's only caller and flag it dead — the cardinal sin.
The wording now says so, and a new entry documents why a single-candidate receiver call is
`INFERRED` (issue #10) so it isn't mistaken for under-claiming.

## Verification

`pytest` 176 passed (4 new regression tests: tree-sitter receiver-call demotion +
cardinal-safety, Python unknown-receiver demotion vs. scope-resolved EXTRACTED,
artifact-cycle demotion vs. confident-cycle, artifact-god-object demotion) · ruff clean ·
mypy clean against **both** the dev pack (1.10.6, loose stub) and the **pinned** bundled
0.13.0 (strict `Literal` stub) — the version CI installs. Confirmed by full three-model
panels (opus + sonnet + haiku). Dogfood `src/`: 3 advisory / 0 holes. Full trajectory in
`REVIEW_HISTORY.md`.
