"""v3.39.0: fixture-aware test rooting — pytest injects fixtures BY PARAMETER
NAME, invisible to every call pass (research/18's zero-recall tests reached
all their real setup through fixtures)."""

from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core.reach import reachable_from


def _project(tmp_path):
    (tmp_path / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def loaded_registry(base_env):\n"      # fixture requesting a fixture
        "    return _build_registry()\n\n"
        "@pytest.fixture\n"
        "def base_env():\n"
        "    return _make_env()\n\n"
        "def _build_registry():\n    return {}\n\n"
        "def _make_env():\n    return {}\n")
    (tmp_path / "test_reg.py").write_text(
        "def test_lookup(loaded_registry):\n"
        "    assert loaded_registry is not None\n")


def test_fixture_chain_reached_from_test(tmp_path):
    _project(tmp_path)
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = reachable_from(store, {"test_reg.py::test_lookup"})
        assert "conftest.py::loaded_registry" in reach       # param -> fixture
        assert "conftest.py::_build_registry" in reach       # fixture body
        assert "conftest.py::base_env" in reach              # fixture -> fixture
        assert "conftest.py::_make_env" in reach             # transitively


def test_fixture_helpers_not_flagged_dead(tmp_path):
    _project(tmp_path)
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "_build_registry" not in stale
        assert "_make_env" not in stale


def test_builtin_fixtures_and_plain_params_bind_nothing(tmp_path):
    (tmp_path / "test_x.py").write_text(
        "def test_io(tmp_path, monkeypatch):\n"
        "    assert tmp_path\n\n"
        "def helper(data):\n"       # not a test, not a fixture: params untouched
        "    return data\n\n"
        "def data():\n    return 1\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = reachable_from(store, {"test_x.py::test_io"})
        assert "test_x.py::data" not in reach  # 'data' param of helper never binds
        n = store.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE src='test_x.py::test_io' "
            "AND relation='CALLS'").fetchone()[0]
        assert n == 0  # tmp_path/monkeypatch aren't project defs
