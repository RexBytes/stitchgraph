<!-- ============================================================================
EDITOR'S NOTES — delete this whole block before publishing
=============================================================================

STATUS: Draft 1 (2026-07-03). Written in the maintainer's first-person voice —
read it aloud and rewrite anything that doesn't sound like you.

BEFORE PUBLISHING, DO:

1. VOICE PASS — especially the opening two paragraphs and the closing section;
   those carry your personality or they carry nothing.

2. BENCHMARK SPECIFICS (the "null result" section currently says only "agent
   with the tool versus agent without, on real tasks"). Add whatever you're
   comfortable sharing: which model(s), what task types, roughly how many runs,
   how you scored them. Even rough numbers preempt the inevitable "citation
   needed" comment — the null result is the load-bearing claim of the piece.

3. URLS TO INSERT / VERIFY:
   - PyPI:        https://pypi.org/project/stitchgraph/            (in the standfirst — verify renders)
   - GitHub:      https://github.com/RexBytes/stitchgraph          (standfirst + closing)
   - Issues:      https://github.com/RexBytes/stitchgraph/issues   (closing)
   - Dogfood evidence (link these where research/14 and research/15 are mentioned):
     https://github.com/RexBytes/stitchgraph/blob/main/research/14-dogfood-v3.25.md
     https://github.com/RexBytes/stitchgraph/blob/main/research/15-dogfood-v3.27.md
   - Process record (optional, in the "development-process record" bullet):
     https://github.com/RexBytes/stitchgraph/blob/main/REVIEW_HISTORY.md
     https://github.com/RexBytes/stitchgraph/blob/main/docs/REVIEW_FINDINGS_2026-07-03.md
   - Benchmark experiment logs if you want receipts for the null result:
     https://github.com/RexBytes/stitchgraph/tree/main/research/08-large-repo-impact
     https://github.com/RexBytes/stitchgraph/tree/main/research/07-dogfood-build
   - Academic references in "What I accidentally reinvented" — link on first
     mention if you want an outbound-reference style:
     CPG/Joern: https://ieeexplore.ieee.org/document/6956589  ·  https://joern.io
     WL kernels: https://www.jmlr.org/papers/v12/shervashidze11a.html
     Ekstazi: https://ekstazi.org

4. DECIDE: whether to name the LLM angle explicitly — the repo history shows the
   tool was substantially LLM-built and this week's review/release cycle was
   LLM-driven. It's already public in the commit log; saying it in the post is
   more interesting than letting readers discover it.

5. AFTER PUBLISHING:
   - Add the post URL to the repo README (Develop/links section).
   - Add the "mcp" topic tag to the GitHub repo; submit to MCP server directories.
   - Consider an arXiv preprint of the POD/intrinsic-dimensionality section
     later — the blog post date-stamps the framing either way.
   - Where to share: HN (the honest null-result framing does well there),
     r/ExperiencedDevs, lobste.rs. The POD numbers (27 behaviours / 64 of 2,350
     tests) are the strongest single hook for social posts.

============================================================================= -->

# I built a code-analysis tool to make LLMs better at programming. It didn't work — and what I found instead was better.

*stitchgraph is a local-first code-intelligence engine: point it at a codebase and it answers
questions — what's dead, what breaks if I change this, which tests should I run — across 12
languages, with a confidence score on every answer. It's on
[PyPI](https://pypi.org/project/stitchgraph/) and [GitHub](https://github.com/RexBytes/stitchgraph).
This is the story of building it, benchmarking it honestly, and what happened when I pointed it at
its own source code.*

---

## The null result

I built stitchgraph on a simple thesis: LLM coding agents waste effort rediscovering structure.
Give an agent a pre-built graph of the codebase — every definition, every call, every route, every
SQL table, queryable in milliseconds — and it should code faster and better.

So I benchmarked it. Agent with the tool versus agent without, on real tasks.

**They performed exactly the same.**

I want to lead with that, because most tool announcements don't, and because *why* it tied turned
out to be the most useful thing the project taught me. A capable model can just read. Every answer
the static graph gives — who calls this, where is that defined, what's reachable from here — is
information the agent can recover with grep and a few file reads. The tool compresses seconds, not
capability. Worse, in a way that's to its credit: stitchgraph attaches honest confidence to every
answer, and when it says *"0.6 confidence, name-based resolution, verify before acting"*, a
competent agent goes and reads the code anyway. Calibrated honesty made it trustworthy by making
it non-load-bearing.

One capability broke the pattern — and it's the one that doesn't compete with reading at all.

## The part that worked: measuring what code *does*

The behavioural toolkit doesn't analyze source. It analyzes an execution record: a matrix of
*which test executed which function*, captured by running your own test suite under coverage (the
tool never runs your code — it generates a sandboxed capture kit and reads the inert result).

Then it does something I borrowed from fluid dynamics rather than software engineering: **proper
orthogonal decomposition** (POD — mean-centred SVD) of that matrix. In fluids, POD extracts the
dominant modes of a turbulent flow. Here, the singular vectors are the *behavioural modes* of your
test suite — sets of functions that fire together — and the spectrum tells you something no amount
of reading can: **how many independent behaviours your suite actually exercises.**

On stitchgraph's own suite: 2,350 tests, 940 logical test rows, 754 executed functions. The answer:

- **27 independent behaviours.** That's the suite's intrinsic dimensionality. Not 2,350 — 27.
- **64 tests cover every executed function.** The other ~97% add redundant coverage (often
  legitimately — parametrized cases share coverage profiles while testing different data — which
  is why the tool flags them for review and never auto-deletes).
- The modes are legible: the top one is the entry-point machinery, the second is polyglot
  extraction, and so on down — the suite's runtime architecture, recovered unsupervised.

Around that core sit the practical operations: *which tests should this PR run* (`select_tests`),
*which live functions does no test execute* (`find_gaps`), *what co-runs with no static
connection between them* (`find_coupling` — this found a real hidden config↔envelope side-channel
in my own code, blind), and *what gained or lost test exposure between two versions*
(`coverage_drift`).

You cannot read your way to any of these numbers. That's the line that mattered: everything on the
static side competed with an LLM's ability to read and tied; everything on the runtime side is
**complementary** to reading — for humans and agents alike.

## The evidence: pointing it at itself, twice

Claims about analysis tools are cheap. So here's the strongest evidence I have: I ran the full
battery on stitchgraph's own source after each of two releases, and it caught real defects both
times — including in code that had just been reviewed and gated.

**Round one** (after v3.25.0, a release that had just absorbed a 24-finding external code review):
the newest operation, `runtime_risk`, returned "no hotspots" on stitchgraph itself. Silently. The
cause: coverage file-ids are relative to the indexed root, git churn paths to the repo root, and
the join between them matched nothing on any src-layout project. Every gate had passed — the op
just answered an empty question confidently. Dogfooding caught it in one run.

**Round two** (after v3.27.0, which included a large deduplication refactor): `find_stale` flagged
a function called `parse_tree` in the freshly refactored shared module. It was right. My refactor
had *added* the shared helper but never wired the nine call sites to use it — and the linter then
auto-removed the unused imports, hiding the slip. Here's the part worth dwelling on: the refactor
was gated by a **byte-identical output differential** and a **1,618-test oracle battery**, and both
passed — *because dead code has no outputs*. Output-equivalence oracles prove a refactor changed
nothing, including that a helper changed nothing because nothing called it. Only a liveness view
sees that. `find_gaps` then corroborated from the runtime side: its untested-dead list was exactly
that function plus the one known advisory.

And the refactor itself gave the runtime toolkit a controlled experiment. Before/after a
~400-line, nine-file deduplication:

- **Intrinsic dimensionality: 27 → 27.** The strongest runtime statement of "behaviour-preserving"
  I know how to make.
- **`coverage_drift` narrated the refactor from coverage alone**: functions that lost coverage =
  exactly the nine deleted per-language copies; functions that gained it = exactly the new shared
  module. A behavioural changelog, derived without reading the diff.
- The static graph watched too: the duplicated frontends had shown up as their own 329-node
  cluster in `find_subsystems` (the graph had, in effect, *recommended the refactor*); after it,
  the cluster shrank and 25 duplicate-driven noise findings evaporated from `scan`.

Two rounds, each finding strictly less than the last, converging on a fixed point of one known,
documented advisory. A tool for finding problems, run on itself until it has nothing left to say —
that's the closest thing to a self-certification the genre allows, and every number above is
reproducible from the repo (`research/14`, `research/15`).

## What I accidentally reinvented

I built stitchgraph largely without the academic literature, and when I finally mapped it against
prior work, the honest answer is: **much of the foundation replicates university research** —
independently. I think that's worth stating plainly, for two reasons: you should know what's new
and what isn't, and independent convergence is its own kind of evidence that these ideas are the
natural ones.

- The layered call ↔ statement ↔ expression graph is the **Code Property Graph** (Yamaguchi et
  al., IEEE S&P 2014 — the tool Joern), reinvented.
- Name/order-invariant clone detection over dependence graphs goes back to **Krinke** and
  **Komondoor & Horwitz** (2001); the Weisfeiler–Lehman kernel is Shervashidze et al. (2011).
- The coverage matrix is what the literature calls **program spectra**; clustering execution
  profiles dates to Dickinson, Leon & Podgurski (2001). Greedy minimal test covers are
  Harrold–Gupta–Soffa (1993); test selection is a whole field (Ekstazi runs it in production).
  Feature location from test execution is Wilde & Scully (1995) and Eisenbarth et al. (2003).
- Clustering call graphs for architecture recovery, PageRank for key classes, churn-based risk:
  all established.

Where I'd claim actual novelty, having looked:

1. **The intrinsic-dimensionality framing.** The field has pointed the coverage matrix at fault
   localization and suite reduction for twenty years. *"Your suite exercises 27 independent
   behaviours — here they are, auto-labelled"* as a first-class, developer-facing metric appears
   to be new. Standard math, standard matrix, new question.
2. **Calibrated honesty as an API contract for agent consumers.** Every answer carries
   confidence + provenance + machine-actionable reasons to doubt it, provenance caps how loudly a
   finding may shout, and the tool refuses rather than guesses. Alarm-ranking research exists;
   *designing the interface around a consumer that will act on the answer without judgment* is a
   problem the literature is only beginning to have.
3. **The development-process record.** stitchgraph was substantially built by LLMs under an
   adversarial multi-model review process — 280+ documented panel rounds with severity-weighted
   release gates, differential oracles pinning every risky path, an honesty ledger of negative
   results, and now two self-analysis rounds converging to a fixed point. I don't know of a
   comparably documented longitudinal record of LLM-driven development — including its failures,
   like the benchmark result this post opened with.

## What I'd tell you to actually do with it

```bash
pip install 'stitchgraph[all]'
cd your-project
stitchgraph reindex . --db stitchgraph.db
stitchgraph report --db stitchgraph.db        # orientation, issues, risk — one page
```

Then, for the part that will tell you something you don't already know: generate the coverage kit
(`stitchgraph scaffold-coverage`), run it in your own sandbox, and ask
`stitchgraph find-modes --coverage coverage_modes.json`. The first time you learn your
five-thousand-test suite has a behavioural dimensionality of 30, and that 70 tests cover every
function you actually execute — that's the moment this project exists for.

Agents get the same operations over MCP (`stitchgraph-mcp --db …`), with the envelope telling them
exactly how much to trust each answer. Everything is local, read-only on your source, and
MIT-licensed.

If you find something — or your dogfooding catches something mine didn't — the
[issue tracker](https://github.com/RexBytes/stitchgraph/issues) is open. That's how the last three
releases happened.
