"""v3.39.0: the bit-parallel multi-source BFS. Contract: `reachable_many` is
byte-identical, per lane, to sequential `reachable_from` calls — over the
sidecar AND the fallback, past the 64-lane chunk boundary, and under
confident_only."""

from __future__ import annotations

import pytest

import stitchgraph as sg
from stitchgraph.core.reach import reachable_from, reachable_from_many

pytest.importorskip("numpy")


@pytest.fixture
def store(tmp_path):
    # A tangle: chains, a diamond, a cycle, an isolated island — enough shape
    # that lanes overlap, diverge, and terminate at different depths.
    for i in range(12):
        callee = f"f{(i * 3 + 1) % 12}" if i % 4 else "hub"
        (tmp_path / f"m{i}.py").write_text(
            f"def f{i}():\n    return {callee}()\n\n"
            f"def hub():\n    return f{(i + 5) % 12}()\n" if i == 0 else
            f"def f{i}():\n    return {callee}()\n")
    (tmp_path / "island.py").write_text("def alone():\n    return 1\n")
    db = str(tmp_path / "g.db")  # on-disk so the sidecar builds
    s = sg.Store(db)
    sg.reindex(s, str(tmp_path))
    yield s
    s.close()


def test_lanes_match_sequential(store):
    seeds = [{f"m{i}.py::f{i}"} for i in range(12)] + [{"island.py::alone"}]
    batched = reachable_from_many(store, seeds)
    for group, got in zip(seeds, batched, strict=True):
        assert got == reachable_from(store, group), group


def test_chunking_past_64_lanes(store):
    # 70 groups forces the recursive 64-lane chunking path.
    seeds = [{f"m{i % 12}.py::f{i % 12}"} for i in range(70)]
    batched = reachable_from_many(store, seeds)
    assert len(batched) == 70
    for group, got in zip(seeds, batched, strict=True):
        assert got == reachable_from(store, group)


def test_confident_only_and_unknown_seeds(store):
    seeds = [{"m1.py::f1"}, {"nope.py::ghost"}, set()]
    batched = reachable_from_many(store, seeds, confident_only=True)
    assert batched[0] == reachable_from(store, {"m1.py::f1"}, confident_only=True)
    assert batched[1] == set() and batched[2] == set()


def test_multi_seed_group(store):
    group = {"m1.py::f1", "m7.py::f7", "island.py::alone"}
    assert reachable_from_many(store, [group])[0] == reachable_from(store, group)
