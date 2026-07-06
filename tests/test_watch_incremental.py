"""v3.38.0: the incremental watch path — `reindex_incremental` (whole-project
extraction, per-owner `replace_file` writes) must land on the SAME graph a fresh
full reindex of the modified tree produces. The store-side convergence machinery
(worklist re-resolve, name-based re-widening, override propagation, dangling
invalidation) is already pinned in the store tests; these pin the new wiring:
owner grouping, pseudo-owner refresh, emptied-file cleanup, exported-surface
propagation, and the watch `diff` classification that drives the CLI fallbacks."""

from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core.operations import reindex_incremental
from stitchgraph.core.watch import diff


def _graph(store):
    nodes = {tuple(r) for r in store.conn.execute(
        "SELECT id, kind, name, roles FROM nodes")}
    edges = {tuple(r) for r in store.conn.execute(
        "SELECT src, relation, dst_symbol, dst_id, weight, provenance, name_based "
        "FROM edges")}
    return nodes, edges


def _converges(tmp_path, mutate, changed):
    """Full-reindex the tree, mutate it, incrementally apply `changed`, and compare
    against a fresh full reindex of the mutated tree in a second store."""
    with sg.Store(":memory:") as inc, sg.Store(":memory:") as full:
        sg.reindex(inc, str(tmp_path))
        mutate()
        r = reindex_incremental(inc, str(tmp_path), changed)
        assert r.ok
        sg.reindex(full, str(tmp_path))
        assert _graph(inc) == _graph(full)
        return r


def test_modified_file_converges_with_full_reindex(tmp_path):
    (tmp_path / "util.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "app.py").write_text(
        "from util import helper\n\ndef main():\n    return helper()\n")

    def mutate():
        (tmp_path / "app.py").write_text(
            "from util import helper\n\ndef main():\n    return helper()\n\n"
            "def extra():\n    return helper()\n")

    r = _converges(tmp_path, mutate, {"app.py"})
    assert r.meta["replaced"] >= 1


def test_added_homonym_rewidens_existing_callers(tmp_path):
    """A NEW same-named definition must re-widen the existing caller's name-based
    edge into AMBIGUOUS arms — the classic incremental drift replace_file's
    _rewiden_resolved exists for, now reachable through the watch wiring."""
    (tmp_path / "a.py").write_text("def process():\n    return 1\n")
    (tmp_path / "caller.py").write_text(
        "def run(x):\n    return x.process()\n")  # receiver call -> name-based

    def mutate():
        (tmp_path / "b.py").write_text("def process():\n    return 2\n")

    _converges(tmp_path, mutate, {"b.py"})


def test_emptied_file_clears_its_rows(tmp_path):
    (tmp_path / "gone.py").write_text("def vanishing():\n    return 1\n")
    (tmp_path / "keep.py").write_text("def stays():\n    return 1\n")

    def mutate():
        (tmp_path / "gone.py").write_text("")  # emptied, not deleted

    _converges(tmp_path, mutate, {"gone.py"})


def test_pseudo_owner_refreshes_without_mtime(tmp_path):
    """An edit that mints an EVENT node changes the pseudo owner `event`, whose
    'file' never appears in a snapshot — the incremental path must refresh pseudo
    owners unconditionally or the new event node (and the decoupled trace through
    it) silently fails to appear."""
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "bus.py").write_text(
        "bus = object()\n"
        "def publisher():\n    return 1\n"
        "def setup():\n    bus.on('user_created', on_created)\n"
        "def on_created():\n    return 1\n")

    def mutate():
        (pkg / "bus.py").write_text(
            "bus = object()\n"
            "def publisher():\n    bus.emit('user_created', 1)\n"
            "def setup():\n    bus.on('user_created', on_created)\n"
            "def on_created():\n    return 1\n")

    _converges(tmp_path, mutate, {"p/bus.py"})


def test_reexport_surface_change_converges(tmp_path):
    """Editing a package __init__'s __all__ must update the exported role on
    OTHER files' symbols (the panel-R37A contract: exported_ids from the same
    whole-project extract)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "impl.py").write_text("def api():\n    return 1\n\ndef _private():\n    return 2\n")
    (pkg / "__init__.py").write_text("from .impl import api\n__all__ = []\n")

    def mutate():
        (pkg / "__init__.py").write_text("from .impl import api\n__all__ = ['api']\n")

    _converges(tmp_path, mutate, {"pkg/__init__.py"})


def test_diff_classifies_changes():
    old = {"/r/a.py": 1.0, "/r/b.py": 1.0, "/r/c.py": 1.0}
    new = {"/r/a.py": 1.0, "/r/b.py": 2.0, "/r/d.py": 1.0}
    added, removed, modified = diff(old, new)
    assert added == {"/r/d.py"}
    assert removed == {"/r/c.py"}   # -> the CLI falls back to a full reindex
    assert modified == {"/r/b.py"}


def test_incremental_refuses_bad_root(tmp_path):
    with sg.Store(":memory:") as store:
        (tmp_path / "a.py").write_text("def f():\n    return 1\n")
        sg.reindex(store, str(tmp_path))
        before = _graph(store)
        r = reindex_incremental(store, str(tmp_path / "nope"), {"a.py"})
        assert not r.ok
        assert _graph(store) == before  # untouched
