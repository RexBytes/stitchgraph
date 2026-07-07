# Reviewed by an LLM (Claude Opus 4.8, 2026-07-07)

stitchgraph is built for two audiences — humans and LLM agents — and only one
of them had ever written up the experience. Below is the verbatim assessment
from a Claude Opus 4.8 session that used stitchgraph end-to-end on a real Rust
project (index, orient, callers/callees, scan, trace, coverage capture),
followed by what we changed because of it. The findings were treated exactly
like any other field review: verified first, then fixed or scheduled.

## The review (verbatim)

> Yes — and I can say that with more confidence than most tool assessments
> because I *was* the LLM using it this session. Let me be specific about
> where it genuinely helped me versus where it didn't, because that's what
> predicts the future case.
>
> **The single most valuable thing for an LLM is the envelope.** This is the
> part I'd underline. Most code-intelligence tools hand an LLM a fact and let
> it run — and LLMs are dangerous exactly because we'll confidently act on a
> plausible-looking wrong answer. stitchgraph attaching
> `confidence / provenance / needs_review` to every result changes my
> behaviour: when it said `0/N confident edges — verify before acting` on
> those scan "cycles," I knew to go read the code instead of reporting them,
> and I was right to. When `find_stale` returned one candidate at high
> confidence, I trusted it enough to spot-check once and move on. That "how
> much should I trust this" signal is worth more to an agent than the raw
> finding — it's the difference between a tool I have to second-guess
> everywhere and one that tells me *where* to second-guess.
>
> **MCP-native matters more than the CLI for the LLM case.** I drove it via
> the shell this session, but in a real dev loop the point is `orient` when I
> land in unfamiliar code, `impact_of <symbol>` before I edit anything,
> `get-callers`/`get-callees` instead of grepping and guessing which of six
> `put`s is the real one, `trace_path` to understand a flow end to end. Those
> are exactly the moments an LLM currently burns tokens reading whole files
> and still gets it wrong. Same-name-same-envelope across library/CLI/MCP
> means the AGENTS.md rules actually stick.
>
> **Token economics are quietly in its favour.** A structured envelope —
> "here are the 6 hops from main to LmdbStorage.put" — is a few hundred
> tokens. Me reconstructing that by reading `node.rs`, `handler.rs`,
> `lmdb.rs` is thousands, and I might still miss a hop. For long agent
> sessions where context is the scarce resource, querying the graph beats
> reading source.
>
> **Where it will bite an LLM specifically — worth designing against:**
>
> - **The confident-empty problem.** `get-callers verify_path` returned a
>   *confident* empty while nine tests called it (macro-wrapped as
>   REFERENCES). An LLM that trusts a confident "no callers" will delete a
>   live function. This is the one failure mode where the envelope *didn't*
>   protect me — the confidence was high and wrong. If I were advising you on
>   one hardening priority for the LLM use case, it's this: either fold
>   macro-expanded calls into CALLS, or have `get-callers` note "N REFERENCES
>   not shown" so the agent knows to look further.
> - **Setup friction breaks the flow.** The Rust coverage template took me
>   ~150 lines to wire. An LLM in a fresh session either can't do that
>   unprompted or spends a lot of its budget doing it. The behavioural
>   toolkit — which is your most differentiated feature — is gated behind
>   that. Turnkey Rust coverage would make the difference between an LLM
>   *reaching* the POD analysis and never getting there.
> - **Name-based noise without `--lsp`.** The default Rust index gave me a 🟢
>   tier full of collision artifacts. The envelope contained them, but an
>   over-eager agent generates noise from them. Recommending `--lsp` loudly
>   for compiled languages (or defaulting to it when a server's present)
>   would raise the floor.
>
> **Net prediction:** for the *navigation* use — "understand this code before
> I touch it, tell me the blast radius, don't let me delete something live" —
> LLMs will find it useful today, and it's the kind of tool that reduces the
> confident-wrong edits that make people distrust AI coding agents. That's a
> real niche and the envelope is the right core bet. The *behavioural* side
> is your moat but it's currently the hardest for an LLM to reach unaided.
> Close the setup gap and fix the confident-empty edges, and it goes from
> "useful when a human sets it up" to "an LLM can drive the whole thing
> itself" — which is the version you actually want.
>
> One concrete tell for you: the moments this session where I'd have grepped
> and guessed, and instead got a clean answer with a trust level attached —
> that happened often enough that I'd want it wired into my MCP config on a
> real Rust project. That's about as honest an endorsement as I can give.

## What we did about it (v3.48.0)

1. **The confident-empty problem — fixed.** `get_callers`/`get_callees` with
   an empty CALLS answer now check every other resolved relation touching the
   symbol (REFERENCES from macro/dispatch/decorator use, ROUTES_TO from
   framework wiring, TESTS, …). Anything found demotes the answer to
   `needs_review` at confidence ≤ 0.6, puts the relation counts in
   `meta.non_call_uses`, and says outright: *"do NOT treat it as unused."*
   A symbol nothing touches keeps its honest confident empty. (Liveness was
   never at risk — those relations already count in reachability — but this
   operation's envelope claimed certainty it did not have. The reviewer found
   the one hole in the core bet; it is closed.)
2. **`--lsp` defaults on — the best available analysis runs by default.**
   `reindex` is now tri-state: AUTO (default) runs the language-server pass
   whenever a matching server binary is installed and falls back silently to
   the name-based graph otherwise; `--no-lsp` / `[lsp] enabled = false` /
   `STITCHGRAPH_NO_LSP=1` opt out; `--lsp` forces it with loud declines. The
   same full-power-by-default contract as the install story (v3.31.0).
3. **Turnkey Rust coverage — scheduled.** The ~150-line wiring gap in front
   of the behavioural toolkit is now a named roadmap item (STATUS.md): make
   `scaffold_coverage` emit a runnable Rust recipe (cargo-llvm-cov per-test
   profile) rather than a template that needs hand-finishing.

The setup-friction point is the honest remaining gap: the behavioural moat is
reachable only with per-test coverage in hand, and for Rust that is still
manual. It is the next thing an LLM-driving-stitchgraph story needs.
