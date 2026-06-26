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
from stitchgraph.core.operations import _auto_stream


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
        sg.reindex(full, str(src), streaming=False)
        fn, fe, fs = _snapshot(full)
    with sg.Store(str(tmp_path / "stream.db")) as stream:
        sg.reindex(stream, str(src), streaming=True)
        sn, se, ss = _snapshot(stream)
    assert fn == sn
    assert fe == se
    assert fs == ss
