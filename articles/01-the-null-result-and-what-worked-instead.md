<!-- ============================================================================
EDITOR'S NOTES — delete this whole block before publishing
=============================================================================

STATUS: Draft 2 (2026-07-07, updated from draft 1 of 2026-07-03). Written in
the maintainer's first-person voice — read it aloud and rewrite anything that
doesn't sound like you. Draft 2 adds everything that happened between v3.28
and v3.50: the field campaign (Home Assistant, Django), the scale/LSP arcs
that qualify the null result, the third dogfood round, the external LLM
review, and the adversarial self-audit. New/rewritten sections are marked
[NEW IN DRAFT 2] — those need the voice pass most.

BEFORE PUBLISHING, DO:

1. VOICE PASS — especially the opening two paragraphs, the closing section,
   and every [NEW IN DRAFT 2] block.

2. BENCHMARK SPECIFICS (the "null result" section currently says only "agent
   with the tool versus agent without, on real tasks"). Add whatever you're
   comfortable sharing: which model(s), what task types, roughly how many runs,
   how you scored them. Even rough numbers preempt the inevitable "citation
   needed" comment — the null result is the load-bearing claim of the piece.

3. URLS TO INSERT / VERIFY:
   - PyPI:        https://pypi.org/project/stitchgraph/            (in the standfirst — verify renders)
   - GitHub:      https://github.com/RexBytes/stitchgraph          (standfirst + closing)
   - Issues:      https://github.com/RexBytes/stitchgraph/issues   (closing)
   - Dogfood evidence (link where research/14, /15, /25 are mentioned):
     https://github.com/RexBytes/stitchgraph/blob/main/research/14-dogfood-v3.25.md
     https://github.com/RexBytes/stitchgraph/blob/main/research/15-dogfood-v3.27.md
     https://github.com/RexBytes/stitchgraph/blob/main/research/25-dogfood-v3.46.md
   - Field campaign (link where mentioned):
     https://github.com/RexBytes/stitchgraph/blob/main/research/16-ha-field-analysis.md
     https://github.com/RexBytes/stitchgraph/blob/main/research/18-ha-pod-field-validation.md
     https://github.com/RexBytes/stitchgraph/blob/main/research/19-django-field-findings.md
   - LSP + coverage arcs:
     https://github.com/RexBytes/stitchgraph/blob/main/research/24-lsp-backend.md
     https://github.com/RexBytes/stitchgraph/blob/main/research/26-turnkey-coverage.md
   - External review + self-audit (process-record bullet):
     https://github.com/RexBytes/stitchgraph/blob/main/docs/LLM_REVIEW.md
     https://github.com/RexBytes/stitchgraph/blob/main/docs/BUG_HUNT_PROMPT.md
     https://github.com/RexBytes/stitchgraph/blob/main/research/27-adversarial-self-audit.md
   - Process record (optional):
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
   tool was substantially LLM-built and the review/release cycle was LLM-driven;
   draft 2 leans into it (the Opus 4.8 field review and the self-audit are now
   part of the story). It's already public in the commit log; saying it in the
   post is more interesting than letting readers discover it.

5. DJANGO FINDING: research/19's Atom1Feed/stylesheets finding is reproducible
   against django-5.2.15 with stdlib only. If you plan to report it upstream,
   do that BEFORE publishing (or soften the wording to avoid zero-daying a
   cosmetic-but-documented behaviour gap).

6. AFTER PUBLISHING:
   - Add the post URL to the repo README (Develop/links section).
   - Add the "mcp" topic tag to the GitHub repo; submit to MCP server directories.
   - Consider an arXiv preprint of the POD/intrinsic-dimensionality section
     later — the blog post date-stamps the framing either way.
   - Where to share: HN (the honest null-result framing does well there),
     r/ExperiencedDevs, lobste.rs. The POD numbers (27 behaviours / 64 of 2,350
     tests) and the HA recall number (0.991) are the strongest hooks.

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

<!-- [NEW IN DRAFT 2] — voice pass needed. This subsection is the honest update to the
null result: two places where "a capable model can just read" stopped being true in the
field. Keep it short; it qualifies the headline claim without retracting it. -->

### Two places the tie breaks (an update)

The months since I ran that benchmark added two honest qualifications.

**Scale.** "The agent can just read" is true at ten thousand lines and false at Home
Assistant's size: 6,728 files, 59k definitions, **16 million resolved edges**. Nothing
reads that. Transitive questions — *what ultimately depends on this function*, *what does
this suite actually reach* — stop being greppable long before that point, and the graph
answers them in seconds: the 12 MB memory-mapped sidecar builds in 2.5 s, and a
strongly-connected-components pass over all 59k nodes runs in 2 s — the pure-Python
reference for one sweep took 46 s. The static side doesn't beat reading; it outlives it.

**Precision the reader can't get.** The graph now drives real language servers
(typescript-language-server, rust-analyzer, gopls, clangd) over its own call sites and
upgrades name-guesses to type-resolved edges — +497 confident edges on hono, +147 on fd,
each hand-verified. That's not information an agent recovers with grep either; it's
information the *compiler's* machinery has and a reader approximates.

The core lesson survives both updates: everything that competes with reading ties on
codebases a model can hold, and the durable value sits where reading was never the
competition.

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

## The evidence: pointing it at itself, four times

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

<!-- [NEW IN DRAFT 2] — voice pass needed. Rounds three and four happened after draft 1. -->

**Round three** came nineteen releases later (v3.46, `research/25`), installed from the released
PyPI wheel and pointed at its own repo again. It caught three more real defects — a dead public
function nothing had ever called, and two parameters threaded through helpers since their first
version that no body ever read. It also taught a different lesson: the raw `scan` produced 435
findings, and hand-verifying them showed most were *correct arithmetic answering the wrong
question* — a heavily-tested helper isn't a "god object", a pytest fixture isn't a "read this
first" hub. Calibrating those judgments against the dogfood evidence took the finding count from
435 to 45 without suppressing a single verified true positive. Dogfooding doesn't just find bugs;
it calibrates taste.

**Round four** dropped the pretense of routine and went adversarial (v3.50, `research/27`): I wrote
the bug-hunt prompt I'd want pointed at *someone else's* project — seven failure classes, a
confirmed-vs-plausible evidence bar, mandatory write-ups for suspicions that dissolve — and fed it
to myself plus two parallel hunting agents. Fourteen confirmed bugs, including an embarrassing one
(the file-watch path silently downgraded the analysis quality of every file you edited) and a
cluster with a common cause: features field-validated on real corpora, but always on the machine
that built them. Every fix shipped pinned by a test. The prompt is in the repo
(`docs/BUG_HUNT_PROMPT.md`) if you want to run it against your own project — or mine.

<!-- [NEW IN DRAFT 2] — voice pass needed. The whole field-campaign section is new. -->

## Taking it to strangers' code

Self-analysis has an obvious weakness: I know my own codebase. So the same battery went to two
codebases that owe me nothing, with a rule — **no number leaves the run without hand-verification
against the source.**

**Home Assistant** (6,728 files, 59k nodes, 16M edges) was the scale trial. Indexing end-to-end
held 158 MB peak under a 4 GB ulimit; the interesting part was what the runtime side did to the
static side. I captured real per-test coverage from HA's helper suite (2,056 tests × 3,274
executed functions) and asked a question most static tools never submit to: *of the functions the
tests actually executed, what fraction does static reachability find?* Three rounds later the
answer was **0.991** — but the three rounds are the story. Round one scored 0.975 while the graph
was silently missing 880 files (a great number hiding a hole); round two fixed the files, exposed
the honest denominator, and scored 0.299; round three stitched the cross-parser edges and earned
the 0.991. Four indexer bugs died on the way. A validation harness that can only confirm success
is not a validation harness. The static battery also paid rent directly: a verified list of dead
code in HA's own utils (`rgbww_to_color_temperature` and its private helper, four of five
`deprecation.py` helpers, a legacy loader shim — each grep-verified to zero call sites in the
shipped package).

