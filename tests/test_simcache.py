"""The similarity sidecar (core/simcache.py): ranking equivalence against the
reference token-cosine path, plus the inherited staleness/config/degradation
contracts (the adjcache pattern)."""
from __future__ import annotations

import textwrap

import pytest

import stitchgraph as sg
from stitchgraph.core import simcache
from stitchgraph.core.similar import find_similar

np = pytest.importorskip("numpy")


def _index(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "http.py").write_text(textwrap.dedent('''
        class Session:
            def send_request(self, url):
                """Send an http request and return the response."""
                return build_payload(url)

        def build_payload(url):
            """Build the raw request payload body."""
            return url

        def render_template(name):
            """Render a template by substituting variables."""
            return name
    '''))
    store = sg.Store(str(tmp_path / "idx.db"))
    assert sg.reindex(store, str(root)).ok
    return store


@pytest.fixture(autouse=True)
def _fresh_memos():
    simcache._loaded.clear()
    simcache._build_failed.clear()
    yield
    simcache._loaded.clear()
    simcache._build_failed.clear()


def test_ranking_matches_reference(tmp_path, monkeypatch):
    """The sidecar must produce the reference path's ranking, with scores equal to
    the reference cosine (up to float summation order)."""
    store = _index(tmp_path)
    q = "send an http request and get the response"
    fast = find_similar(store, q, limit=5)
    assert (tmp_path / "idx.db.simcache").exists(), "lazy build must have happened"

    with monkeypatch.context() as m:
        m.setattr(simcache, "load_cache", lambda s, **kw: None)
        reference = find_similar(store, q, limit=5)
    assert [nid for nid, _ in fast] == [nid for nid, _ in reference]
    for (_, sf), (_, sr) in zip(fast, reference, strict=True):
        assert abs(sf - sr) < 1e-4
    assert fast[0][0].endswith("Session.send_request")


def test_stale_after_replace_file(tmp_path):
    store = _index(tmp_path)
    find_similar(store, "render a template", limit=3)
    manifest = tmp_path / "idx.db.simcache" / "manifest.json"
    before = manifest.read_text()

    from stitchgraph.core.extract import extract_project
    root = tmp_path / "src"
    (root / "http.py").write_text(
        "def parse_options(argv):\n    \"\"\"Parse command line options.\"\"\"\n"
        "    return argv\n")
    nodes, edges = extract_project(str(root))
    store.replace_file("http.py", [n for n in nodes if n.id.startswith("http.py::")],
                       [e for e in edges if e.src.startswith("http.py::")])

    hits = find_similar(store, "parse command line options", limit=3)
    assert hits and hits[0][0] == "http.py::parse_options"
    assert manifest.read_text() != before, "sidecar must have been rebuilt"
    stale_names = {nid for nid, _ in hits}
    assert not any("send_request" in n for n in stale_names), "old rows must be gone"


def test_config_disable_and_pure_mode(tmp_path, monkeypatch):
    store = _index(tmp_path)
    (tmp_path / "src" / "stitchgraph.toml").write_text(
        "[index]\nsimilarity_cache = false\n")
    find_similar(store, "send an http request", limit=3)
    assert not (tmp_path / "idx.db.simcache").exists()

    (tmp_path / "src" / "stitchgraph.toml").unlink()
    monkeypatch.setenv("STITCHGRAPH_PURE", "1")
    find_similar(store, "send an http request", limit=3)
    assert not (tmp_path / "idx.db.simcache").exists(), "pure mode must not build"


def test_find_component_uses_it(tmp_path):
    """find_component rides find_similar, so the sidecar serves it transparently."""
    store = _index(tmp_path)
    r = sg.find_component(store, "send an http request and return the response")
    assert r.ok and r.result[0]["name"] == "send_request"
    assert (tmp_path / "idx.db.simcache").exists()
