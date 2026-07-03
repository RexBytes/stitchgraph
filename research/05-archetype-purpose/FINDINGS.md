# §2 / §3 — archetype fingerprint + purpose-aware locator (scale-up, 2026-07-02)

Follow-up to the 2026-06-23 spikes (`research/archetype_fingerprint.py`, `research/find_component.py`),
re-run and extended on the current codebase (v3.18.0 — full 12-language body/statement/expression
layers). Two questions the original spikes left open: does the archetype signal survive a *bigger*
corpus, and can stitchgraph's unique **boundary signals** (routes/SQL/events/ORM) *augment* it (an
explicit IDEAS §2 hypothesis)? Plus: quantify the §3 locator and find which ingredient carries it.

Scripts: `research/archetype_scale.py` (§2), `research/find_component_eval.py` (§3). Corpus in the
git-ignored `research/_corpus/` (PyPI sdists + npm tarballs). Exploratory — nothing here ships.

---

## §2 — the semantic-name signal replicates and STRENGTHENS at 2× scale; boundary signals do NOT help

Corpus grown from 5→**11 archetypes** (cli, web, http, template, date, validation, logging, markdown,
orm, cache, lexer), **21 packages** (10 py / 11 js). Four fingerprints, each scored by mean cosine
within groups + leave-one-out nearest-neighbour archetype accuracy (chance ≈ 1/10):

| Fingerprint | same-archetype (×-lang) | same-language | NN archetype acc. | tracks |
|---|---|---|---|---|
| **TOPOLOGY** (degree/hub/kind metrics) | −0.206 | **+0.112** | 2/21 (~chance) | **language** |
| **NAMES_TFIDF** (identifier tokens, generic vocab down-weighted) | **+0.237** | +0.135 | **13/21 (62%)** | **archetype** (language-invariant) |
| **BOUNDARY** (route/SQL/event/ORM/kind fractions) | −0.292 | **+0.276** | 0/21 | **language** |
| **COMBINED** (NAMES_TFIDF ⊕ 0.6·BOUNDARY) | +0.097 | +0.172 | 1/21 | worse than names |

**Conclusions.**
1. **The original 6/10 was not small-n noise.** At double the corpus, NAMES_TFIDF holds at **13/21
   ≈ 62% (~6× chance)** and remains the *only* fingerprint whose same-archetype similarity exceeds
   its same-language similarity (+0.237 > +0.135) — i.e. genuinely language-invariant.
2. **Topology still tracks the extractor, not the function** (2/21, same-language +0.112 >
   same-archetype −0.206) — the 2026-06-23 result replicates cleanly.
3. **NEW — the IDEAS §2 "augment with boundary signals" sub-hypothesis is REFUTED.** As a *global*
   fingerprint, stitchgraph's route/SQL/event/ORM signals track **language**, not archetype (0/21,
   same-language +0.276 ≫ same-archetype −0.292), and folding them into the name vector *degrades*
   it (13/21 → 1/21). Why: those signals are **sparse across archetypes** — only web has routes,
   only orm has MAPS_TO — so for 9 of 11 archetypes the boundary vector collapses onto its kind-mix
   component (class/method/function fractions), which is a language convention (JS = method-heavy,
   Python = function-heavy). Boundary signals are a good **positive detector** for the specific
   archetype that bears them ("this has routes → it's a web framework"), but they are the wrong
   tool for *general* archetype classification, and they must not be blended into the name axis.

**Productization read (unchanged, now firmer):** "identify what a package does" is reachable via the
**semantic-name axis only** — ideally stitchgraph's pluggable `find_similar` dense embedder in place
of TF-IDF — not topology and not boundary-signal augmentation.

---

## §3 — the purpose-aware locator, quantified: 76% P@1 / 0.80 MRR; both ingredients earn their place

`find_component_eval.py` runs a **labelled** query set (17 queries over 13 py + 4 js packages; each
query carries an explicit acceptable-target set) and ablates the two ingredients of the
`find_component` recipe. A hit is correct if the top result's leaf name matches an acceptable target;
MRR uses the rank of the first acceptable hit.

| Mode | P@1 | MRR |
|---|---|---|
| RAW (plain `find_similar`) | 9/17 (53%) | 0.640 |
| −TESTS (drop test code by role + path) | 10/17 (59%) | 0.699 |
| **−TESTS+PUBLIC** (also boost exported API — the recipe) | **13/17 (76%)** | **0.797** |

**Conclusions.**
1. **Both ingredients contribute monotonically:** test-exclusion +6pp P@1 (it removes distractors
   like `test_*.view`, `TestModuleLoader._test_common` that otherwise top the list — see jinja2
   "render a template", flask "match a url route"), and public-boost a further **+17pp** (it lifts
   the real entry point — `Scaffold.route`, `markdown`, `Schema.dump`, `Axios` — over internal
   helpers of similar name). The full recipe reaches **76% P@1 / 0.80 MRR**.
2. **The failure modes are diagnostic, and both point at the dense embedder:**
   - **Minified npm dist tarballs defeat name search entirely.** `marked` and `dayjs` ship *bundled,
     minified* dist code — identifiers are `y.html`, `S`, `L`, `proto.toDate`. Name-based similarity
     has nothing to bind to (RR 0.10 / 0.20). This is a real limitation of the semantic-name axis:
     it needs *source* names, and is blind to obfuscated/bundled code. (A corpus caveat too — index
     the package *source*, not its shipped `dist/`.)
   - **Token cosine drowns a specific function under many same-token siblings.** pygments "highlight
     source code" returns dozens of `*Lexer` classes ahead of the actual public `highlight()` — a
     recall failure of *token overlap* that a semantic embedding (which knows "highlight" ≈ "colorize
     output") would likely fix.
3. **Cross-language works where source ships:** express ("define a route handler" → `app.route`) and
   axios ("send an http request" → `Axios`/`Axios.request`) resolve correctly; only the minified
   packages fail. So the §3 capability is language-agnostic *given readable names*.

**Productization read:** `find_component(query)` is a real, on-brand advisory op — graph supplies the
verifiable role structure (test/exported), ranking stays confidence-carrying. The single highest-
leverage upgrade for BOTH §2 and §3 is swapping token similarity for the pluggable **dense embedder**;
the second is a doc note that purpose-search wants **source**, not minified dist.

---

## What would move this further (not done — needs maintainer)
- A **dense-embedder** run (model2vec) over the same two harnesses — the predicted win for both the
  archetype fingerprint and the locator's recall misses. Needs the optional embedder installed
  (network/model download — the documented optional-dep blind spot).
- A **third language** per archetype (Go/Rust) — pip/npm can't fetch those; needs maintainer-provided
  source (ties into the IDEAS §1/§4 corpus-access blocker).
- Index package **source trees** (not shipped `dist/`) to remove the minification confound.
