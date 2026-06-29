"""find_similar: token-similarity retrieval over the graph.

Mutation meta-oracle (`scripts/mutate.py src/stitchgraph/core/similar.py`): 29/32 mutants killed by
this file + test_find_similar_structure.py. The 3 survivors are JUSTIFIED EQUIVALENT, not test gaps
(IDEAS §5d):
  - `_cosine` `not a or not b` -> `and`: when exactly one bag is empty the function still returns
    0.0 via the downstream `if dot == 0` guard, so the two forms are observationally identical.
  - `_dot_cos` `zip(..., strict=False)` -> `strict=True`: identical for the equal-length vectors the
    embedder contract guarantees; differs only on ragged (contract-violating) embeddings, where
    `strict=False` is the deliberate defensive choice.
  - `_python_fn_fingerprints` `not sep or not .py` -> `and`: the extra-permissive branch only lets a
    non-Python (or sep-less) id through to `fingerprint_source`, which then yields nothing for it —
    so no spurious fingerprint is produced either way.
"""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg
from stitchgraph.core.similar import tokenise


def test_tokenise_splits_identifiers():
    assert "sql" in tokenise("parse_sql_query")
    assert "resolver" in tokenise("SqlResolver")
    assert "self" not in tokenise("self.value")  # stopword dropped


def _project(root: Path) -> sg.Store:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text(
        "def parse_sql_query(text):\n"
        '    "Parse a SQL query and extract the table names."\n'
        "    return tables(text)\n\n"
        "def tables(text):\n"
        "    return []\n\n"
        "def render_widget(w):\n"
        '    "Draw a UI widget on screen."\n'
        "    return str(w)\n"
    )
    store = sg.Store(":memory:")
    sg.reindex(store, str(root))
    return store


def test_find_similar_ranks_relevant_first(tmp_path):
    with _project(tmp_path) as store:
        res = sg.find_similar(store, "extract tables from a sql statement", limit=3)
        assert res.ok
        assert res.result[0]["id"].endswith("::parse_sql_query")


def test_find_similar_empty_query_refuses(tmp_path):
    with _project(tmp_path) as store:
        res = sg.find_similar(store, "!!! ???", limit=3)
        assert not res.ok and res.needs_review


def test_pluggable_dense_embedder(tmp_path):
    """Injecting a dense embedder switches find_similar to cosine over vectors."""
    from stitchgraph.core import similar

    # A deterministic fake embedder: 2-d vector from two keyword counts.
    def fake(texts):
        out = []
        for t in texts:
            tl = t.lower()
            out.append([float(tl.count("sql") + tl.count("table")),
                        float(tl.count("draw") + tl.count("widget"))])
        return out

    store = _project(tmp_path)
    try:
        similar.set_embedder(fake)
        res = sg.find_similar(store, "extract tables from a sql statement", limit=3)
        assert res.ok
        assert res.result[0]["id"].endswith("::parse_sql_query")
    finally:
        similar.set_embedder(None)
        store.close()


def test_dense_ranks_strictly_and_drops_nonpositive(tmp_path):
    """Pin the dense path's sort direction and `> 0` filter with a STRICT, tie-free ranking.

    IDEAS §5d hardening: the older dense test tied at the top (two nodes both scored 1.0), so the
    `reverse=True` and `> 0` mutants survived. Here a 3-axis fake embedder gives parse_sql_query a
    strictly higher score than `tables`, and render_widget exactly 0 — so order and the filter are
    both observable.
    """
    from stitchgraph.core import similar

    def fake(texts):
        # orthogonal axes counted on the JOINED-TOKEN text (so the `... or n.name` fallback,
        # if flipped to `and`, changes the scores and is caught too).
        out = []
        for t in texts:
            tl = t.lower()
            out.append([float(tl.count("sql")), float(tl.count("table")), float(tl.count("draw"))])
        return out

    with _project(tmp_path) as store:
        try:
            similar.set_embedder(fake)
            res = similar.find_similar(store, "sql table table", limit=10)
        finally:
            similar.set_embedder(None)
    ids = [nid.rsplit("::", 1)[-1] for nid, _ in res]
    scores = [s for _, s in res]
    assert ids[0] == "parse_sql_query"            # strict winner (kills reverse=True -> False)
    assert "tables" in ids
    assert "render_widget" not in ids             # exactly-zero cosine dropped (kills `> 0` flip)
    assert scores == sorted(scores, reverse=True)  # descending
    assert scores[0] > scores[1]                   # genuinely strict — no top tie


