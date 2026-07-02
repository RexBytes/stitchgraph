"""Tests for behavioural-mode analysis (`find_modes`, POD over runtime coverage) and the
`scaffold_coverage` capture-kit generator (design §6 win 3). Advisory, read-only; stitchgraph never
executes code — `find_modes` reads an inert per-test coverage artifact, `scaffold_coverage` only
writes helper files. numpy is a hard test dep via [dev]."""
from __future__ import annotations

import json
import os

import pytest

import stitchgraph as sg
from stitchgraph.core.model import Node, NodeKind

pytest.importorskip("numpy")


def _artifact(tmp_path):
    """Two planted behavioural groups (parse/lex/tokenize vs render/emit/format) + a bridge test."""
    tests = {}
    for i in range(6):
        tests[f"tests/test_parse.py::test_{i}"] = ["m.py::parse", "m.py::lex", "m.py::tokenize"]
    for i in range(6):
        tests[f"tests/test_render.py::test_{i}"] = ["m.py::render", "m.py::emit", "m.py::format"]
    tests["tests/test_e2e.py::test_full"] = ["m.py::parse", "m.py::render"]
    p = tmp_path / "coverage_modes.json"
    p.write_text(json.dumps({"format": "stitchgraph-coverage-v1", "tests": tests}))
    return str(p)


def test_find_modes_recovers_planted_behaviour(tmp_path):
    r = sg.find_modes(sg.Store(":memory:"), _artifact(tmp_path))
    assert r.ok and r.provenance.value == "extracted"
    assert r.meta["tests"] == 13 and r.meta["functions"] == 6
    # two planted groups → low intrinsic dimensionality
    assert 1 <= r.result["intrinsic_dimensionality"] <= 3
    assert r.result["modes"] and all("label" in m and "functions" in m for m in r.result["modes"])


def test_minimal_test_set_actually_covers_everything(tmp_path):
    art = _artifact(tmp_path)
    r = sg.find_modes(sg.Store(":memory:"), art)
    cov = json.load(open(art))["tests"]
    all_funcs = {f for fs in cov.values() for f in fs}
    chosen_funcs = {f for t in r.result["minimal_test_set"] for f in cov[t]}
    assert chosen_funcs == all_funcs                      # the reported set really is a cover
    assert r.result["minimal_test_count"] < len(cov)      # and it's smaller than the whole suite
    assert r.result["redundant_test_pairs"] > 0           # the 6+6 identical rows are redundant


def test_find_modes_bad_input_refuses(tmp_path):
    st = sg.Store(":memory:")
    assert not sg.find_modes(st, "/does/not/exist.json").ok
    assert not sg.find_modes(st, 123).ok                  # type: ignore[arg-type]
    empty = tmp_path / "e.json"
    empty.write_text('{"format":"stitchgraph-coverage-v1","tests":{}}')
    assert not sg.find_modes(st, str(empty)).ok           # <4 tests → refuse
    junk = tmp_path / "j.json"
    junk.write_text("not json")
    assert not sg.find_modes(st, str(junk)).ok


def test_find_modes_is_deterministic(tmp_path):
    art = _artifact(tmp_path)
    a = sg.find_modes(sg.Store(":memory:"), art).result
    b = sg.find_modes(sg.Store(":memory:"), art).result
    assert [m["functions"] for m in a["modes"]] == [m["functions"] for m in b["modes"]]
    assert a["minimal_test_count"] == b["minimal_test_count"]


def test_large_suite_few_functions_never_ooms(tmp_path):
    """Panel R271 MEDIUM: many tests but few functions must NOT allocate an O(n_tests^2) matrix and
    must never raise — it returns a clean Result. (n far below the min-dim cap, but n^2 is huge.)"""
    tests = {f"t.py::test_{i}": ["m.py::a", "m.py::b", "m.py::c", "m.py::d"] for i in range(6000)}
    p = tmp_path / "big.json"
    p.write_text(json.dumps({"format": "stitchgraph-coverage-v1", "tests": tests}))
    r = sg.find_modes(sg.Store(":memory:"), str(p))
    assert r.ok                                            # no MemoryError, no raise
    assert r.result["minimal_test_count"] == 1            # any single test covers all 4 funcs
    assert r.result["redundant_test_pairs"] == 6000 * 5999 // 2   # all identical profiles


