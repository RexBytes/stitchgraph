#!/usr/bin/env python3
"""Held-out acceptance test for the circuit-simulator build experiment.

Usage: python acceptance.py <project_dir>
Runs `python run.py <netlist>` for each hidden case, parses the JSON on stdout, checks the
node voltages against analytic answers. Prints a PASS/FAIL report and exits 0 iff ALL pass.
The circuits here are DIFFERENT from the worked examples in SPEC.md.
"""
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = [
    {
        "name": "divider_3k_1k",
        "netlist": "V1 in 0 10\nR1 in out 3000\nR2 out 0 1000\n.op\n",
        "expect": {"in": 10.0, "out": 2.5},   # 10 * 1k/4k
        "tol": 1e-3,
    },
    {
        "name": "series_3R",
        "netlist": "V1 a 0 9\nR1 a b 1000\nR2 b c 2000\nR3 c 0 3000\n.op\n",
        "expect": {"a": 9.0, "b": 7.5, "c": 4.5},  # I=1.5mA
        "tol": 1e-3,
    },
    {
        "name": "rc_2ms_tau",
        "netlist": "V1 in 0 5\nR1 in n 2000\nC1 n 0 1e-6\n.tran 1e-6 2e-3\n",
        "expect": {"n": 5.0 * (1.0 - math.exp(-1.0))},  # RC=2e-3, t=2e-3 => 1 tau => 3.1606
        "tol": 0.05,
    },
]


def _extract_json(stdout: str):
    # tolerate stray output: try whole thing, then the last brace-balanced line/blob
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
    # last resort: from first '{' to last '}'
    i, j = stdout.find("{"), stdout.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(stdout[i:j + 1])
        except Exception:
            return None
    return None


def run_case(project: Path, case: dict) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".ckt", delete=False) as f:
        f.write(case["netlist"])
        netlist = f.name
    try:
        proc = subprocess.run(
            [sys.executable, "run.py", netlist],
            cwd=project, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    if proc.returncode != 0:
        return False, f"exit {proc.returncode}; stderr tail: {proc.stderr.strip()[-300:]}"
    data = _extract_json(proc.stdout)
    if not isinstance(data, dict) or "nodes" not in data or not isinstance(data["nodes"], dict):
        return False, f"bad/missing JSON on stdout: {proc.stdout.strip()[:300]!r}"
    nodes = data["nodes"]
    diffs = []
    ok = True
    for name, want in case["expect"].items():
        got = nodes.get(name)
        if got is None:
            ok = False
            diffs.append(f"{name}: MISSING")
            continue
        try:
            got = float(got)
        except Exception:
            ok = False
            diffs.append(f"{name}: non-numeric {got!r}")
            continue
        d = abs(got - want)
        mark = "ok" if d <= case["tol"] else "OFF"
        if d > case["tol"]:
            ok = False
        diffs.append(f"{name}: got {got:.5g} want {want:.5g} (Δ{d:.2g} {mark})")
    return ok, "; ".join(diffs)


def main():
    if len(sys.argv) != 2:
        print("usage: python acceptance.py <project_dir>")
        sys.exit(2)
    project = Path(sys.argv[1]).resolve()
    if not (project / "run.py").exists():
        print(f"FAIL: no run.py in {project}")
        sys.exit(1)
    all_ok = True
    for case in CASES:
        ok, detail = run_case(project, case)
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['name']}: {detail}")
    print("-" * 60)
    print(f"RESULT: {'ALL PASS' if all_ok else 'FAIL'}  ({project})")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