**Django 5.2.15** (2,873 files, 47k nodes) was the adversarial pick — one of the most-audited
Python codebases alive. The battery plus hand-verification produced one finding I'd take upstream:
the 5.2 release notes say all `SyndicationFeed` classes support `stylesheets`, and `Atom1Feed`
accepts the argument — then silently never writes it. `find_stale` flagged the base hook as
uncalled; reading the flag's *reason* (RSS calls its own override; Atom never calls the hook at
all) turned a dead-code advisory into a behaviour bug with a three-line stdlib repro. That's the
workflow I now believe in: the tool proposes with calibrated confidence, the human disposes with
the source open.

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
   results, four self-analysis rounds, an unprompted field review by a *different* frontier model
   whose findings shipped as fixes within two releases (`docs/LLM_REVIEW.md`), and an adversarial
   self-audit run with a published prompt (`docs/BUG_HUNT_PROMPT.md`). I don't know of a
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
`stitchgraph find-modes --coverage coverage_modes.json`. The kit is turnkey for Python, Rust, Go,
and JS/TS — a generated script runs your suite per-test and converts the result, no wiring
(the Rust one ran fd's 267 tests unedited: dimensionality 7, a covering set of 154). The first
time you learn your five-thousand-test suite has a behavioural dimensionality of 30, and that 70
tests cover every function you actually execute — that's the moment this project exists for.

Agents get the same operations over MCP (`stitchgraph-mcp --db …`), with the envelope telling them
exactly how much to trust each answer. Everything is local, read-only on your source, and
MIT-licensed.

If you find something — or your dogfooding catches something mine didn't — the
[issue tracker](https://github.com/RexBytes/stitchgraph/issues) is open. Reviews, benchmarks, and
bug hunts — including the unflattering ones — are how most of the releases above happened.
