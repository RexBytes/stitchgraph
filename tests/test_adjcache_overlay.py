"""v3.40.0: the incremental sidecar refresh. After a replace_file, `load_cache`
patches a row OVERLAY from the captured adj_delta metas instead of paying the
full rebuild. Contracts: overlay-served BFS-family results are byte-equal to a
fresh full rebuild; new nodes are reachable through overlay rows; SCC and
articulation refuse the patched cache and fall back to the reference path; a
broken delta chain degrades to the full rebuild."""

from __future__ import annotations

import shutil

import pytest

import stitchgraph as sg
from stitchgraph.core.adjcache import load_cache, sidecar_path
from stitchgraph.core.operations import reindex_incremental
from stitchgraph.core.reach import (
    reachable_from_many,
    strongly_connected_components,
)

pytest.importorskip("numpy")


def _project(tmp_path):
    (tmp_path / "util.py").write_text(
        "def helper():\n    return 1\n\ndef spare():\n    return 2\n")
    (tmp_path / "app.py").write_text(
        "from util import helper\n\ndef main():\n    return helper()\n")
    (tmp_path / "cyc.py").write_text(
        "def a():\n    return b()\n\ndef b():\n    return a()\n")


def _warm(tmp_path):
    db = str(tmp_path / "g.db")
    store = sg.Store(db)
    sg.reindex(store, str(tmp_path))
    assert load_cache(store) is not None  # warm the sidecar at gen G
    return store


def test_overlay_patch_matches_full_rebuild(tmp_path):
    _project(tmp_path)
    store = _warm(tmp_path)
    # Edit: main() now ALSO calls spare(), and a brand-new function appears.
    (tmp_path / "app.py").write_text(
        "from util import helper, spare\n\n"
        "def main():\n    return helper() or spare() or fresh()\n\n"
        "def fresh():\n    return 3\n")
    r = reindex_incremental(store, str(tmp_path), {"app.py"})
    assert r.ok
    cache = load_cache(store)
    assert cache is not None and cache.has_overlay  # patched, not rebuilt
    seeds = {"app.py::main"}
    patched_reach = cache.reachable(seeds, __import__(
        "stitchgraph.core.reach", fromlist=["LIVENESS_RELATIONS"]).LIVENESS_RELATIONS)
    patched_many = reachable_from_many(store, [seeds, {"cyc.py::a"}])
    patched_fan_in = cache.fan_in(__import__(
        "stitchgraph.core.reach", fromlist=["LIVENESS_RELATIONS"]).LIVENESS_RELATIONS)
    # Now force the full rebuild and compare byte-for-byte.
    shutil.rmtree(sidecar_path(store))
    rebuilt = load_cache(store)
    assert rebuilt is not None and not rebuilt.has_overlay
    from stitchgraph.core.reach import LIVENESS_RELATIONS
    assert patched_reach == rebuilt.reachable(seeds, LIVENESS_RELATIONS)
    assert patched_many == rebuilt.reachable_many([seeds, {"cyc.py::a"}],
                                                  LIVENESS_RELATIONS)
    assert patched_fan_in == rebuilt.fan_in(LIVENESS_RELATIONS)
    assert "app.py::fresh" in patched_reach  # the NEW node, via its overlay row
    assert "util.py::spare" in patched_reach  # the new call target
    store.close()


def test_scc_falls_back_on_patched_cache(tmp_path):
    _project(tmp_path)
    store = _warm(tmp_path)
    (tmp_path / "app.py").write_text(
        "from util import helper\n\ndef main():\n    return helper() + 1\n")
    reindex_incremental(store, str(tmp_path), {"app.py"})
    assert load_cache(store).has_overlay
    comps = strongly_connected_components(store)
    assert any({"cyc.py::a", "cyc.py::b"} <= set(c) for c in comps)  # correct via fallback
    store.close()


def test_broken_delta_chain_forces_full_rebuild(tmp_path):
    _project(tmp_path)
    store = _warm(tmp_path)
    (tmp_path / "app.py").write_text(
        "from util import helper\n\ndef main():\n    return helper() + 2\n")
    reindex_incremental(store, str(tmp_path), {"app.py"})
    gen = store.get_meta("generation")
    store.set_meta(f"adj_delta:{gen}", "FULL")  # tombstone the chain
    cache = load_cache(store)
    assert cache is not None and not cache.has_overlay  # rebuilt, still served
    assert "util.py::helper" in cache.reachable(
        {"app.py::main"},
        __import__("stitchgraph.core.reach",
                   fromlist=["LIVENESS_RELATIONS"]).LIVENESS_RELATIONS)
    store.close()
