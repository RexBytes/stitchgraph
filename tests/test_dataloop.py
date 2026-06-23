"""Data-loop detection: feedback through mutable global state."""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg
from stitchgraph.core.dataloop import find_data_loops


def _project(root: Path) -> sg.Store:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "acc.py").write_text(
        "total = 0\n\n"
        "def accumulate(x):\n"          # reads AND writes total -> feedback loop
        "    global total\n"
        "    total = total + x\n"
        "    return total\n\n"
        "def reset():\n"                # write-only -> no feedback
        "    global total\n"
        "    total = 0\n\n"
        "def report():\n"               # read-only -> no feedback
        "    return total\n"
    )
    store = sg.Store(":memory:")
    sg.reindex(store, str(root))
    return store


def test_detects_accumulator_feedback(tmp_path):
    with _project(tmp_path) as store:
        loops = find_data_loops(store)
        members = {m.rsplit("::", 1)[-1] for comp in loops for m in comp}
        assert "accumulate" in members
        assert "total" in members
        # write-only / read-only functions are not part of a feedback loop
        assert "reset" not in members
        assert "report" not in members


def test_data_loop_surfaces_in_scan(tmp_path):
    with _project(tmp_path) as store:
        kinds = {(i["kind"], i["urgency"]) for i in sg.scan(store).result}
        assert ("data_loop", "orange") in kinds


def test_no_globals_no_loops(tmp_path):
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("def f(x):\n    return x + 1\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert find_data_loops(store) == []