def test_dense_tolerates_zero_norm_vectors(tmp_path):
    """A zero-magnitude embedding (query OR a node) must not divide-by-zero in `_dot_cos`.

    Pins the two `... or 1.0` norm guards: flipped to `and`, a zero norm becomes 0.0 and the
    cosine raises ZeroDivisionError instead of degrading to 0.0.
    """
    from stitchgraph.core import similar

    def node_zero(texts):  # query real; the render node embeds to all-zeros
        out = [[1.0, 0.0]]
        out += [[0.0, 0.0] if "draw" in t.lower() else [1.0, 0.0] for t in texts[1:]]
        return out

    def query_zero(texts):  # the query itself embeds to all-zeros
        return [[0.0, 0.0]] + [[1.0, 0.0] for _ in texts[1:]]

    with _project(tmp_path) as store:
        try:
            similar.set_embedder(node_zero)
            res = similar.find_similar(store, "anything", limit=10)   # must not raise (nb guard)
            assert "render_widget" not in [nid.rsplit("::", 1)[-1] for nid, _ in res]
            similar.set_embedder(query_zero)
            assert similar.find_similar(store, "anything", limit=10) == []  # na guard, no raise
        finally:
            similar.set_embedder(None)


def test_model2vec_autoload_is_offline_safe_and_once(tmp_path, monkeypatch):
    """Pin the model2vec auto-load path without a network, using a fake module + fake config.

    Covers: the success branch wires an embedder and picks the configured-or-default model name
    (`embed_model or "<default>"`); the load is attempted at most once (the `_M2V_TRIED` latch);
    and on import failure it returns False and stays on the token path (so find_similar never calls
    `_dense` with no embedder). IDEAS §5d hardening.
    """
    import sys
    import types

    from stitchgraph.core import similar

    names: list[str] = []

    fake_mod = types.ModuleType("model2vec")

    class _FakeStatic:
        @staticmethod
        def from_pretrained(name):
            names.append(name)

            class _M:
                def encode(self, texts):
                    class _Arr(list):
                        def tolist(self):
                            return [[float(len(t)), 1.0] for t in self]
                    return _Arr(list(texts))
            return _M()

    fake_mod.StaticModel = _FakeStatic

    # Build the index BEFORE patching load_config (reindex calls load_config(path)).
    store = _project(tmp_path)
    # _try_model2vec calls load_config() with no args; keep it arg-tolerant so reindex-style
    # call sites would still work. embed_model=None -> exercises the `or <default>` arm.
    cfg = types.SimpleNamespace(embed_model=None)
    monkeypatch.setattr("stitchgraph.core.config.load_config", lambda *a, **k: cfg, raising=False)
    monkeypatch.setattr(similar, "_EMBEDDER", None, raising=False)
    monkeypatch.setattr(similar, "_M2V_TRIED", False, raising=False)
    try:
        # Success path: fake module importable.
        monkeypatch.setitem(sys.modules, "model2vec", fake_mod)
        assert similar._try_model2vec() is True
        assert similar._EMBEDDER is not None
        assert similar._M2V_TRIED is True                  # latch set (kills `_M2V_TRIED = True` flip)
        assert names == ["minishlab/potion-base-8M"]       # `embed_model or default` (kills `and`)

        # already-loaded fast path: with an embedder live, _try returns True and does NOT reload.
        loaded = len(names)
        assert similar._try_model2vec() is True            # kills `if _EMBEDDER is not None: return False`
        assert len(names) == loaded                        # no second model load

        # find_similar auto-loads via the OR arm when no embedder is registered yet.
        monkeypatch.setattr(similar, "_EMBEDDER", None, raising=False)
        monkeypatch.setattr(similar, "_M2V_TRIED", False, raising=False)
        res = sg.find_similar(store, "parse a sql query", limit=3)
        assert res.ok
        assert similar._EMBEDDER is not None               # auto-load fired (kills the `or`->`and`)

        # Failure path: no model2vec module -> False, latched, token path stays safe.
        loads_before = len(names)
        monkeypatch.setattr(similar, "_EMBEDDER", None, raising=False)
        monkeypatch.setattr(similar, "_M2V_TRIED", False, raising=False)
        monkeypatch.delitem(sys.modules, "model2vec", raising=False)
        monkeypatch.setattr("builtins.__import__",
                            _blocking_import("model2vec", __import__), raising=False)
        assert similar._try_model2vec() is False           # import failed (kills except `return True`)
        assert similar._M2V_TRIED is True                  # latch still set on failure
        assert similar._EMBEDDER is None
        assert similar._try_model2vec() is False           # second call short-circuits on the latch
        assert len(names) == loads_before                  # no model load on the failure path
    finally:
        store.close()


def test_model2vec_latch_initialises_unset():
    # The module-level once-latch must start False — if it initialised True, model2vec auto-load
    # could never fire (the guard would be pre-tripped). Pins the `_M2V_TRIED = False` literal.
    import importlib

    from stitchgraph.core import similar
    mod = importlib.reload(similar)
    try:
        assert mod._M2V_TRIED is False
        assert mod._EMBEDDER is None
    finally:
        mod.set_embedder(None)


def _blocking_import(blocked: str, real):
    def _imp(name, *a, **k):
        if name == blocked:
            raise ImportError(f"blocked: {blocked}")
        return real(name, *a, **k)
    return _imp
