"""v3.42.0: sampled transitive fan-in (reach.transitive_fan_in_estimate) — the
sidecar hub metric past the GraphBLAS closure's node cap.

Contracts: EXACT when the sample budget covers the graph (matching a
brute-force distinct-ancestor count and, where installed, the GraphBLAS
closure); deterministic across calls on one index state; honestly flagged
`exact=False` when sampling; `reach_hits` is pinned to the set-materialising
`reachable_many` it shares its sweep with; orient dispatches to it past the
cap and names the metric truthfully.
"""
from __future__ import annotations

import textwrap

import pytest

import stitchgraph as sg
from stitchgraph.core import reach
from stitchgraph.core.adjcache import load_cache
from stitchgraph.core.reach import LIVENESS_RELATIONS

np = pytest.importorskip("numpy")


CORPUS = {
    "chain.py": """
        def leaf():
            return 1

        def mid():
            return leaf()

        def top():
            return mid()

        def top2():
            return mid()
    """,
    "cyc.py": """
        def a():
            return b()

        def b():
            return a()

        def entry():
            return a()
    """,
}


def _index(tmp_path):
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    for rel, content in CORPUS.items():
        (root / rel).write_text(textwrap.dedent(content))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    return store


def _brute_force_ancestors(store):
    """Reference: per node, |distinct other nodes reaching it| over confident
    LIVENESS edges — a per-node reverse BFS in plain Python."""
    from collections import defaultdict, deque
    rels = {r.value for r in LIVENESS_RELATIONS}
    radj = defaultdict(set)
    for src, rel, dst, _w in store.iter_resolved(confident_only=True):
        if rel in rels:
            radj[dst].add(src)
    out = {}
    for nid in store.all_node_ids():
        seen, dq = set(), deque([nid])
        while dq:
            for p in radj.get(dq.popleft(), ()):
                if p not in seen:
                    seen.add(p)
                    dq.append(p)
        seen.discard(nid)  # distinct OTHER ancestors (offdiag)
        if seen:
            out[nid] = float(len(seen))
    return out


def test_exact_tier_matches_brute_force(tmp_path):
    store = _index(tmp_path)
    est = reach.transitive_fan_in_estimate(store)
    assert est is not None
    counts, exact = est
    assert exact is True
    assert counts == _brute_force_ancestors(store)
    # the corpus exercises both a chain (leaf has 3 distinct ancestors) and a
    # cycle (a/b reach each other; entry reaches both)
    assert counts["chain.py::leaf"] == 3.0
    assert counts["cyc.py::a"] >= 2.0


def test_exact_tier_matches_graphblas_closure(tmp_path):
    from stitchgraph.core import algebra
    if not algebra.HAS_GRAPHBLAS:
        pytest.skip("graphblas not installed")
    store = _index(tmp_path)
    counts, exact = reach.transitive_fan_in_estimate(store)
    assert exact
    tfi = {k: float(v) for k, v in algebra.transitive_fan_in(store).items()}
    assert counts == tfi


def test_deterministic_and_sampled_flagging(tmp_path):
    store = _index(tmp_path)
    a = reach.transitive_fan_in_estimate(store)
    b = reach.transitive_fan_in_estimate(store)
    assert a == b
    counts, exact = reach.transitive_fan_in_estimate(store, samples=3)
    assert exact is False  # budget below n: estimates, and says so
    assert all(v > 0 for v in counts.values())


def test_reach_hits_pinned_to_reachable_many(tmp_path):
    """reach_hits must agree with counting membership across reachable_many's
    materialised sets — same sweep, popcount vs set path."""
    store = _index(tmp_path)
    cache = load_cache(store)
    assert cache is not None
    groups = [{nid} for nid in cache.ids]
    hits = cache.reach_hits(groups, LIVENESS_RELATIONS, True)
    sets = cache.reachable_many(groups, LIVENESS_RELATIONS, True)
    expected = np.zeros(cache.n, np.int64)
    idx = {nid: i for i, nid in enumerate(cache.ids)}
    for s in sets:
        for nid in s:
            expected[idx[nid]] += 1
    assert (hits == expected).all()


def test_orient_dispatch_past_cap(tmp_path, monkeypatch):
    """When the exact closure refuses (past its node cap / no GraphBLAS), orient
    must serve the sidecar estimator — named `transitive_fan_in` when the budget
    made it exact, `transitive_fan_in_sampled` when it estimated."""
    from stitchgraph.core import algebra, operations
    store = _index(tmp_path)
    monkeypatch.setattr(algebra, "transitive_fan_in", lambda *a, **k: {})
    r = sg.orient(store)
    assert r.meta["hub_metric"] == "transitive_fan_in"  # exact via estimator
    assert any(h["id"] == "chain.py::leaf" for h in r.result["top_hubs"])

    monkeypatch.setattr(operations, "transitive_fan_in_estimate",
                        lambda s, **k: ({"chain.py::leaf": 12.5}, False))
    r2 = sg.orient(store)
    assert r2.meta["hub_metric"] == "transitive_fan_in_sampled"


def test_no_sidecar_degrades_to_confident_fan_in(tmp_path, monkeypatch):
    from stitchgraph.core import adjcache, algebra
    store = _index(tmp_path)
    monkeypatch.setattr(algebra, "transitive_fan_in", lambda *a, **k: {})
    monkeypatch.setattr(adjcache, "load_cache", lambda s, **kw: None)
    r = sg.orient(store)
    assert r.meta["hub_metric"] == "confident_fan_in"
