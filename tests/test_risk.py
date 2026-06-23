"""Git-history risk fusion: hotspots + hidden coupling."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import stitchgraph as sg


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def test_risk_refuses_without_git(tmp_path):
    with sg.Store(":memory:") as store:
        res = sg.risk(store, str(tmp_path))
        assert not res.ok and res.needs_review


def test_risk_finds_hidden_coupling(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not available")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    a, b = pkg / "a.py", pkg / "b.py"
    a.write_text("def fa():\n    return 1\n")          # no import of b
    b.write_text("def fb():\n    return 2\n")          # no import of a

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "c1")
    a.write_text("def fa():\n    return 10\n")
    b.write_text("def fb():\n    return 20\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "c2")

    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.risk(store, str(tmp_path))
        assert res.ok
        pairs = [set(h["files"]) for h in res.result["hidden_coupling"]]
        # a.py and b.py change together but share no structural edge.
        assert {"pkg/a.py", "pkg/b.py"} in pairs
