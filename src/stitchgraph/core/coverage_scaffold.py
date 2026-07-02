"""Generate a sandboxed per-test-coverage capture kit (design §6 win 3 support). `find_modes` needs a
per-test coverage artifact, and producing it means *running the project's test suite* — which executes
arbitrary code. stitchgraph must never do that (cardinal read-only). Instead this **generates a recipe**
the user (or their agent) runs **in their own jail**, emitting the canonical artifact
`{"format": "stitchgraph-coverage-v1", "tests": {"<test id>": ["<function id>", ...]}}`.

Per detected language it writes three interchangeable options — pick whatever fits human or LLM:
  1. **Docker** (Dockerfile + docker-compose.yml): fully isolated (no network, non-root, tmpfs, capped).
  2. **shell** (run_coverage.sh): for when you already have a sandbox / container / CI runner.
  3. **CI** snippet (in README): drop into GitHub Actions.
Only the inert JSON matrix leaves the sandbox; `find_modes` then runs pure math on it.

Python is turnkey (coverage.py `--cov-context=test` + a converter that maps covered lines→functions via
AST). JS / Go / Rust / Java ship the right coverage tool wired plus a converter skeleton and the exact
canonical spec, so the last mile is a short, well-specified step.
"""
from __future__ import annotations

import os
from typing import Any

from .store import Store

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "javascript",
    ".tsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go", ".rs": "rust", ".java": "java",
}


