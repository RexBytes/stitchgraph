"""v3.39.0: the getattr-dispatch heuristic. `getattr(recv, f"_step_{x}")`
invokes SOME `_step_*` member the source never names — research/18 measured
the misses on HA (`_ScriptRun._async_step_*`), research/19 found the shape
twice in Django (`as_%s` vendor methods, `_get_%s_permissions`)."""

from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core.reach import reachable_from


def _reach(store, seed):
    return reachable_from(store, {seed})


def test_fstring_dispatch_reaches_prefixed_members(tmp_path):
    (tmp_path / "script.py").write_text(
        "class Runner:\n"
        "    def run(self, action):\n"
        "        return getattr(self, f'_async_step_{action}')()\n"
        "    def _async_step_event(self):\n        return self._fire()\n"
        "    def _async_step_delay(self):\n        return 2\n"
        "    def _fire(self):\n        return 1\n"
        "    def _unrelated(self):\n        return 3\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "script.py::Runner.run")
        assert "script.py::Runner._async_step_event" in reach
        assert "script.py::Runner._async_step_delay" in reach
        assert "script.py::Runner._fire" in reach          # THROUGH the handler
        assert "script.py::Runner._unrelated" not in reach  # pattern-scoped


def test_percent_format_dispatch_crosses_classes(tmp_path):
    """Django's exact shape: the matching member lives on another class."""
    (tmp_path / "lookups.py").write_text(
        "class Lookup:\n"
        "    def as_sql(self, connection):\n"
        "        return getattr(self, 'as_%s' % connection.vendor)()\n\n"
        "class HasKey(Lookup):\n"
        "    def as_mysql(self):\n        return 1\n"
        "    def as_postgresql(self):\n        return 2\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "lookups.py::Lookup.as_sql")
        assert "lookups.py::HasKey.as_mysql" in reach
        assert "lookups.py::HasKey.as_postgresql" in reach


def test_concat_and_format_shapes(tmp_path):
    (tmp_path / "perm.py").write_text(
        "class Backend:\n"
        "    def perms(self, kind, obj):\n"
        "        a = getattr(self, '_get_' + kind + '_permissions')(obj)\n"
        "        b = getattr(self, 'handle_{}'.format(kind))(obj)\n"
        "        return a or b\n"
        "    def _get_user_permissions(self, o):\n        return 1\n"
        "    def _get_group_permissions(self, o):\n        return 2\n"
        "    def handle_user(self, o):\n        return 3\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "perm.py::Backend.perms")
        assert "perm.py::Backend._get_user_permissions" in reach
        assert "perm.py::Backend._get_group_permissions" in reach
        assert "perm.py::Backend.handle_user" in reach


def test_anchorless_pattern_adds_nothing(tmp_path):
    """A bare f"{x}" (no literal anchor) would match every symbol — rejected."""
    (tmp_path / "wild.py").write_text(
        "class W:\n"
        "    def run(self, x):\n"
        "        return getattr(self, f'{x}')()\n"
        "    def secret(self):\n        return 1\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert "wild.py::W.secret" not in _reach(store, "wild.py::W.run")
