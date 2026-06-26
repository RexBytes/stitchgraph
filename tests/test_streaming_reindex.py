"""Streaming reindex internals (v2): the AUTO-mode decision and the per-source dedup sink.

These pin the small but correctness-critical surface that `reindex(streaming=...)` adds, and
serve as the fast kill-signal for the streaming-focused mutation run
(`scripts/mutate.py src/stitchgraph/core/operations.py --only
_auto_stream,_StoreEdgeSink,_reindex_streaming,_dedup_edges ...`). The heavy byte-identity
checks live in tests/oracles/test_streaming_differential.py; this file adds the cases the
oracle's `:memory:` corpora don't reach (the AUTO threshold, on-disk vs in-memory).
"""
from __future__ import annotations

import textwrap

import stitchgraph as sg
from stitchgraph.core.model import Edge, Provenance, Relation
from stitchgraph.core.operations import _auto_stream, _dedup_edges


def _write(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))


def test_auto_stream_false_for_memory_store(tmp_path):
    """A :memory: store never auto-streams — streaming saves nothing when rows live in RAM."""
    _write(tmp_path, {f"m{i}.py": "def f():\n    return 1\n" for i in range(5)})
    with sg.Store(":memory:") as store:
        assert _auto_stream(str(tmp_path), store) is False


def test_auto_stream_false_for_small_ondisk_repo(tmp_path):
    """An on-disk store below the file threshold uses the (faster) in-memory path."""
    _write(tmp_path, {f"m{i}.py": "def f():\n    return 1\n" for i in range(5)})
    with sg.Store(str(tmp_path / "idx.db")) as store:
        assert _auto_stream(str(tmp_path), store) is False


def test_auto_stream_true_for_large_ondisk_repo(tmp_path, monkeypatch):
    """At/above the threshold, an on-disk store auto-streams. The threshold is lowered here
    so the test stays fast; the comparison itself (`n >= threshold`) is what's pinned."""
    import stitchgraph.core.operations as ops
    monkeypatch.setattr(ops, "_STREAM_AUTO_FILES", 4)
    _write(tmp_path, {f"m{i}.py": "def f():\n    return 1\n" for i in range(6)})
    with sg.Store(str(tmp_path / "idx.db")) as store:
        assert _auto_stream(str(tmp_path), store) is True


def test_auto_stream_counts_only_code_files(tmp_path, monkeypatch):
    """Non-code files don't count toward the streaming threshold (4 .py < threshold 5 even
    though there are 100 .txt files)."""
    import stitchgraph.core.operations as ops
    monkeypatch.setattr(ops, "_STREAM_AUTO_FILES", 5)
    _write(tmp_path, {f"m{i}.py": "def f():\n    return 1\n" for i in range(4)})
    _write(tmp_path, {f"data{i}.txt": "x\n" for i in range(100)})
    with sg.Store(str(tmp_path / "idx.db")) as store:
        assert _auto_stream(str(tmp_path), store) is False


def test_auto_stream_false_when_walk_errors(tmp_path, monkeypatch):
    """An OSError while counting files (permission denied / unwalkable tree) degrades to the
    in-memory path (return False), never propagates — pins the defensive except branch."""
    import stitchgraph.core.operations as ops
    monkeypatch.setattr(ops, "_STREAM_AUTO_FILES", 1)

    def _boom(self, pattern):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.rglob", _boom)
    with sg.Store(str(tmp_path / "idx.db")) as store:
        assert _auto_stream(str(tmp_path), store) is False


# --- _dedup_edges: absolute behaviour ---------------------------------------------------
# The differential oracle (full vs streaming) is BLIND to _dedup_edges mutations: both paths
# call it, so a bug changes both sides equally. These pin its output directly.

def _edge(src, dst_id, rel=Relation.CALLS, weight=1.0):
    return Edge(src=src, relation=rel, dst_symbol=dst_id.split(".")[-1] if dst_id else "x",
                dst_id=dst_id, weight=weight, provenance=Provenance.EXTRACTED)


def test_dedup_keeps_highest_weight_duplicate():
    """Two edges with the same (src, relation, dst_id) collapse to one — the HIGHER weight."""
    out = _dedup_edges([_edge("a::f", "a::g", weight=0.3), _edge("a::f", "a::g", weight=0.9)])
    assert len(out) == 1
    assert out[0].weight == 0.9


def test_dedup_calls_subsumes_references_same_target():
    """A CALLS edge subsumes a REFERENCES to the same (src, dst): only CALLS survives."""
    out = _dedup_edges([
        _edge("a::f", "a::g", rel=Relation.CALLS),
        _edge("a::f", "a::g", rel=Relation.REFERENCES),
    ])
    assert len(out) == 1
    assert out[0].relation is Relation.CALLS


def test_dedup_drops_references_self_loop_keeps_calls_self_loop():
    """A REFERENCES self-loop is meaningless and dropped; a recursive CALLS self-loop is kept."""
    out = _dedup_edges([
        _edge("a::f", "a::f", rel=Relation.REFERENCES),
        _edge("a::r", "a::r", rel=Relation.CALLS),
    ])
    rels = {(e.src, e.relation) for e in out}
    assert ("a::r", Relation.CALLS) in rels
    assert ("a::f", Relation.REFERENCES) not in rels


def test_dedup_keeps_all_holes():
    """Unresolved edges (dst_id is None) are distinct reference sites — all kept, never merged."""
    holes = [Edge(src="a::f", relation=Relation.CALLS, dst_symbol="missing", dst_id=None)
             for _ in range(3)]
    out = _dedup_edges(holes)
    assert sum(1 for e in out if e.dst_id is None) == 3


def _snapshot(store):
    nodes = sorted((r["id"], r["kind"], r["name"], r["roles"] or "")
                   for r in store.conn.execute("SELECT id, kind, name, roles FROM nodes"))
    edges = sorted((r["src"], r["relation"], r["dst_symbol"] or "", r["dst_id"] or "")
                   for r in store.conn.execute(
                       "SELECT src, relation, dst_symbol, dst_id FROM edges"))
    stale = {c["id"] for c in (sg.find_stale(store).result or [])}
    return nodes, edges, stale


def test_streaming_equals_full_ondisk(tmp_path):
    """On-disk streaming reindex == in-memory reindex (the path AUTO would pick), exercising
    the sink's per-source dedup + the final store dedup on a Python tree with inheritance,
    duplicate call sites, and an exported re-export."""
    src = tmp_path / "proj"
    _write(src, {
        "pkg/__init__.py": '__all__ = ["Widget"]\nfrom pkg.core import Widget\n',
        "pkg/core.py": (
            "class Base:\n    def hook(self):\n        return self.run()\n"
            "    def run(self):\n        return helper()\n\n"
            "class Widget(Base):\n    def run(self):\n        return helper() + helper()\n\n"
            "def helper():\n    return 1\n\ndef dead():\n    return 0\n"),
        "pkg/cli.py": "from pkg import core\ndef main():\n    return core.Widget().hook()\n",
    })
    with sg.Store(str(tmp_path / "full.db")) as full:
        rf = sg.reindex(full, str(src), streaming=False)
        fn, fe, fs = _snapshot(full)
    with sg.Store(str(tmp_path / "stream.db")) as stream:
        rs = sg.reindex(stream, str(src), streaming=True)
        sn, se, ss = _snapshot(stream)
    assert fn == sn
    assert fe == se
    assert fs == ss
    # The reported file/node/hole counts must match too (pins the streaming result meta,
    # e.g. the `files` set comprehension in _reindex_streaming).
    assert rf.meta == rs.meta
