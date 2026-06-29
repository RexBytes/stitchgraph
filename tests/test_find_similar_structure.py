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
