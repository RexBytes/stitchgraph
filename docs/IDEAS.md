# Ideas / Nice-to-have backlog (parked)

> **Status: parked.** These are exploratory research directions, not committed work
> and not part of the v1.0.0 scope. Do **not** action anything here until the
> maintainer explicitly raises it again. The near-term, committed roadmap lives in
> [`STATUS.md`](STATUS.md#roadmap-whats-left); this file is for longer-horizon,
> higher-risk/higher-reward ideas.

Captured 2026-06-23. Author: maintainer (zorani). The four items below are believed
**related and parallelisable** (see §4).

---

## 1. Validate / iterate effectiveness against real-world repos using bug-fix commits as ground truth

Rather than only synthetic fixtures, measure how good stitchgraph actually is by
pointing it at existing, well-known git repositories and using their history as
**labelled data**:

- Take a commit that **fixes a known issue/bug**. Check out the code *before* the
  fix and run stitchgraph — does it detect/flag the problem (dead code, hole,
  risky hotspot, missing edge, etc.)? Then run it on the code *after* the fix and
  compare.
- Bug-fix commits (and their linked issues) become a stream of "training" /
  evaluation points: before = positive case, after = negative case.
- This gives a real precision/recall signal on real code, complements the panel
  process, and could be turned into a repeatable benchmark harness.

Open questions: which issue/defect classes stitchgraph can realistically detect
vs. which are out of scope; how to label automatically (issue text, CI failures,
test diffs); how to avoid overfitting to one project's style.

## 2. Is there a connection between graph structure and *function* (what the code does)?

Hypothesis: the dependency / call graph for the **same kind of application** is
structurally similar **across languages**. E.g. would the graph of an *audio
player* written in Python and the same app in another language look alike?

- **First, check if it's true** — build the graph for several implementations of
  the same application type (same app, different languages; and different apps,
  same language) and compare structure (motifs, shapes, hub patterns, subgraph
  isomorphism / graph similarity metrics).
- **If it holds**, start building a **pattern database** shipped as part of the
  package, so stitchgraph can **identify what a codebase *does*** from its graph
  (application archetype / "this looks like an audio player / web server / CLI
  parser / ETL pipeline").

Open questions: what's the right structural fingerprint (graph kernels, motif
counts, role/centrality distributions)? How language-invariant is it really?
How big a corpus is needed before patterns are stable?

## 3. If we can infer the *purpose* of code, what else can we do with that?

If §2 works and we can recover intent/purpose from structure:

- What new **helper functions / operations** could the package expose on top of
  that understanding? (e.g. "summarise what this subsystem is for," "find the
  component that plays audio," "locate the auth boundary," purpose-aware
  navigation, archetype-aware risk/orientation.)
- Purpose could sharpen existing ops too (better orient/summaries, smarter
  entry-point inference, archetype-specific dead-code priors).

This is the "so what" of §2 — enumerate the capabilities that become possible
once the graph is tied to function.

## 4. Do all of the above together, across a wide, varied corpus

The three above are related and can run **in parallel**: pick a **wide range of
repos with different applications** (varied domains *and* languages), then
simultaneously (a) validate detection on their fix history (§1), (b) compare graph
structure within/across app types (§2), and (c) prototype purpose-derived helpers
(§3).

Intriguing possibility: we may find **patterns that do something not yet named in
the code** — recurring structures that perform a recognisable function the authors
never labelled. Surfacing/naming those could be a feature in itself.

### Logistics / access note
If repo access is needed and the agent environment can't clone them directly, the
maintainer will **clone the chosen repos and expose them through their own repo**
for access. When this work is picked up: agree a repo shortlist first (diverse
domains × languages), then the maintainer provisions access.

---

_When resuming: start with §2's "check if it's true" step — it's the load-bearing
hypothesis the rest depends on — and §1's benchmark harness, which is useful
regardless of whether §2 pans out._
