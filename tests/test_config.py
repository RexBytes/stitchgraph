"""stitchgraph.toml config: entry-point override + ignore globs."""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg


def _project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # `orphan` has no caller and isn't exported -> stale by default.
    (pkg / "m.py").write_text(
        "def orphan():\n    return helper()\n\n"
        "def helper():\n    return 1\n"
    )


def test_entry_point_override_makes_node_live(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))

    stale_before = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "orphan" in stale_before  # genuinely orphaned

    (tmp_path / "stitchgraph.toml").write_text(
        '[entry_points]\ninclude = ["pkg/m.py::orphan"]\n')
    stale_after = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "orphan" not in stale_after   # pinned as a root
    assert "helper" not in stale_after   # now reached from orphan
    store.close()


def test_ignore_glob_skips_files(tmp_path, monkeypatch):
    _project(tmp_path)
    (tmp_path / "pkg" / "generated.py").write_text("def boom():\n    return 1\n")
    (tmp_path / "stitchgraph.toml").write_text(
        '[index]\nignore = ["pkg/generated.py"]\n')
    monkeypatch.chdir(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    assert store.nodes_by_name("boom") == []  # skipped by the ignore glob
    store.close()


def test_include_tests_defaults_true_and_respects_override(tmp_path):
    """Mutation (scripts/mutate.py on config.py): the `include_tests` default flips
    True->False unnoticed — no test pinned it. Absent key must default True; an explicit
    false must be honoured."""
    from stitchgraph.core.config import load_config
    # load_config(start) searches `start` (a directory) upward for stitchgraph.toml.
    no_key = tmp_path / "nokey"
    no_key.mkdir()
    (no_key / "stitchgraph.toml").write_text("[entry_points]\ninclude = []\n")
    assert load_config(no_key).include_tests is True
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "stitchgraph.toml").write_text("[entry_points]\ninclude_tests = false\n")
    assert load_config(explicit).include_tests is False
