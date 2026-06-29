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


def test_leaf_helper_normalises_ctor_only():
    # _leaf reduces to the name tail and canonicalises constructor spellings, nothing else.
    assert graphdiff._leaf("Box.__init__") == "<init>"
    assert graphdiff._leaf("Widget.constructor") == "<init>"
    assert graphdiff._leaf("pkg.sub.helper") == "helper"
    assert graphdiff._leaf("a/m.py::run") == "run"
    assert graphdiff._leaf("helper") == "helper"  # a non-ctor name is returned as-is
    assert graphdiff._leaf("") == ""


def test_leaf_mode_resolves_module_path_difference(tmp_path):
    # Module nodes are dotted (pkg_a.util vs pkg_b.util): id mode flags them as different, leaf
    # mode (name tail "util") treats them as the same. Pins the node-key mode branch in both
    # directions — a flipped branch would invert which mode shows the delta.
    a = _index(tmp_path / "a", {"pkg_a/util.py": "def helper(x):\n    return x + 1\n"})
    b = _index(tmp_path / "b", {"pkg_b/util.py": "def helper(x):\n    return x + 1\n"})
    d_id = graphdiff.graph_diff(a, b, mode="id", body=False)
    d_leaf = graphdiff.graph_diff(a, b, mode="leaf", body=False)
    # id mode: the two module nodes differ by their dotted package
    assert any("pkg_a.util" in n for n in d_id["nodes_only_a"])
    assert any("pkg_b.util" in n for n in d_id["nodes_only_b"])
    # leaf mode: module leaf "util" matches, helper matches -> no node deltas at all
    assert not d_leaf["nodes_only_a"] and not d_leaf["nodes_only_b"]


def test_leaf_mode_resolves_module_path_edges(tmp_path):
    # An IMPORTS edge to a submodule carries the dotted package (pkg_a.util): id mode flags it as
    # different across two packages, leaf mode reduces it to (main, IMPORTS, util) and resolves it.
    # Pins the edge-key mode branch.
    a = _index(tmp_path / "a", {
        "pkg_a/__init__.py": "",
        "pkg_a/util.py": "def helper(n):\n    return n + 1\n",
        "pkg_a/main.py": "from pkg_a import util\n\ndef run():\n    return util.helper(1)\n",
    })
    b = _index(tmp_path / "b", {
        "pkg_b/__init__.py": "",
        "pkg_b/util.py": "def helper(n):\n    return n + 1\n",
        "pkg_b/main.py": "from pkg_b import util\n\ndef run():\n    return util.helper(1)\n",
    })
    d_id = graphdiff.graph_diff(a, b, mode="id", body=False)
    d_leaf = graphdiff.graph_diff(a, b, mode="leaf", body=False)
    # id mode: the dotted submodule import differs between the packages
    assert any("pkg_a.util" in e for e in d_id["edges_only_a"])
    # leaf mode: that submodule import resolves to (main, IMPORTS, util) on both sides
    assert not any("IMPORTS" in e and "util" in e for e in d_leaf["edges_only_a"])


def test_body_analysis_on_by_default(tmp_path):
    # graph_diff(...) with no body= must still run the body layer (default True). Pins the default.
    plan = _index(tmp_path / "plan", PLAN)
    buggy = _index(tmp_path / "buggy", BUGGY)
    d = graphdiff.graph_diff(plan, buggy)  # no body kwarg
    assert any(c["name"] == "score" for c in d["body_changed"])


def test_identical_bodies_not_flagged(tmp_path):
    # threshold direction: functions whose body is unchanged must NOT appear in body_changed.
    a = _index(tmp_path / "a", PLAN)
    b = _index(tmp_path / "b", PLAN)
    d = graphdiff.graph_diff(a, b, mode="id", body=True)
    assert d["body_changed"] == []
    assert d["equivalent"]


def test_internal_call_edge_resolves_to_target_name(tmp_path):
    # A resolved CALLS edge keys on the TARGET's node name; diffing two repos where run() calls a
    # differently-named helper must surface that edge delta (exercises the dst resolution).
    a = _index(tmp_path / "a", {"m.py": "def helper_a():\n    return 1\n\n"
                                "def run():\n    return helper_a()\n"})
    b = _index(tmp_path / "b", {"m.py": "def helper_b():\n    return 1\n\n"
                                "def run():\n    return helper_b()\n"})
    d = graphdiff.graph_diff(a, b, mode="id", body=False)
    assert any("helper_a" in e for e in d["edges_only_a"])
    assert any("helper_b" in e for e in d["edges_only_b"])
    # the edge src is the bare node NAME ("run"), not the path-qualified id ("a/m.py::run")
    assert any(e.startswith("('run'") for e in d["edges_only_a"])