def detect_languages(store: Store) -> list[str]:
    """Languages present in the index, by file extension of the node ids (most common first)."""
    counts: dict[str, int] = {}
    for nid in store.all_node_ids():
        path = nid.split("::", 1)[0]
        ext = os.path.splitext(path)[1].lower()
        lang = _EXT_LANG.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return [lang for lang, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


# --- the Python converter: native .coverage contexts -> canonical JSON (turnkey) ---
_PY_CONVERTER = r'''#!/usr/bin/env python3
"""Convert coverage.py per-test contexts (.coverage) into the canonical stitchgraph artifact.
Runs INSIDE the sandbox after the suite. Maps covered lines -> functions via AST. No stitchgraph needed."""
import ast, os, json, sys
import coverage

SRC = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = sys.argv[2] if len(sys.argv) > 2 else "coverage_modes.json"

def func_ranges(path):
    try: tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception: return []
    return [(n.name, n.lineno, n.end_lineno)
            for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

cov = coverage.Coverage(); cov.load(); data = cov.get_data()
tests = {}
for f in data.measured_files():
    if not f.endswith(".py"): continue
    rel = os.path.relpath(f, SRC)      # always relative (SRC='.' → relative to cwd) for clean ids
    ranges = func_ranges(f)
    try: cbl = data.contexts_by_lineno(f)
    except Exception: continue
    for ln, ctxs in cbl.items():
        for c in ctxs:
            if "::" not in c: continue          # keep real pytest test ids only
            for (name, lo, hi) in ranges:
                if lo <= ln <= (hi or lo):
                    tests.setdefault(c, set()).add(f"{rel}::{name}")
out = {"format": "stitchgraph-coverage-v1",
       "tests": {t: sorted(fs) for t, fs in tests.items()}}
json.dump(out, open(OUT, "w"), indent=0)
print(f"wrote {OUT}: {len(out['tests'])} tests, "
      f"{len({x for fs in out['tests'].values() for x in fs})} functions")
'''

_PY_RUN = r'''#!/usr/bin/env bash
# Run the test suite under per-test coverage, then emit the canonical artifact.
# Intended to run inside the sandbox (Docker service below, or your own jail/CI).
set -euo pipefail
pip install --quiet coverage pytest 2>/dev/null || true
# --cov-context=test tags coverage by the running test id
python -m pytest -p no:cacheprovider -q \
  --cov=. --cov-context=test || true      # keep going even if some tests fail
python to_canonical.py . coverage_modes.json
echo "artifact ready: coverage_modes.json  (copy it out; run: stitchgraph find-modes coverage_modes.json)"
'''

_DOCKERFILE = """# Sandboxed per-test coverage capture. Build/run this in YOUR environment; it executes the
# project's tests in an isolated, non-root, network-less container. Only coverage_modes.json leaves.
FROM {base}
WORKDIR /work
# copy the project in read-only spirit; the container is disposable
COPY . /work
RUN useradd -m runner || true
USER runner
{deps}
CMD ["bash", "run_coverage.sh"]
"""

_COMPOSE = """# `docker compose run --rm coverage` — isolated: no network, read-only rootfs, tmpfs work area,
# dropped caps, memory/pid limits. Emits ./out/coverage_modes.json.
services:
  coverage:
    build: .
    network_mode: "none"          # no outbound network while running untrusted tests
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 512
    mem_limit: 2g
    volumes:
      - ./out:/work/out           # the ONLY thing that comes back out
    command: bash -lc "bash run_coverage.sh && cp coverage_modes.json out/"
"""

_LANG: dict[str, dict[str, Any]] = {
    "python": {"base": "python:3.12-slim",
               "deps": "RUN pip install --no-cache-dir coverage pytest",
               "tool": "coverage.py (`pytest --cov --cov-context=test`)",
               "run": _PY_RUN, "converter": ("to_canonical.py", _PY_CONVERTER)},
    "javascript": {"base": "node:22-slim",
                   "deps": "RUN npm i -g c8 2>/dev/null || true",
                   "tool": "c8 / nyc (per-test contexts: run tests with a reporter that tags by test)",
                   "run": None, "converter": None},
    "go": {"base": "golang:1.24",
           "deps": "", "tool": "`go test -coverprofile` (per-test: run `go test -run <Test>` per test)",
           "run": None, "converter": None},
    "rust": {"base": "rust:1", "deps": "RUN cargo install cargo-llvm-cov 2>/dev/null || true",
             "tool": "cargo-llvm-cov (per-test: `cargo llvm-cov --json` per `--test`)",
             "run": None, "converter": None},
    "java": {"base": "eclipse-temurin:21", "deps": "",
             "tool": "JaCoCo (per-test: the jacoco agent with a per-test session id)",
             "run": None, "converter": None},
}


def _readme(lang: str, info: dict[str, Any]) -> str:
    turnkey = info["run"] is not None
    return f"""# stitchgraph per-test coverage kit — {lang}

`find_modes` needs a **per-test coverage artifact**: which test executed which function. Producing it
means *running this project's test suite*, which runs arbitrary code — so do it in a **sandbox**, never
on your host. This kit gives three interchangeable ways to produce the canonical artifact
`coverage_modes.json`. stitchgraph did NOT run anything to make this kit — you run it, jailed.

Coverage tool for {lang}: **{info['tool']}**

## Option 1 — Docker (most isolated; recommended)
```
docker compose run --rm coverage        # no network, non-root, read-only rootfs, capped
# → ./out/coverage_modes.json
```

## Option 2 — plain shell (if you already have a sandbox / CI runner / devcontainer)
```
bash run_coverage.sh                     # → coverage_modes.json
```

## Option 3 — CI (GitHub Actions)
```yaml
- run: bash run_coverage.sh
- uses: actions/upload-artifact@v4
  with: {{ name: coverage_modes, path: coverage_modes.json }}
```

## Then, back on your machine (no code execution — pure math):
```
stitchgraph find-modes coverage_modes.json      # behavioural modes, dimensionality, minimal test set
```

## Canonical artifact format (`stitchgraph-coverage-v1`)
```json
{{
  "format": "stitchgraph-coverage-v1",
  "tests": {{
    "tests/test_x.py::test_a": ["src/pkg/mod.py::func1", "src/pkg/mod.py::Class.method"],
    "...": ["..."]
  }}
}}
```
Keys = test ids; values = the functions that test executed (ids like `path::qualified.name`, matching
stitchgraph's node ids so `find_modes` can label modes by module). Any per-test coverage tool that can
emit this works.
""" + ("" if turnkey else f"""
## NOTE — {lang} is a TEMPLATE, not turnkey
Python ships a complete converter; for {lang}, wire `{info['tool']}` in `run_coverage.sh` to run the
suite with per-test attribution, then convert its output to the canonical format above (map covered
lines → enclosing functions). It's a short, well-specified step — an LLM agent can complete it from
this spec.
""")


def generate(store: Store, out_dir: str, language: str | None = None) -> dict[str, Any]:
    """Write the capture kit(s) into `out_dir`. Returns a manifest {languages, files, out_dir}.
    Writes helper files only (like `report`) — never touches project source, never executes."""
    langs = [language] if language else detect_languages(store)
    langs = [x for x in langs if x in _LANG] or ["python"]
    written: list[str] = []
    for lang in langs:
        info = _LANG[lang]
        d = os.path.join(out_dir, lang) if len(langs) > 1 else out_dir
        os.makedirs(os.path.join(d, "out"), exist_ok=True)
        files = {
            "Dockerfile": _DOCKERFILE.format(base=info["base"], deps=info["deps"]),
            "docker-compose.yml": _COMPOSE,
            "run_coverage.sh": info["run"] or _PY_RUN.replace("python -m pytest", "# TODO: run "
                               + info["tool"]),
            "README.md": _readme(lang, info),
        }
        if info["converter"]:
            cname, ctext = info["converter"]
            files[cname] = ctext
        for name, text in files.items():
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            written.append(p)
    return {"languages": langs, "files": written, "out_dir": out_dir,
            "turnkey": [x for x in langs if _LANG[x]["run"] is not None]}