def test_intrinsic_dimensionality_never_exceeds_modes(tmp_path):
    """Panel R272 MEDIUM: with zero behavioural variance (every test hits the same functions) the
    mean-centred matrix is all-zero, so intrinsic dimensionality must be 0 — and in general it must
    never exceed the number of modes actually computed."""
    tests = {f"t.py::test_{i}": ["m.py::a", "m.py::b", "m.py::c", "m.py::d"] for i in range(6)}
    p = tmp_path / "flat.json"
    p.write_text(json.dumps({"format": "stitchgraph-coverage-v1", "tests": tests}))
    r = sg.find_modes(sg.Store(":memory:"), str(p))
    assert r.ok
    assert r.result["intrinsic_dimensionality"] == 0            # no behavioural variance
    assert r.result["intrinsic_dimensionality"] <= len(r.result["modes"])
    # and the invariant holds on the planted-behaviour artifact too
    r2 = sg.find_modes(sg.Store(":memory:"), _artifact(tmp_path))
    assert r2.result["intrinsic_dimensionality"] <= len(r2.result["modes"])
    # defense-in-depth: even a direct decompose(k=1) (unreachable via the public API) keeps
    # intrinsic_dimensionality <= number of modes (panel R273)
    from stitchgraph.core import modes as _m
    payload, _ = _m.decompose(sg.Store(":memory:"), _artifact(tmp_path), k=1)
    assert payload["intrinsic_dimensionality"] <= len(payload["modes"])


def test_minimal_test_set_is_a_full_cover_even_when_large(tmp_path):
    """Panel R271 LOW: the reported minimal_test_set must actually cover everything (no silent
    truncation) — 500 tests each hitting a unique function need all 500."""
    tests = {f"t.py::test_{i}": [f"m.py::f{i}"] for i in range(500)}
    p = tmp_path / "uniq.json"
    p.write_text(json.dumps({"format": "stitchgraph-coverage-v1", "tests": tests}))
    r = sg.find_modes(sg.Store(":memory:"), str(p))
    assert r.result["minimal_test_count"] == 500
    assert len(r.result["minimal_test_set"]) == 500       # not truncated
    covered = {f for t in r.result["minimal_test_set"] for f in tests[t]}
    assert len(covered) == 500                            # genuinely a full cover


def test_scaffold_writes_a_sandboxed_kit(tmp_path):
    st = sg.Store(":memory:")
    st.add_node(Node(id="m.py::parse", kind=NodeKind.FUNCTION, name="parse", location="m.py:1:0"))
    st.commit()
    out = tmp_path / "kit"
    r = sg.scaffold_coverage(st, out_dir=str(out))
    assert r.ok and "python" in r.meta["languages"]
    names = {os.path.basename(p) for p in r.result["files"]}
    assert {"Dockerfile", "docker-compose.yml", "run_coverage.sh", "README.md",
            "to_canonical.py"} <= names
    # the docker recipe must be network-less (safety) and the README must name all three options
    compose = (out / "docker-compose.yml").read_text()
    assert 'network_mode: "none"' in compose and "cap_drop" in compose
    readme = (out / "README.md").read_text()
    assert "Docker" in readme and "shell" in readme and "CI" in readme
    assert "stitchgraph-coverage-v1" in readme


def test_converter_qualifies_method_names(tmp_path):
    """Panel R274 HIGH: the shipped Python converter must emit qualified ids (Class.method,
    outer.inner) matching stitchgraph node ids — never bare names that collapse distinct
    same-named methods (A.run and B.run) into one node."""
    import ast as _ast

    from stitchgraph.core.coverage_scaffold import _PY_CONVERTER
    ns = {"ast": _ast, "os": os}
    src = _PY_CONVERTER
    seg = src[src.index("def func_ranges"):src.index("cov = coverage.Coverage")]
    exec(seg, ns)  # noqa: S102 — exercising the shipped converter's function-mapping logic
    sample = tmp_path / "s.py"
    sample.write_text(
        "class A:\n    def run(self):\n        return 1\n"
        "class B:\n    def run(self):\n        return 2\n"
        "def top():\n    def inner():\n        return 3\n    return inner\n")
    names = {r[0] for r in ns["func_ranges"](str(sample))}
    assert "A.run" in names and "B.run" in names   # distinct methods stay distinct
    assert "top" in names and "top.inner" in names  # nested qualified
    assert "run" not in names                       # bare method name never emitted


def test_scaffold_bad_input_refuses():
    st = sg.Store(":memory:")
    assert not sg.scaffold_coverage(st, out_dir="").ok
    assert not sg.scaffold_coverage(st, language=123).ok  # type: ignore[arg-type]


def test_cardinal_no_eager_import_and_liveness_untouched(tmp_path):
    import subprocess
    import sys
    # import stitchgraph must not eagerly pull in modes/coverage_scaffold/numpy
    code = ("import sys, stitchgraph; "
            "assert 'stitchgraph.core.modes' not in sys.modules; "
            "assert 'stitchgraph.core.coverage_scaffold' not in sys.modules; "
            "print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr

    # find_modes / scaffold_coverage never change find_stale (they don't touch the graph)
    d = tmp_path / "proj"
    d.mkdir()
    (d / "m.py").write_text("def a():\n    return b()\n\ndef b():\n    return 1\n\ndef dead():\n    return 0\n")
    store = sg.Store(":memory:")
    sg.reindex(store, str(d))
    before = sg.find_stale(store).result
    sg.find_modes(store, _artifact(tmp_path))
    sg.scaffold_coverage(store, out_dir=str(tmp_path / "kit2"))
    after = sg.find_stale(store).result
    assert before == after
