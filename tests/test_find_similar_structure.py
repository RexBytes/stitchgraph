"""find_similar(mode="structure") — body-shape retrieval over an indexed repo (v3.0.0 phase 2).

Stdlib-only (no tree-sitter / extras needed for a pure-Python fixture), so it runs in the
core (no-extras) CI job.
"""
from __future__ import annotations

from pathlib import Path

import stitchgraph as sg


def _index(tmp_path: Path, files: dict[str, str]):
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    return store


REPO = {
    "acc.py": (
        "def sum_even_squares(items):\n"
        "    total = 0\n"
        "    for x in items:\n"
        "        if x % 2 == 0:\n"
        "            total = total + x * x\n"
        "    return total\n\n"
        "def parse_csv(line):\n"
        "    out = []\n"
        "    for field in line.split(','):\n"
        "        out.append(field.strip())\n"
        "    return out\n"
    ),
}

# Same shape as sum_even_squares, different names/literals — the structural clone.
QUERY = (
    "def accumulate_even(data):\n"
    "    acc = 0\n"
    "    for v in data:\n"
    "        if v % 3 == 0:\n"
    "            acc = acc + v * v\n"
    "    return acc\n"
)


def test_structure_mode_ranks_the_clone_first(tmp_path):
    store = _index(tmp_path, REPO)
    res = sg.find_similar(store, QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids, "expected at least one structural match"
    # the accumulator clone must rank above the csv-parser
    assert ids[0].endswith("::sum_even_squares")
    top = res.result[0]["score"]
    csv = next((r["score"] for r in res.result if r["id"].endswith("::parse_csv")), 0.0)
    assert top > csv


def test_semantic_mode_unchanged_default(tmp_path):
    store = _index(tmp_path, REPO)
    # default mode still works (token similarity on the name)
    res = sg.find_similar(store, "sum of even squares accumulator")
    assert res.ok


def test_structure_mode_rejects_non_python_snippet(tmp_path):
    store = _index(tmp_path, REPO)
    res = sg.find_similar(store, "this is not code", mode="structure")
    assert not res.ok
    assert "python" in " ".join(res.review_reasons).lower()


def test_bad_mode_refused(tmp_path):
    store = _index(tmp_path, REPO)
    res = sg.find_similar(store, QUERY, mode="nonsense")
    assert not res.ok
    assert "mode" in " ".join(res.review_reasons).lower()


# --- JS/TS body matrix (v3.2.0) — needs the tree-sitter extra ---------------------------------

_JS_REPO = {
    "acc.js": (
        "function sumEvenSquares(items){\n"
        "  let total = 0;\n"
        "  for (const x of items){\n"
        "    if (x % 2 === 0){ total = total + x * x; }\n"
        "  }\n"
        "  return total;\n"
        "}\n"
        "function parseCsv(line){\n"
        "  const out = [];\n"
        "  for (const field of line.split(',')){ out.push(field.trim()); }\n"
        "  return out;\n"
        "}\n"
    ),
}
# same body shape as sumEvenSquares, renamed + an arrow + different literals — the JS clone.
_JS_QUERY = (
    "const accumulateEven = (data) => {\n"
    "  let acc = 0;\n"
    "  for (const v of data){\n"
    "    if (v % 3 === 0){ acc = acc + v * v; }\n"
    "  }\n"
    "  return acc;\n"
    "};\n"
)


def test_js_structure_mode_ranks_the_clone_first(tmp_path):
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    store = _index(tmp_path, _JS_REPO)
    res = sg.find_similar(store, _JS_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids[0].endswith("::sumEvenSquares")    # the accumulator clone, not parseCsv
    top = res.result[0]["score"]
    csv = next((r["score"] for r in res.result if r["id"].endswith("::parseCsv")), 0.0)
    assert top > csv


def test_structure_mode_ranks_same_language_only(tmp_path):
    # A JS snippet must rank JS functions, NOT a same-shaped Python one (cross-language body scores
    # aren't comparable — extractor-dependent). Index BOTH languages, query with JS.
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    files = dict(_JS_REPO)
    files.update(REPO)   # adds acc.py with sum_even_squares (the Python accumulator)
    store = _index(tmp_path, files)
    res = sg.find_similar(store, _JS_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids and all("::" in i for i in ids), ids
    # every result's file is JS-family, never the Python accumulator
    assert all(not i.split("::", 1)[0].endswith(".py") for i in ids), ids
    assert any(i.endswith("::sumEvenSquares") for i in ids)


_GO_REPO = {
    "acc.go": (
        "package m\n"
        "func SumEvenSquares(items []int) int {\n"
        "    total := 0\n"
        "    for _, x := range items {\n"
        "        if x%2 == 0 { total = total + x*x }\n"
        "    }\n"
        "    return total\n"
        "}\n"
        "func ParseCSV(line string) []string {\n"
        "    out := []string{}\n"
        "    for _, f := range split(line) { out = append(out, trim(f)) }\n"
        "    return out\n"
        "}\n"
    ),
}
# same body shape as SumEvenSquares, renamed + different literals — the Go clone.
_GO_QUERY = (
    "package m\n"
    "func accumulate(data []int) int {\n"
    "    acc := 0\n"
    "    for _, v := range data {\n"
    "        if v%3 == 0 { acc = acc + v*v }\n"
    "    }\n"
    "    return acc\n"
    "}\n"
)


def test_go_structure_mode_ranks_the_clone_first(tmp_path):
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    store = _index(tmp_path, _GO_REPO)
    res = sg.find_similar(store, _GO_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids[0].endswith("::SumEvenSquares")    # the accumulator clone, not ParseCSV
    top = res.result[0]["score"]
    csv = next((r["score"] for r in res.result if r["id"].endswith("::ParseCSV")), 0.0)
    assert top > csv


def test_go_structure_mode_ranks_same_language_only(tmp_path):
    # A Go snippet must rank Go functions, NOT a same-shaped Python one. Index both; query with Go.
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    files = dict(_GO_REPO)
    files.update(REPO)   # adds acc.py with sum_even_squares (the Python accumulator)
    store = _index(tmp_path, files)
    res = sg.find_similar(store, _GO_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids and all("::" in i for i in ids), ids
    assert all(not i.split("::", 1)[0].endswith(".py") for i in ids), ids
    assert any(i.endswith("::SumEvenSquares") for i in ids)


_RUST_REPO = {
    "acc.rs": (
        "fn sum_even_squares(items: &[i32]) -> i32 {\n"
        "    let mut total = 0;\n"
        "    for x in items { if x % 2 == 0 { total += x * x } }\n"
        "    total\n"
        "}\n"
        "fn parse_csv(line: &str) -> Vec<String> {\n"
        "    let mut out = Vec::new();\n"
        "    for f in line.split(',') { out.push(f.trim().to_string()) }\n"
        "    out\n"
        "}\n"
    ),
}
# same body shape as sum_even_squares, renamed + trailing-expr return — the Rust clone.
_RUST_QUERY = (
    "fn accumulate(data: &[i32]) -> i32 {\n"
    "    let mut acc = 0;\n"
    "    for v in data { if v % 3 == 0 { acc += v * v } }\n"
    "    acc\n"
    "}\n"
)


def test_rust_structure_mode_ranks_the_clone_first(tmp_path):
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    store = _index(tmp_path, _RUST_REPO)
    res = sg.find_similar(store, _RUST_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids[0].endswith("::sum_even_squares")    # the accumulator clone, not parse_csv
    top = res.result[0]["score"]
    csv = next((r["score"] for r in res.result if r["id"].endswith("::parse_csv")), 0.0)
    assert top > csv


def test_rust_structure_mode_ranks_same_language_only(tmp_path):
    # A Rust snippet must rank Rust functions, NOT a same-shaped Python one. Index both; query Rust.
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    files = dict(_RUST_REPO)
    files.update(REPO)   # adds acc.py with sum_even_squares (the Python accumulator)
    store = _index(tmp_path, files)
    res = sg.find_similar(store, _RUST_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids and all("::" in i for i in ids), ids
    assert all(not i.split("::", 1)[0].endswith(".py") for i in ids), ids
    assert any(i.endswith("::sum_even_squares") for i in ids)


_CPP_REPO = {
    "acc.cpp": (
        "int sum_even_squares(int* items, int n) {\n"
        "    int total = 0;\n"
        "    for (int i = 0; i < n; i++) { if (items[i] % 2 == 0) { total += items[i] * items[i]; } }\n"
        "    return total;\n"
        "}\n"
        "int parse_count(const char* s) {\n"
        "    int c = 0;\n"
        "    while (*s) { if (*s == ',') { c++; } s++; }\n"
        "    return c;\n"
        "}\n"
    ),
}
# same body shape as sum_even_squares, renamed — the C/C++ clone.
_CPP_QUERY = (
    "int accumulate(int* data, int m) {\n"
    "    int acc = 0;\n"
    "    for (int j = 0; j < m; j++) { if (data[j] % 3 == 0) { acc += data[j] * data[j]; } }\n"
    "    return acc;\n"
    "}\n"
)


def test_cpp_structure_mode_ranks_the_clone_first(tmp_path):
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    store = _index(tmp_path, _CPP_REPO)
    res = sg.find_similar(store, _CPP_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids[0].endswith("::sum_even_squares")    # the accumulator clone, not parse_count
    top = res.result[0]["score"]
    cnt = next((r["score"] for r in res.result if r["id"].endswith("::parse_count")), 0.0)
    assert top > cnt


def test_cpp_structure_mode_ranks_same_language_only(tmp_path):
    # A C/C++ snippet must rank C/C++ functions, NOT a same-shaped Python one. Index both; query C++.
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    files = dict(_CPP_REPO)
    files.update(REPO)   # adds acc.py with sum_even_squares (the Python accumulator)
    store = _index(tmp_path, files)
    res = sg.find_similar(store, _CPP_QUERY, mode="structure")
    assert res.ok, res.review_reasons
    ids = [row["id"] for row in res.result]
    assert ids and all("::" in i for i in ids), ids
    assert all(not i.split("::", 1)[0].endswith(".py") for i in ids), ids
    assert any(i.endswith("::sum_even_squares") for i in ids)


# --- Java + C# body layer (v3.6.0): drive _java/_csharp_fn_fingerprints + graph_diff body --------

def test_java_graph_diff_body_layer_and_fingerprints(tmp_path):
    """The Java fingerprint corpus (`similar._java_fn_fingerprints`) keys by the stored node id, and
    `graph_diff`'s body layer flags a Java method whose body shape changed (helper() -> const)."""
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core import graphdiff, similar
    a = _index(tmp_path / "a", {
        "Calc.java": "class Calc { int add(int x, int y){ int s = compute(x) + y; return s; } }"})
    b = _index(tmp_path / "b", {
        "Calc.java": "class Calc { int add(int x, int y){ int s = 0 + y; return s; } }"})
    fps = dict(similar._java_fn_fingerprints(a))
    assert "Calc.java::Calc.add" in fps, fps  # keyed by the full node id, Java-only
    d = graphdiff.graph_diff(a, b, body=True)
    assert any(c["name"] == "Calc.add" for c in d["body_changed"]), d["body_changed"]
    # unchanged body must NOT be flagged (direction guard)
    same = graphdiff.graph_diff(a, a, body=True)
    assert same["body_changed"] == []


def test_csharp_graph_diff_body_layer_and_fingerprints(tmp_path):
    """The C# fingerprint corpus (`similar._csharp_fn_fingerprints`) keys by the stored node id
    (namespace excluded), and `graph_diff`'s body layer flags a changed C# method body."""
    import pytest
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core import graphdiff, similar
    a = _index(tmp_path / "a", {
        "Calc.cs": "namespace App { class Calc { int Add(int x){ return compute(x); } } }"})
    b = _index(tmp_path / "b", {
        "Calc.cs": "namespace App { class Calc { int Add(int x){ return 0; } } }"})
    fps = dict(similar._csharp_fn_fingerprints(a))
    assert "Calc.cs::Calc.Add" in fps, fps  # namespace App is not part of the key
    d = graphdiff.graph_diff(a, b, body=True)
    assert any(c["name"] == "Calc.Add" for c in d["body_changed"]), d["body_changed"]
    same = graphdiff.graph_diff(a, a, body=True)
    assert same["body_changed"] == []
