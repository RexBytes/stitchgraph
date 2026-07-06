"""The mmapped adjacency sidecar (core/adjcache.py): build/staleness contract and
result equivalence against the pure-Python reference sweeps.

The sidecar is memoized derivation — SQLite stays authoritative, the sidecar must
be (a) impossible to read stale (generation contract) and (b) bit-equal to the
reference implementation on every op it accelerates. These tests pin both. The
corpus deliberately mixes provenance (homonym methods -> AMBIGUOUS widening arms)
so the confident_only path has something to filter.
"""
from __future__ import annotations

import textwrap

import pytest

import stitchgraph as sg
from stitchgraph.core import adjcache, reach
from stitchgraph.core.model import NodeKind

np = pytest.importorskip("numpy")  # sidecar is numpy-gated; without it these paths vanish


CORPUS = {
    "a.py": """
        class Base:
            def work(self):
                return 1

        class Sub(Base):
            def work(self):
                return 2
    """,
    "b.py": """
        from a import Base

        def run(obj):
            return obj.work()

        def helper():
            return run(Base())

        def orphan():
            return 3
    """,
    "c.py": """
        import b

        def main():
            return b.helper()
    """,
}


def _index(tmp_path, monkeypatch=None):
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    for rel, content in CORPUS.items():
        (root / rel).write_text(textwrap.dedent(content))
    store = sg.Store(str(tmp_path / "idx.db"))
    assert sg.reindex(store, str(root)).ok
    return store


def _seeds(store):
    # modules + the one real entry point: module nodes alone don't reach functions
    # (defining is not calling), and the tests need a function-level closure.
    return sorted(n.id for n in store.nodes_by_kind(NodeKind.MODULE)) + ["c.py::main"]


@pytest.fixture(autouse=True)
def _fresh_memos():
    """Sidecar memos are per-process; tests must not see another test's entries."""
    adjcache._loaded.clear()
    adjcache._build_failed.clear()
    yield
    adjcache._loaded.clear()
    adjcache._build_failed.clear()


@pytest.fixture
def no_cache(monkeypatch):
    """Force every sweep onto its pre-sidecar path (the reference implementation)."""
    monkeypatch.setattr(adjcache, "load_cache", lambda store, **kw: None)


def test_sidecar_built_lazily_on_first_sweep(tmp_path):
    store = _index(tmp_path)
    side = tmp_path / "idx.db.adjcache"
    assert not side.exists(), "reindex itself must NOT build (constant-memory gate)"
    reach.reachable_from(store, _seeds(store))
    assert (side / "manifest.json").exists()


def test_memory_store_never_builds():
    store = sg.Store(":memory:")
    assert adjcache.load_cache(store) is None


def test_equivalence_with_pure_python(tmp_path, monkeypatch):
    """Every accelerated op must return exactly the reference result — same sets,
    same counts — including the confident_only (EXTRACTED-edge) closure."""
    store = _index(tmp_path)
    seeds = _seeds(store)
    target = seeds[0]

    def snapshot():
        return {
            "reach": reach.reachable_from(store, seeds),
            "confident": reach.reachable_from(store, seeds, confident_only=True),
            "reverse": reach.reverse_reachable_from(store, {target}),
            "fan_in": dict(reach.fan_in(store)),
            "fan_out": dict(reach.fan_out(store)),
        }

    with monkeypatch.context() as m:
        m.setattr(adjcache, "load_cache", lambda s, **kw: None)
        reference = snapshot()
    accelerated = snapshot()
    assert adjcache.load_cache(store) is not None, "sidecar must actually be in play"
    assert accelerated == reference
    # the corpus must exercise the filter: the confident closure is a strict subset
    assert reference["confident"] < reference["reach"]


