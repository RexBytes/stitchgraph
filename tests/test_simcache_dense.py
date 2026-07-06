"""v3.38.0: dense-embedder persistence in the similarity sidecar. Contracts:

- With a `cache_key`, node embeddings persist once and a query embeds ONLY the
  snippet (the counting-embedder test is the whole point of the feature).
- Results match the recompute-per-query reference path (same texts, same cosine).
- The manifest pins the vector-space identity: a different key rebuilds, a stale
  generation rebuilds, a keyless embedder never persists.
"""

from __future__ import annotations

import pytest

import stitchgraph as sg
from stitchgraph.core import similar as sim
from stitchgraph.core.simcache import dense_path

np = pytest.importorskip("numpy")


class CountingEmbedder:
    """Deterministic 4-dim 'model': hash-bucket token counts. Counts CALLS."""

    def __init__(self):
        self.calls = 0
        self.texts_embedded = 0

    def __call__(self, texts):
        self.calls += 1
        self.texts_embedded += len(texts)
        out = []
        for t in texts:
            v = [0.0, 0.0, 0.0, 0.0]
            for tok in t.split():
                v[hash(tok) % 4] += 1.0
            out.append(v)
        return out


@pytest.fixture
def project(tmp_path):
    (tmp_path / "auth.py").write_text(
        "def check_password(pw):\n    return hash_secret(pw)\n\n"
        "def hash_secret(pw):\n    return 1\n")
    (tmp_path / "render.py").write_text(
        "def render_template(name):\n    return name\n")
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_embedder():
    yield
    sim.set_embedder(None)


def test_dense_sidecar_persists_and_embeds_only_snippet(project, tmp_path):
    db = str(tmp_path / "g.db")
    emb = CountingEmbedder()
    sim.set_embedder(emb, cache_key="test-model@1")
    with sg.Store(db) as store:
        sg.reindex(store, str(project))
        r1 = sg.find_similar(store, "password hashing check")
        assert r1.ok and r1.result
        assert dense_path(store) is not None
        import os
        assert os.path.isdir(dense_path(store))  # persisted
        built_texts = emb.texts_embedded
        r2 = sg.find_similar(store, "password hashing check")
        # the second query embedded exactly ONE text (the snippet)
        assert emb.texts_embedded == built_texts + 1
        assert r2.result == r1.result


def test_dense_results_match_reference_path(project, tmp_path):
    db = str(tmp_path / "g.db")
    emb = CountingEmbedder()
    with sg.Store(db) as store:
        sg.reindex(store, str(project))
        sim.set_embedder(emb, cache_key="test-model@1")
        cached = sg.find_similar(store, "render template name").result
        sim.set_embedder(emb)  # keyless -> reference recompute path
        reference = sg.find_similar(store, "render template name").result
    assert [r["id"] for r in cached] == [r["id"] for r in reference]
    for a, b in zip(cached, reference, strict=True):
        assert abs(a["score"] - b["score"]) < 1e-5


def test_keyless_embedder_never_persists(project, tmp_path):
    db = str(tmp_path / "g.db")
    sim.set_embedder(CountingEmbedder())  # no cache_key
    with sg.Store(db) as store:
        sg.reindex(store, str(project))
        assert sg.find_similar(store, "check password").ok
        import os
        assert not os.path.exists(f"{db}.simcache-dense")


def test_stale_generation_and_model_switch_rebuild(project, tmp_path):
    db = str(tmp_path / "g.db")
    emb = CountingEmbedder()
    sim.set_embedder(emb, cache_key="test-model@1")
    with sg.Store(db) as store:
        sg.reindex(store, str(project))
        sg.find_similar(store, "check password")
        after_build = emb.texts_embedded
        # a reindex bumps the generation -> next query rebuilds the matrix
        (project / "extra.py").write_text("def brand_new():\n    return 1\n")
        sg.reindex(store, str(project))
        sg.find_similar(store, "check password")
        assert emb.texts_embedded > after_build + 1  # re-embedded the nodes
        # switching vector spaces (new key) also rebuilds — never mixes spaces
        emb2 = CountingEmbedder()
        sim.set_embedder(emb2, cache_key="other-model@9")
        r = sg.find_similar(store, "check password")
        assert r.ok
        assert emb2.texts_embedded > 1  # rebuilt under the new identity
