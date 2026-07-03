#!/usr/bin/env python3
"""Research spike (IDEAS.md §6, push D — POD over runtime coverage).

The most control-theory-faithful form of §6: Proper Orthogonal Decomposition (= SVD) over a
SNAPSHOT matrix of runtime behaviour. Snapshots = individual test modules run under coverage; each
row is "which functions fired when this test module ran". POD of that matrix yields the dominant
CO-ACTIVATION modes — groups of functions that light up together across executions — a *dynamic*
decomposition that the static call graph can't give.

Pipeline: run N test modules each under coverage.py → executed lines per source file → map to
stitchgraph function nodes (via node location/end_line) → binary snapshot matrix M[module, node] →
POD (SVD of the column-centred M). Report each top mode's dominant functions + directories.

Needs coverage.py (dev extra). Slow (one pytest run per module). Run:
  PYTHONPATH=src python research/06-spectral/pod_coverage.py
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

import stitchgraph as sg
from stitchgraph.core.model import NodeKind

MODULES = ["test_seams.py", "test_risk.py", "test_dataloop.py", "test_config.py",
           "test_eval.py", "test_safety.py", "test_algebra.py", "test_frameworks.py"]
COVDIR = Path("/tmp/claude-0/-home-user-stitchgraph/02fe16e2-c2bb-5262-b2df-5a2a9db3c952/scratchpad/cov")


def run_coverage():
    COVDIR.mkdir(parents=True, exist_ok=True)
    snaps = {}
    for mod in MODULES:
        df = COVDIR / f"{mod}.data"
        jf = COVDIR / f"{mod}.json"
        if not jf.exists():
            subprocess.run([sys.executable, "-m", "coverage", "run", f"--data-file={df}",
                            "--source=src/stitchgraph", "-m", "pytest", f"tests/{mod}", "-q"],
                           capture_output=True)
            subprocess.run([sys.executable, "-m", "coverage", "json", f"--data-file={df}",
                            "-o", str(jf)], capture_output=True)
        try:
            data = json.loads(jf.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        execd = {}
        for fpath, info in data.get("files", {}).items():
            # normalise to the repo-relative path stitchgraph uses (…/src/stitchgraph/X -> X)
            p = fpath.replace("\\", "/")
            if "stitchgraph/" in p:
                p = p.split("stitchgraph/", 1)[1]
            execd[p] = set(info.get("executed_lines", []))
        snaps[mod] = execd
        print(f"  ran {mod}: {sum(len(v) for v in execd.values())} executed lines in "
              f"{len(execd)} files")
    return snaps


def main():
    print("running test modules under coverage (one pytest run each — slow)...")
    snaps = run_coverage()
    if len(snaps) < 3:
        print("too few coverage snapshots; aborting")
        return 1

    with sg.Store(":memory:") as store:
        sg.reindex(store, "src/stitchgraph")
        nodes = [n for n in store.all_nodes_full()
                 if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD) and n.location and n.end_line]
    # node -> (relpath, start, end)
    spans = []
    for n in nodes:
        loc = n.location.rsplit(":", 2)
        if len(loc) == 3:
            spans.append((n.id, loc[0], int(loc[1]), n.end_line))

    def fired(execd, relpath, start, end):
        lines = execd.get(relpath) or next((v for k, v in execd.items() if k.endswith(relpath)), None)
        if not lines:
            return False
        return any(start <= ln <= end for ln in lines)

    mods = list(snaps)
    M = np.zeros((len(mods), len(spans)))
    for mi, mod in enumerate(mods):
        for ni, (_id, rp, s, e) in enumerate(spans):
            if fired(snaps[mod], rp, s, e):
                M[mi, ni] = 1.0
    active = M.sum(0) > 0
    M = M[:, active]
    active_spans = [spans[i] for i in range(len(spans)) if active[i]]
    print(f"\nsnapshot matrix: {M.shape[0]} test-modules × {M.shape[1]} activated functions")

    # POD = SVD of the column-centred snapshot matrix
    Mc = M - M.mean(0, keepdims=True)
    U, S, Vt = np.linalg.svd(Mc, full_matrices=False)
    energy = (S ** 2) / (S ** 2).sum()
    print(f"POD singular values: {np.round(S, 2).tolist()}")
    print(f"energy per mode: {np.round(energy, 3).tolist()}")

    def _dir(nid):
        parts = nid.split("::", 1)[0].split("/")
        return "/".join(parts[:-1]) or "(root)"

    for mode in range(min(4, len(S))):
        load = Vt[mode, :]
        # which test-modules define this mode (left vector), which functions load it (right vector)
        mod_load = U[:, mode]
        top_mods = [mods[i].replace("test_", "").replace(".py", "")
                    for i in np.argsort(-np.abs(mod_load))[:3]]
        top_fns = np.argsort(-np.abs(load))[:8]
        dirs = collections.Counter(_dir(active_spans[i][0]) for i in top_fns)
        names = [active_spans[i][0].split("::")[-1] for i in top_fns[:6]]
        print(f"\n  POD mode {mode} (energy {energy[mode]:.1%}) — test-modules: {top_mods}")
        print(f"    dominant dirs: {dict(dirs)}")
        print(f"    co-activating fns: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
