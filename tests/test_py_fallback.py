"""v3.37.0: files with syntax newer than the indexing interpreter must never vanish
silently (research/18 bug 1: 880 PEP 695 files — 10% of Home Assistant, half its
test-executed functions — were dropped with no signal). Two contracts:

1. With tree-sitter installed, such files are RESCUED by the Python grammar at
   structural fidelity — module/class/method/function nodes on the stdlib-ast id
   conventions, call edges, and a `python_fallback_files` count in the reindex meta.
2. Without tree-sitter, the files stay missing but the reindex Result SAYS so:
   `skipped_files` meta + a review reason naming them.

PEP 695 `type` aliases parse on NO released Python below 3.12, so the fixture is a
guaranteed ast.parse failure on 3.11 (CI) while remaining valid modern Python. On an
indexing interpreter >= 3.12 the file parses natively and the fallback never fires —
tests that need the fallback path itself are guarded accordingly."""

from __future__ import annotations

import ast

import pytest

import stitchgraph as sg
from stitchgraph.core import extract as extract_pkg

MODERN = (
    "type Alias = dict[str, int]\n\n"
    "class Manager:\n"
    "    def create_token(self):\n"
    "        return _mint()\n\n"
    "def _mint():\n    return 1\n"
)


def _parses_natively() -> bool:
    try:
        ast.parse(MODERN)
        return True
    except SyntaxError:
        return False


def test_pep695_file_is_rescued_by_treesitter_fallback(tmp_path):
    pytest.importorskip("tree_sitter_language_pack")
    if _parses_natively():
        pytest.skip("indexing interpreter parses PEP 695 natively — fallback never fires")
    (tmp_path / "modern.py").write_text(MODERN)
    with sg.Store(":memory:") as store:
        r = sg.reindex(store, str(tmp_path))
        assert r.ok and r.meta.get("python_fallback_files") == 1
        assert "skipped_files" not in r.meta  # rescued, not missing
        kinds = {row[0]: row[1] for row in
                 store.conn.execute("SELECT id, kind FROM nodes")}
        # stdlib-ast id + kind conventions hold on the fallback path
        assert kinds["modern.py::Manager"] == "Class"
        assert kinds["modern.py::Manager.create_token"] == "Method"
        assert kinds["modern.py::_mint"] == "Function"
        edges = {(row[0], row[1], row[2]) for row in store.conn.execute(
            "SELECT src, relation, dst_id FROM edges WHERE dst_id IS NOT NULL")}
        assert ("modern.py::Manager.create_token", "CALLS", "modern.py::_mint") in edges


def test_unparseable_file_surfaces_when_no_treesitter(tmp_path, monkeypatch):
    if _parses_natively():
        pytest.skip("indexing interpreter parses PEP 695 natively — nothing is skipped")
    import stitchgraph.core.extract.treesitter as ts
    monkeypatch.setattr(ts, "HAS_TREE_SITTER", False)
    (tmp_path / "modern.py").write_text(MODERN)
    (tmp_path / "ok.py").write_text("def fine():\n    return 1\n")
    with sg.Store(":memory:") as store:
        r = sg.reindex(store, str(tmp_path))
        assert r.ok  # one bad file never aborts the reindex
        assert r.meta.get("skipped_files") == 1
        assert r.needs_review
        assert any("modern.py" in reason and "MISSING" in reason
                   for reason in r.review_reasons)
        assert len(store.nodes_by_name("fine")) == 1  # the parseable file is unaffected


def test_extract_report_separates_skipped_from_rescued(tmp_path):
    """The package dispatcher's report: a SyntaxError file goes to `fallback` when
    tree-sitter rescues it, while a genuinely unreadable file stays in `skipped`."""
    pytest.importorskip("tree_sitter_language_pack")
    if _parses_natively():
        pytest.skip("indexing interpreter parses PEP 695 natively — fallback never fires")
    (tmp_path / "modern.py").write_text(MODERN)
    report: dict = {}
    extract_pkg.extract_project(str(tmp_path), report=report)
    assert report["fallback"] == ["modern.py"]
    assert report["skipped"] == []


def test_normal_python_never_goes_through_treesitter(tmp_path):
    """.py is deliberately absent from EXT_LANG: the stdlib-ast extractor owns Python,
    and the tree-sitter grammar sees only explicit fallback files — otherwise every
    Python file would be double-extracted with colliding module nodes."""
    from stitchgraph.core.extract.treesitter import EXT_LANG
    assert ".py" not in EXT_LANG
    (tmp_path / "plain.py").write_text("def solo():\n    return 1\n")
    with sg.Store(":memory:") as store:
        r = sg.reindex(store, str(tmp_path))
        assert r.ok and "python_fallback_files" not in r.meta
        assert len(store.nodes_by_name("solo")) == 1  # exactly one node, no duplicate
