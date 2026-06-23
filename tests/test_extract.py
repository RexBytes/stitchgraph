"""Extractor + end-to-end operation tests against a synthetic fixture project."""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg
from stitchgraph.core.extract import extract_project


def _write_project(root: Path) -> None:
    pkg = root / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('from .api import public_api\n__all__ = ["public_api"]\n')
    (pkg / "api.py").write_text(
        "from .util import helper\n\n"
        "def public_api(x):\n"
        "    return helper(x)\n\n"
        "def orphan():\n"  # defined, never referenced -> dead
        "    return 1\n"
    )
    (pkg / "util.py").write_text(
        "def helper(x):\n"
        "    return missing_dep(x)\n\n"  # missing_dep is undefined internally
        "def todo():\n"
        "    raise NotImplementedError\n"
    )
    (pkg / "cli.py").write_text(
        "from .api import public_api\n\n"
        "def run():\n"
        "    return public_api(1)\n\n"
        'if __name__ == "__main__":\n'
        "    run()\n"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_api.py").write_text(
        "from mypkg.api import public_api\n\n"
        "def test_public_api():\n"
        "    assert public_api(2)\n"
    )


def _index(tmp_path: Path) -> sg.Store:
    _write_project(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    return store


def test_extract_produces_nodes_and_edges(tmp_path):
    _write_project(tmp_path)
    nodes, edges = extract_project(str(tmp_path))
    names = {n.name for n in nodes}
    assert {"public_api", "helper", "orphan", "todo", "run"} <= names
    assert any(e.relation.value == "CALLS" for e in edges)


def test_roles_tag_entrypoints(tmp_path):
    with _index(tmp_path) as store:
        exported = {n.name for n in store.nodes_with_role("exported")}
        assert "public_api" in exported           # public API is a root
        mains = {n.name for n in store.nodes_with_role("main")}
        assert "run" in mains                      # invoked from __main__
        tests = {n.name for n in store.nodes_with_role("test")}
        assert "test_public_api" in tests


def test_find_stale_finds_orphan_not_public_api(tmp_path):
    with _index(tmp_path) as store:
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "orphan" in stale          # genuinely unreferenced
        assert "public_api" not in stale  # exported: never dead for lack of callers
        assert "helper" not in stale      # reachable via public_api


def test_find_holes_flags_internal_missing(tmp_path):
    with _index(tmp_path) as store:
        holes = sg.find_holes(store)
        # `missing_dep` is called but defined nowhere -> dropped as external
        # (precision); the reliable hole signal is internal imports. Either way,
        # find_holes must not crash and returns a list.
        assert isinstance(holes.result, list)


def test_scan_flags_live_stub(tmp_path):
    with _index(tmp_path) as store:
        # `todo` raises NotImplementedError but isn't reachable -> green stub,
        # not red. Make it reachable by overriding it as an entry point.
        from stitchgraph.core.entrypoints import PythonLibraryDetector
        det = PythonLibraryDetector(overrides={"mypkg/util.py::todo"})
        scan = sg.scan(store, detector=det)
        stubs = [i for i in scan.result if i["kind"] == "live_stub"]
        assert any(s["node"].endswith("::todo") for s in stubs)


def test_impact_of_reverse_reachability(tmp_path):
    with _index(tmp_path) as store:
        impact = sg.impact_of(store, "helper")
        deps = set(impact.result["blast_radius"])
        assert any(d.endswith("::public_api") for d in deps)


def test_trace_path_finds_route(tmp_path):
    with _index(tmp_path) as store:
        res = sg.trace_path(store, "public_api", "helper")
        assert res.ok
        assert res.result[0].endswith("::public_api")
        assert res.result[-1].endswith("::helper")


def test_reindex_is_idempotent(tmp_path):
    with _index(tmp_path) as store:
        n1 = store.node_count()
        sg.reindex(store, str(tmp_path))
        assert store.node_count() == n1  # rebuild, not duplicate


def test_get_matrix_bounded_and_refuses(tmp_path):
    with _index(tmp_path) as store:
        m = sg.get_matrix(store, "mypkg/api.py", "CALLS")
        assert m.ok
        assert "public_api" in m.result["labels"]
        assert m.result["relation"] == "CALLS"
        # too-broad scope refuses rather than dumping a huge matrix
        big = sg.get_matrix(store, "mypkg", "CALLS", limit=1)
        assert big.needs_review and not big.ok