def test_stale_after_replace_file_falls_back_then_rebuilds(tmp_path):
    """replace_file bumps the generation: the old sidecar must be refused, the
    sweep must still be CORRECT via fallback/rebuild, and the rebuilt sidecar
    reflects the edit."""
    store = _index(tmp_path)
    seeds = _seeds(store)
    before = reach.reachable_from(store, seeds)
    manifest = tmp_path / "idx.db.adjcache" / "manifest.json"
    gen_before = manifest.read_text()

    # cut c.py's dependency on b: main() no longer reaches helper/run
    from stitchgraph.core.extract import extract_project
    root = tmp_path / "src"
    (root / "c.py").write_text("def main():\n    return 4\n")
    nodes, edges = extract_project(str(root))
    c_nodes = [n for n in nodes if n.id.startswith("c.py::")]
    c_edges = [e for e in edges if e.src.startswith("c.py::")]
    store.replace_file("c.py", c_nodes, c_edges)

    after = reach.reachable_from(store, seeds)
    assert "b.py::helper" in before
    assert after < before, "the severed dependency must disappear from the closure"
    # v3.40.0: a replace_file no longer forces a rebuild — the loader may PATCH a
    # row overlay from the captured delta (manifest generation then stays put) or
    # rebuild (manifest changes). Either path must serve the corrected closure,
    # which the assertion above just proved; pin that one of the two happened.
    from stitchgraph.core.adjcache import load_cache
    cache = load_cache(store)
    assert cache is not None
    assert cache.has_overlay or manifest.read_text() != gen_before, \
        "sidecar must have been patched or rebuilt"

    # falsification arm: the raw on-disk sidecar without the delta chain is
    # genuinely STALE (under the v3.40.0 patch path its manifest legitimately
    # stays at the old generation) — the loader's generation check + delta walk
    # is the thing standing between us and wrong answers. Prove it: the raw
    # cache still reaches the severed dependency; the loader-served one doesn't.
    stale = adjcache.AdjacencyCache(str(tmp_path / "idx.db.adjcache"))
    if stale.manifest["generation"] != adjcache.current_generation(store):
        from stitchgraph.core.reach import LIVENESS_RELATIONS
        assert "b.py::helper" in stale.reachable(seeds, LIVENESS_RELATIONS)
        assert "b.py::helper" not in cache.reachable(seeds, LIVENESS_RELATIONS)


def test_config_disable(tmp_path):
    store = _index(tmp_path)
    (tmp_path / "src" / "stitchgraph.toml").write_text(
        "[index]\nadjacency_cache = false\n")
    reach.reachable_from(store, _seeds(store))
    assert not (tmp_path / "idx.db.adjcache").exists()


def test_numpy_absent_degrades_silently(tmp_path, monkeypatch):
    store = _index(tmp_path)
    monkeypatch.setattr(adjcache, "_np", None)
    assert adjcache.load_cache(store) is None
    assert reach.reachable_from(store, _seeds(store))  # pure path still answers


def test_failed_build_memoised_per_generation(tmp_path, monkeypatch):
    """A build failure (read-only fs) must cost one attempt per index state, not
    one 74-second attempt per sweep."""
    store = _index(tmp_path)
    calls = []

    def failing_build(s):
        calls.append(1)
        return False

    monkeypatch.setattr(adjcache, "build_cache", failing_build)
    assert adjcache.load_cache(store) is None
    assert adjcache.load_cache(store) is None
    assert len(calls) == 1
    store.bump_generation()  # new state -> one fresh attempt allowed
    assert adjcache.load_cache(store) is None
    assert len(calls) == 2


def test_tampered_manifest_refused(tmp_path):
    store = _index(tmp_path)
    reach.reachable_from(store, _seeds(store))
    side = tmp_path / "idx.db.adjcache"
    manifest = side / "manifest.json"
    manifest.write_text(manifest.read_text().replace(
        f'"generation": "{adjcache.current_generation(store)}"', '"generation": "999"'))
    adjcache._loaded.clear()
    assert adjcache.load_cache(store, build=False) is None


