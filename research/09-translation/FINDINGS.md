# Translation (port one language to another): does the matrix assist?

**Question (maintainer):** we hadn't tested the cross-language *translation* use case — stitchgraph's
`graph_diff` / body-matrix was pitched for "translation / plan-vs-actual". Does the graph help port
code (e.g. Python → JavaScript)?

Probed cheaply at the **tool level** first (a Python `calc.py` — `compute()` calls `add()`+`mul()` —
vs three JS "ports"), because that decides whether an agent race is even testing a real capability.

## What the graph actually does across a translation

| Scenario | graph_diff verdict | Correct? |
|---|---|---|
| Faithful JS port | `equivalent: True` | ✅ right |
| JS port that **drops** the `mul` call (a real defect) | `equivalent: False`, `edges_only_a: compute CALLS mul` | ✅ **catches it** |
| JS port that **inlines** `add`/`mul` (a correct work-around, identical behaviour) | `equivalent: False`, `nodes_only_a: add, mul` | ❌ **false positive** |
| A data-flow change that preserves the call graph, *across* languages | `body_changed: []` | ❌ **not detected** (body-shape is oracle-only cross-language; it *is* detected within one language) |

Also: cross-language diff is **name-based**, so it self-reports `confidence 0.60, needs_review:
"name-based result — verify before relying on it"` — honest, but low-trust.

## Honest verdict

- **The LLM owns the hard part of translation and the graph can't help there.** Idiom mapping and
  platform work-arounds — Python file I/O → browser `localStorage`, `numpy` → a hand-rolled solver,
  a Python module → an in-page JS app — are exactly what an LLM is good at, and they *legitimately
  change the call structure*. That's the creative 20%.
- **The graph's only translation contribution is a mechanical completeness check** — "every source
  symbol has a same-named counterpart" — and it has a **structural signal-to-noise problem**: it
  flags *every* intentional restructuring as a discrepancy (the inlining false-positive above). Since
  good translations restructure freely, most flags on a well-adapted port are noise the human/LLM
  must triage. It flags **structural divergence**, and cannot tell **defect from adaptation**.
- **Where it could still net-help:** a *large* port where the LLM can't hold the whole source and
  might genuinely *drop* a function — the completeness list would surface the omission (mixed in with
  the adaptation false-positives). This is the same **scale / low-context** regime flagged as the one
  untested favorable case in `research/08`. On a small port an LLM holds entirely, it's redundant.

## Decision: agent port-race not run

A fresh source (`scratchpad/xport/py_src`, a pure-Python numpy-free DC circuit simulator, 11 symbols)
and a graph-derived **port checklist** (every symbol + its call deps, leaf-first order) and a JS
behavioural grader were built and are ready. The A/B port race (agent-with-process vs ad-hoc) was
**not executed**: on a source this small the prediction (from rounds 1–3 + the false-positive above)
is another null — the LLM ports it fine unaided, and the completeness gate is redundant/noisy. Re-run
it only as part of the **large-port / low-context** experiment, where the completeness mechanism has
something to catch. The graph-as-checklist and graph_diff-as-fidelity-gate are demonstrated above;
their value is bounded by the SNR problem, not by whether the plumbing works.
