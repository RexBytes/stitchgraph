#!/usr/bin/env python3
"""Research spike (IDEAS.md §1): does stitchgraph's *structural* signal predict
where code actually gets changed/fixed over time?

`risk()` blends git churn with centrality, so validating risk-vs-fixes directly is
circular (churn is both input and label). This isolates the non-circular question:
does **structural centrality alone** (fan_in + fan_out, computed with NO git input)
correlate with a file's **historical change frequency** (the proxy "fix-proneness"
label, read from `git log`)?

A positive correlation supports the premise behind `risk()` — that centrality
carries fix-proneness signal independent of churn.

On stitchgraph's own repo: Spearman ~= 0.65, top-5 central files overlap 4/5 with
top-5 most-changed. Suggestive, NOT conclusive: a single repo whose history is
skewed by its own development. The honest next step (IDEAS.md §1) needs a corpus of
external repos with diverse git history — which the current environment cannot clone
(registry downloads give sdists without `.git`), so the maintainer would provide
them. This is exploratory research, NOT part of the stitchgraph package.

Run:  PYTHONPATH=src python research/risk_centrality_check.py [path-to-git-repo] [src-subdir]
"""
from __future__ import annotations

import collections
import subprocess
import sys
from pathlib import Path

import stitchgraph as sg
from stitchgraph.core.reach import fan_in, fan_out


def spearman(a: dict[str, float], b: dict[str, float], keys: list[str]) -> float:
    def ranks(d):
        order = sorted(keys, key=lambda k: d.get(k, 0))
        return {k: i for i, k in enumerate(order)}
    ra, rb = ranks(a), ranks(b)
    n = len(keys)
    if n < 2:
        return 0.0
    d2 = sum((ra[k] - rb[k]) ** 2 for k in keys)
    return 1 - 6 * d2 / (n * (n * n - 1))


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    sub = sys.argv[2] if len(sys.argv) > 2 else "src/stitchgraph"
    index_path = repo / sub
    prefix = f"{sub}/"

    # structural centrality per file — NO git input
    cent: collections.Counter[str] = collections.Counter()
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(index_path))
        fi, fo = fan_in(store), fan_out(store)
        for n in store.all_nodes_full():
            f = n.id.split("::", 1)[0]
            cent[f] += fi.get(n.id, 0) + fo.get(n.id, 0)

    # historical change frequency per file — the fix-proneness label, from git
    log = subprocess.run(["git", "-C", str(repo), "log", "--name-only",
                          "--pretty=format:"], capture_output=True, text=True).stdout
    churn: collections.Counter[str] = collections.Counter(
        line[len(prefix):] for line in log.splitlines()
        if line.startswith(prefix) and line.endswith(".py"))

    common = [f for f in cent if f in churn]
    rho = spearman(cent, churn, common)
    top_c = [f for f, _ in cent.most_common(5)]
    top_h = [f for f, _ in churn.most_common(5)]
    print(f"repo: {repo}  src: {sub}")
    print(f"files compared: {len(common)}")
    print(f"Spearman(structural centrality, historical change count) = {rho:.3f}")
    print("\n top-5 by structural centrality   | top-5 by actual change count")
    for a, b in zip(top_c, top_h):
        print(f"  {a:30} | {b}")
    print(f"\n top-5 set overlap: {len(set(top_c) & set(top_h))}/5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
