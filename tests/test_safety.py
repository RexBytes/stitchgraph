"""Safety / robustness tests.

stitchgraph's safety surface is narrow (it's read-only on analyzed code), but the
guarantees still need pinning:
- the **read_only** invariant: indexing never modifies analyzed source;
- **malformed** input (syntax errors, binary files, bad coverage) is skipped, not
  fatal;
- no path **traversal**: node ids stay relative to the indexed root.
"""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg


def _project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good.py").write_text("def f():\n    return g()\ndef g():\n    return 1\n")


def test_read_only_never_modifies_source(tmp_path):
    _project(tmp_path)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in tmp_path.rglob("*.py")}
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in tmp_path.rglob("*.py")}
    assert before == after, "reindex must not touch analyzed source files"


def test_malformed_files_are_skipped_not_fatal(tmp_path):
    _project(tmp_path)
    (tmp_path / "pkg" / "broken.py").write_text("def oops(:\n  syntax error (((\n")
    (tmp_path / "pkg" / "binary.py").write_bytes(b"\x00\x01\x02\xff\xfe not text")
    with sg.Store(":memory:") as store:
        res = sg.reindex(store, str(tmp_path))  # must not raise
        assert res.ok
        # the valid file is still indexed despite the malformed siblings
        assert store.nodes_by_name("f")


def test_no_path_traversal_in_node_ids(tmp_path):
    _project(tmp_path)
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        for nid in store.all_node_ids():
            owning = nid.split("::", 1)[0]
            assert not owning.startswith("/")     # never absolute
            assert ".." not in owning.split("/")  # never escapes the root
            assert (tmp_path / owning).resolve().is_relative_to(tmp_path.resolve())


def test_malformed_coverage_refused_not_fatal(tmp_path):
    _project(tmp_path)
    bad = tmp_path / "cov.json"
    bad.write_text("{ this is not valid json ::::")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.ingest_trace(store, str(bad))  # must refuse cleanly, not crash
        assert not res.ok and res.needs_review


def test_missing_path_reindex_is_not_fatal(tmp_path):
    with sg.Store(":memory:") as store:
        res = sg.reindex(store, str(tmp_path / "does-not-exist"))
        assert res.ok and res.result["nodes"] == 0  # empty, not an exception