def test_same_qualname_in_different_files_not_collapsed(tmp_path):
    # R153 (opus, HIGH): body matching keyed by bare qualname dropped a real change when two files
    # share a name. Here u.py::helper changes substantially while v.py::helper is untouched; the
    # change must NOT be swallowed by v.py::helper.
    common_v = "def helper():\n    return 1\n"
    changed_u = "def helper():\n    out = []\n    for i in range(10):\n        out.append(i * i)\n    return out\n"
    a = _index(tmp_path / "a", {"u.py": "def helper():\n    return 1\n", "v.py": common_v})
    b = _index(tmp_path / "b", {"u.py": changed_u, "v.py": common_v})
    d = graphdiff.graph_diff(a, b, mode="id", body=True)
    assert any(c["name"] == "helper" for c in d["body_changed"])
    assert not d["equivalent"]


def test_empty_body_function_not_flagged_against_itself(tmp_path):
    # R153 (opus, MEDIUM): stubs (pass / ... / docstring-only) have an empty fingerprint and
    # similarity(empty, empty)==0.0 — must NOT be flagged changed when unchanged.
    files = {"s.py": "def stub():\n    ...\n\nclass P:\n    def m(self):\n        pass\n\n"
                     "def real(x):\n    return x + 1\n"}
    a = _index(tmp_path / "a", files)
    b = _index(tmp_path / "b", files)
    d = graphdiff.graph_diff(a, b, mode="id", body=True)
    assert d["body_changed"] == []
    assert d["equivalent"]


def test_operation_handles_db_path_with_uri_reserved_chars(tmp_path):
    # R154 (opus LOW): a valid index whose filename contains ?/# must not be misparsed by the
    # read-only URI probe and falsely refused.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(x):\n    return x + 1\n")
    weird = tmp_path / "weird?x#y.db"
    other = sg.Store(str(weird))
    sg.reindex(other, str(tmp_path / "src"))
    other.close()
    cur = sg.Store(":memory:")
    sg.reindex(cur, str(tmp_path / "src"))
    res = sg.graph_diff(cur, str(weird), mode="id")
    assert res.ok, res.review_reasons
    assert res.result["equivalent"]


def test_body_diff_catches_control_flow_nested_def_change(tmp_path):
    # R155 (opus): a body change to a def nested in a control-flow block must be caught, not
    # silently swallowed (qualname "outer.inner", no control-flow qual level).
    plan = {"m.py": "def outer(c):\n    if c:\n        def inner(x):\n            return x + 1\n"
                    "        return inner(c)\n    return 0\n"}
    actual = {"m.py": "def outer(c):\n    if c:\n        def inner(x):\n            return x - 1\n"
                      "        return inner(c)\n    return 0\n"}
    a = _index(tmp_path / "a", plan)
    b = _index(tmp_path / "b", actual)
    d = graphdiff.graph_diff(a, b, mode="id", body=True)
    assert any(c["name"] == "outer.inner" for c in d["body_changed"]), d["body_changed"]


def test_body_threshold_tunes_sensitivity(tmp_path):
    # R155 (sonnet NIT): body_threshold is exposed. score's body sim ~0.6 — flagged at the 0.95
    # default, not flagged when the caller lowers the bar below it.
    a = _index(tmp_path / "a", PLAN)
    b = _index(tmp_path / "b", BUGGY)
    hi = graphdiff.graph_diff(a, b, mode="id", body=True, body_threshold=0.95)
    lo = graphdiff.graph_diff(a, b, mode="id", body=True, body_threshold=0.3)
    assert any(c["name"] == "score" for c in hi["body_changed"])
    assert not any(c["name"] == "score" for c in lo["body_changed"])


def test_operation_refuses_bad_body_threshold(tmp_path):
    cur = _index(tmp_path, PLAN)
    for bad in (1.5, 0.0, -0.1):
        res = sg.graph_diff(cur, "x.db", body_threshold=bad)
        assert not res.ok
        assert "body_threshold" in " ".join(res.review_reasons).lower()


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


def test_operation_refuses_corrupt_db_without_crashing(tmp_path):
    # R153 (sonnet F1): a real file that isn't a SQLite db must return a Result, not raise.
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite database, at all\x00\x01")
    cur = _index(tmp_path, PLAN)
    res = sg.graph_diff(cur, str(bad))
    assert not res.ok
    assert "stitchgraph index" in " ".join(res.review_reasons).lower()


def test_operation_refuses_alien_db_without_mutating_it(tmp_path):
    # R153 (sonnet F2): a valid SQLite file that isn't a stitchgraph index must be refused AND
    # left untouched (no migration tables added) — the read-only-on-other-files promise.
    import sqlite3
    alien = tmp_path / "app.db"
    conn = sqlite3.connect(str(alien))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()
    before = alien.read_bytes()
    cur = _index(tmp_path, PLAN)
    res = sg.graph_diff(cur, str(alien))
    assert not res.ok
    assert "stitchgraph index" in " ".join(res.review_reasons).lower()
    assert alien.read_bytes() == before  # not mutated — no tables added
