"""Differential oracle for graph_diff (v3.0.0): stitchgraph's own source, indexed twice, must
diff to EQUIVALENT — no phantom node/edge deltas and no spurious body-shape changes on real code.

If this ever fails, the diff (or a fingerprint) is non-deterministic — exactly what an oracle is
for. Pure-Python source, so stdlib-only (runs in the core no-extras CI job).
"""
from __future__ import annotations

from pathlib import Path

import stitchgraph as sg
from stitchgraph.core import graphdiff

SRC = str(Path(__file__).resolve().parents[2] / "src" / "stitchgraph")


def _index() -> sg.Store:
    store = sg.Store(":memory:")
    sg.reindex(store, SRC)
    return store


def test_self_diff_is_equivalent_id_mode():
    a, b = _index(), _index()
    d = graphdiff.graph_diff(a, b, mode="id", body=True)
    assert d["equivalent"], {k: v for k, v in d.items() if v and k != "mode"}


def test_self_diff_is_equivalent_leaf_mode():
    a, b = _index(), _index()
    d = graphdiff.graph_diff(a, b, mode="leaf", body=True)
    assert d["equivalent"], {k: v for k, v in d.items() if v and k != "mode"}


def test_self_diff_has_no_body_changes():
    # every Python function fingerprints identically to itself across two independent indexes
    a, b = _index(), _index()
    d = graphdiff.graph_diff(a, b, mode="id", body=True)
    assert d["body_changed"] == []
