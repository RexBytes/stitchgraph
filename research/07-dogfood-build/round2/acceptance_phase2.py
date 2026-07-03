#!/usr/bin/env python3
"""Held-out grader for the multi-session (phase-2) experiment.

Usage: python acceptance_phase2.py <project_dir>
Cases 1-5 are REGRESSION (must keep working after the change); cases 6-7 are the NEW VCVS behavior.
On the frozen base, 1-5 should PASS and 6-7 FAIL (no VCVS yet); on a correctly-extended project all
7 PASS. Prints per-case PASS/FAIL and a summary line. Exit 0 iff all 7 pass.
"""
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

TAU = 1.0  # marker only

CASES = [
    # --- regression (pre-existing behavior) ---
    {"name": "op_divider",  "kind": "op",
     "netlist": "V1 in 0 10\nR1 in out 3000\nR2 out 0 1000\n.op\n",
     "expect": {"in": 10.0, "out": 2.5}, "tol": 1e-3, "group": "regression"},
    {"name": "op_series3",  "kind": "op",
     "netlist": "V1 a 0 9\nR1 a b 1000\nR2 b c 2000\nR3 c 0 3000\n.op\n",
     "expect": {"a": 9.0, "b": 7.5, "c": 4.5}, "tol": 1e-3, "group": "regression"},
    {"name": "tran_rc",     "kind": "tran",
     "netlist": "V1 in 0 5\nR1 in n 2000\nC1 n 0 1e-6\n.tran 1e-6 2e-3\n",
     "expect": {"n": 5.0 * (1.0 - math.exp(-1.0))}, "tol": 0.05, "group": "regression"},
    {"name": "tran_rl",     "kind": "tran",
     # tau = L/R = 0.1/100 = 1e-3; at t=1e-3 the inductor node voltage = V*e^-1 across L
     "netlist": "V1 in 0 1\nR1 in n 100\nL1 n 0 0.1\n.tran 1e-6 1e-3\n",
     "expect": {"n": 1.0 * math.exp(-1.0)}, "tol": 0.03, "group": "regression"},
    {"name": "dc_sweep",    "kind": "dc",
     "netlist": "V1 in 0 0\nR1 in out 2000\nR2 out 0 2000\n.dc V1 0 8 4\n",
     "expect_sweep": {0.0: {"out": 0.0}, 4.0: {"out": 2.0}, 8.0: {"out": 4.0}},
     "tol": 1e-3, "group": "regression"},
    # --- new VCVS behavior ---
    {"name": "vcvs_gain",   "kind": "op",
     "netlist": "V1 in 0 2\nE1 out 0 in 0 3\nR1 out 0 1000\n.op\n",
     "expect": {"in": 2.0, "out": 6.0}, "tol": 1e-3, "group": "vcvs"},
    {"name": "vcvs_loaded", "kind": "op",
     "netlist": "V1 in 0 2\nE1 amp 0 in 0 3\nR1 amp mid 1000\nR2 mid 0 1000\n.op\n",
     "expect": {"in": 2.0, "amp": 6.0, "mid": 3.0}, "tol": 1e-3, "group": "vcvs"},
]


def _extract_json(stdout: str):
    try:
        return json.loads(stdout)
    except Exception:
        pass
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    i, j = stdout.find("{"), stdout.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(stdout[i:j + 1])
        except Exception:
            return None
    return None


def _run(project: Path, netlist: str):
    with tempfile.NamedTemporaryFile("w", suffix=".ckt", delete=False) as f:
        f.write(netlist)
        path = f.name
    try:
        p = subprocess.run([sys.executable, "run.py", path], cwd=project,
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT"
    if p.returncode != 0:
        return None, f"exit {p.returncode}; stderr: {p.stderr.strip()[-200:]}"
    data = _extract_json(p.stdout)
    if data is None:
        return None, f"no JSON: {p.stdout.strip()[:200]!r}"
    return data, ""


def _check_nodes(nodes, expect, tol):
    diffs, ok = [], True
    for name, want in expect.items():
        got = nodes.get(name)
        try:
            got = float(got)
        except Exception:
            ok = False
            diffs.append(f"{name}:MISSING/NaN")
            continue
        d = abs(got - want)
        if d > tol:
            ok = False
        diffs.append(f"{name}={got:.5g}(want {want:.5g})")
    return ok, ", ".join(diffs)


def run_case(project, case):
    data, err = _run(project, case["netlist"])
    if data is None:
        return False, err
    if case["kind"] in ("op", "tran"):
        nodes = data.get("nodes")
        if not isinstance(nodes, dict):
            return False, f"no nodes dict: {str(data)[:150]}"
        return _check_nodes(nodes, case["expect"], case["tol"])
    if case["kind"] == "dc":
        sweep = data.get("sweep")
        if not isinstance(sweep, list):
            return False, f"no sweep list: {str(data)[:150]}"
        okall, parts = True, []
        for val, exp in case["expect_sweep"].items():
            pt = min(sweep, key=lambda s: abs(float(s.get("value", 1e9)) - val), default=None)
            if pt is None or abs(float(pt.get("value", 1e9)) - val) > 1e-6:
                okall = False
                parts.append(f"@{val}:missing")
                continue
            ok, d = _check_nodes(pt.get("nodes", {}), exp, case["tol"])
            okall = okall and ok
            parts.append(f"@{val}[{d}]")
        return okall, "; ".join(parts)
    return False, "unknown kind"


def main():
    project = Path(sys.argv[1]).resolve()
    if not (project / "run.py").exists():
        print(f"FAIL: no run.py in {project}")
        sys.exit(1)
    passed = {"regression": 0, "vcvs": 0}
    total = {"regression": 0, "vcvs": 0}
    all_ok = True
    for case in CASES:
        ok, detail = run_case(project, case)
        total[case["group"]] += 1
        passed[case["group"]] += 1 if ok else 0
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] ({case['group']}) {case['name']}: {detail}")
    print("-" * 62)
    print(f"regression: {passed['regression']}/{total['regression']}   "
          f"vcvs: {passed['vcvs']}/{total['vcvs']}   "
          f"OVERALL: {'ALL PASS' if all_ok else 'not all pass'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
