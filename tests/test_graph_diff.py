"""graph_diff — structural diff of two indexes (v3.0.0 phase 3).

Includes the differential oracle (an index diffed against itself is EQUIVALENT — no phantom
deltas) plus located-delta and body-aware cases. Stdlib-only Python fixtures, so this runs in the
core (no-extras) CI job.
"""
from __future__ import annotations

from pathlib import Path

import stitchgraph as sg
from stitchgraph.core import graphdiff


def _index(d: Path, files: dict[str, str]):
    for name, body in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    store = sg.Store(":memory:")
    sg.reindex(store, str(d))
    return store


PLAN = {
    "score.py": (
        "def heavy(v):\n    return v * v\n\n"
        "def combine(x, y):\n    return x + y\n\n"
        "def score(a, b):\n"
        "    x = heavy(a)\n"
        "    y = heavy(b)\n"
        "    return combine(x, y)\n"
    ),
}
# same names, same calls (heavy x2, combine x1) -> identical call graph, but a data-flow bug:
# the second heavy() is fed `a` not `b`.
BUGGY = {
    "score.py": (
        "def heavy(v):\n    return v * v\n\n"
        "def combine(x, y):\n    return x + y\n\n"
        "def score(a, b):\n"
        "    x = heavy(a)\n"
        "    y = heavy(a)\n"
        "    return combine(x, y)\n"
    ),
}


def test_differential_oracle_self_diff_is_equivalent(tmp_path):
    store = _index(tmp_path, PLAN)
    d = graphdiff.graph_diff(store, store, mode="id")
    assert d["equivalent"], d
    assert not d["nodes_only_a"] and not d["nodes_only_b"]
    assert not d["edges_only_a"] and not d["edges_only_b"]
    assert not d["body_changed"]


def test_dropped_call_and_rename_located(tmp_path):
    a = _index(tmp_path / "a", {"m.py": "def helper():\n    return 1\n\n"
                                "def run():\n    return helper()\n"})
    b = _index(tmp_path / "b", {"m.py": "def helper():\n    return 1\n\n"
                                "def run():\n    return 2\n"})
    d = graphdiff.graph_diff(a, b, mode="id", body=False)
    assert not d["equivalent"]
    # the run->helper CALLS edge is present only in A
    assert any("helper" in e and "run" in e for e in d["edges_only_a"])


def test_body_diff_catches_dataflow_bug_call_graph_misses(tmp_path):
    plan = _index(tmp_path / "plan", PLAN)
    buggy = _index(tmp_path / "buggy", BUGGY)
    d = graphdiff.graph_diff(plan, buggy, mode="id", body=True)
    # call graph is identical (same defs, same calls) ...
    assert not d["nodes_only_a"] and not d["nodes_only_b"]
    assert not d["edges_only_a"] and not d["edges_only_b"]
    # ... but the body-aware layer flags score()
    changed = {c["name"] for c in d["body_changed"]}
    assert "score" in changed
    assert not d["equivalent"]


def test_operation_diffs_against_a_db_path(tmp_path):
    # build two on-disk indexes; graph_diff op takes a path to the 'other' db
    other_db = tmp_path / "other.db"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(x):\n    return x + 1\n")
    other = sg.Store(str(other_db))
    sg.reindex(other, str(tmp_path / "src"))
    other.close()

    cur = sg.Store(":memory:")
    sg.reindex(cur, str(tmp_path / "src"))
    res = sg.graph_diff(cur, str(other_db), mode="id")
    assert res.ok, res.review_reasons
    assert res.result["equivalent"]


def test_operation_refuses_missing_db(tmp_path):
    cur = _index(tmp_path, PLAN)
    res = sg.graph_diff(cur, str(tmp_path / "nope.db"))
    assert not res.ok
    assert "no index database" in " ".join(res.review_reasons).lower()


def test_operation_refuses_bad_mode(tmp_path):
    cur = _index(tmp_path, PLAN)
    res = sg.graph_diff(cur, "whatever.db", mode="bogus")
    assert not res.ok
    assert "mode" in " ".join(res.review_reasons).lower()
