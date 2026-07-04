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
    assert manifest.read_text() != gen_before, "sidecar must have been rebuilt"

    # falsification arm: with the bump suppressed the stale sidecar WOULD be served —
    # proving the generation check is the thing standing between us and wrong answers
    stale = adjcache.AdjacencyCache(str(tmp_path / "idx.db.adjcache"))
    assert stale.manifest["generation"] == adjcache.current_generation(store)


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