def test_scc_articulation_scan_equivalence(tmp_path, monkeypatch):
    """SCC, articulation points, orient's confident fan-in, and the FULL scan result
    must be identical with and without the sidecar — including component member
    order and issue order (the scan differential contract)."""
    root = tmp_path / "src"
    root.mkdir()
    for rel, content in CORPUS.items():
        (root / rel).write_text(textwrap.dedent(content))
    # add a mutual-recursion cycle and a self-loop so SCC has real work
    (root / "d.py").write_text(textwrap.dedent("""
        def ping(n):
            return pong(n - 1) if n else 0

        def pong(n):
            return ping(n - 1) if n else 1

        def rec(n):
            return rec(n - 1) if n else 2
    """))
    store = sg.Store(str(tmp_path / "idx.db"))
    assert sg.reindex(store, str(root)).ok

    def snapshot():
        return {
            "scc": reach.strongly_connected_components(store),
            "aps": reach.articulation_points(store),
            "scan": sg.scan(store).result,
        }

    with monkeypatch.context() as m:
        m.setattr(adjcache, "load_cache", lambda s, **kw: None)
        reference = snapshot()
    accelerated = snapshot()
    assert adjcache.load_cache(store) is not None
    assert accelerated == reference
    assert any("ping" in m for c in reference["scc"] for m in c), "cycle must be found"


def test_god_object_review_cap(monkeypatch):
    """Above the cap, hedged god-object flags are cut to the top-N by (confidence
    desc, node) with the suppression reported; confident flags always survive."""
    from stitchgraph.core import operations as ops
    from stitchgraph.core.model import Edge, Node, NodeKind, Provenance, Relation

    store = sg.Store(":memory:")
    def fn(name):
        store.add_node(Node(id=f"m.py::{name}", name=name, kind=NodeKind.FUNCTION,
                            location="m.py:1:0"))
    # three hedged god objects (ambiguous in/out >= 5, confident < 5) with distinct
    # confident shares, one confident god object (extracted in/out >= 5)
    targets = [f"g{i}" for i in range(3)] + ["solid"]
    for t in targets:
        fn(t)
    callers = [f"c{i}" for i in range(8)]
    callees = [f"e{i}" for i in range(8)]
    for x in callers + callees:
        fn(x)
    for gi, t in enumerate(targets):
        conf_n = gi if t != "solid" else 6  # g0:0, g1:1, g2:2 confident arms
        for i in range(6):
            prov = Provenance.EXTRACTED if i < conf_n else Provenance.AMBIGUOUS
            store.add_edge(Edge(src=f"m.py::{callers[i]}", relation=Relation.CALLS,
                                dst_symbol=t, dst_id=f"m.py::{t}", provenance=prov))
            store.add_edge(Edge(src=f"m.py::{t}", relation=Relation.CALLS,
                                dst_symbol=callees[i], dst_id=f"m.py::{callees[i]}",
                                provenance=prov))
    store.commit()

    monkeypatch.setattr(ops, "_GOD_REVIEW_CAP", 2)
    r = sg.scan(store)
    gods = [i for i in r.result if i["kind"] == "god_object"]
    hedged = [g["node"] for g in gods if g["needs_review"]]
    solid = [g["node"] for g in gods if not g["needs_review"]]
    assert solid == ["m.py::solid"], gods
    # top 2 hedged by confidence desc = g2, g1; g0 suppressed
    assert sorted(hedged) == ["m.py::g1", "m.py::g2"]
    assert r.meta.get("god_objects_suppressed") == 1
    assert any("suppressed" in reason for reason in r.review_reasons)


def test_pure_mode_forces_reference_paths(tmp_path, monkeypatch):
    """STITCHGRAPH_PURE=1 must disable the sidecar (and GraphBLAS) while every op
    still answers — identically — via the reference implementations."""
    from stitchgraph.core import purity
    from stitchgraph.core import reach as reach_mod

    store = _index(tmp_path)
    seeds = _seeds(store)
    fast = reach_mod.reachable_from(store, seeds)
    assert (tmp_path / "idx.db.adjcache").exists()

    monkeypatch.setenv("STITCHGRAPH_PURE", "1")
    assert purity.pure_mode()
    assert adjcache.load_cache(store) is None, "pure mode must refuse the sidecar"
    assert reach_mod._graphblas() is None, "pure mode must refuse GraphBLAS"
    assert reach_mod.reachable_from(store, seeds) == fast, "identical results"

    monkeypatch.delenv("STITCHGRAPH_PURE")
    assert adjcache.load_cache(store) is not None
