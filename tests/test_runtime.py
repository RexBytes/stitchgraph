"""Runtime-trace fusion: coverage grounds liveness and dead-code confidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import stitchgraph as sg


def _project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # None are exported/called -> all look dead statically.
    (pkg / "m.py").write_text(
        "def used():\n        return 1\n\n"        # lines 1-2
        "def reflective():\n        return 2\n\n"  # lines 4-5
        "def truly_dead():\n        return 3\n"    # lines 7-8
    )


def _coverage(root: Path, executed: list[int]) -> Path:
    p = root / "coverage.json"
    p.write_text(json.dumps({"files": {"pkg/m.py": {"executed_lines": executed}}}))
    return p


def _index(root: Path) -> sg.Store:
    _project(root)
    store = sg.Store(":memory:")
    sg.reindex(store, str(root))
    return store


def test_ingest_marks_executed_nodes(tmp_path):
    with _index(tmp_path) as store:
        _coverage(tmp_path, [1, 2, 4, 5])  # used + reflective ran; truly_dead didn't
        res = sg.ingest_trace(store, str(tmp_path / "coverage.json"))
        assert res.ok and res.meta["executed"] == 2
        runtime = {n.name for n in store.nodes_with_role("runtime")}
        assert runtime == {"used", "reflective"}


def test_runtime_makes_executed_code_live(tmp_path):
    with _index(tmp_path) as store:
        sg.ingest_trace(store, str(_coverage(tmp_path, [1, 2, 4, 5])))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "used" not in stale          # executed -> live
        assert "reflective" not in stale     # executed (dynamically) -> live
        assert "truly_dead" in stale         # never ran -> still dead


def test_runtime_raises_stale_confidence(tmp_path):
    with _index(tmp_path) as store:
        sg.ingest_trace(store, str(_coverage(tmp_path, [1, 2])))
        res = sg.find_stale(store)
        assert res.confidence > 0.7          # grounded in a real trace
        assert any("executed in the ingested trace" in r for r in res.review_reasons)


def test_ingest_refuses_missing_trace(tmp_path):
    with _index(tmp_path) as store:
        res = sg.ingest_trace(store, str(tmp_path / "nope.json"))
        assert not res.ok and res.needs_review


def test_lcov_format(tmp_path):
    pytest.importorskip("tree_sitter_language_pack")  # JS extraction needs tree-sitter
    (tmp_path / "u.js").write_text(
        "export function ran(){ return 1; }\nexport function notRun(){ return 2; }\n")
    (tmp_path / "lcov.info").write_text("SF:u.js\nDA:1,5\nDA:2,0\nend_of_record\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert sg.ingest_trace(store, str(tmp_path / "lcov.info")).ok
        hit = {n.name for n in store.nodes_with_role("runtime")}
        assert "ran" in hit and "notRun" not in hit


def test_go_coverprofile_format(tmp_path):
    pytest.importorskip("tree_sitter_language_pack")  # Go extraction needs tree-sitter
    (tmp_path / "m.go").write_text(
        "package m\nfunc GoRan() int {\n return 1\n}\nfunc GoNot() int {\n return 2\n}\n")
    (tmp_path / "cover.out").write_text(
        "mode: set\nm.go:2.20,4.2 1 3\nm.go:5.20,7.2 1 0\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert sg.ingest_trace(store, str(tmp_path / "cover.out")).ok
        hit = {n.name for n in store.nodes_with_role("runtime")}
        assert "GoRan" in hit and "GoNot" not in hit
