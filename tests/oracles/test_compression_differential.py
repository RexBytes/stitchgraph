"""Homonym-group edge compression differential oracle (research/20).

The same tree indexed with compression ON and OFF must give IDENTICAL answers
through every surface: the edges_all row multiset, the whole operation battery
(scan/orient/find_stale/find_holes/impact_of), the reachability sweeps, and the
adjacency sidecar's derived bytes. This is the arc's release gate: compression
is a pure representation change or it does not ship. Both reindex paths are
covered (in-memory AND streaming), plus the incremental path's convergence with
each.
"""
from __future__ import annotations

import textwrap

import stitchgraph as sg
from stitchgraph.core import reach
from stitchgraph.core.model import NodeKind


def _tree(tmp_path):
    """Homonym-dense corpus exercising every compression seam: name widening,
    inheritance overrides (subtree widening + name arms), CALLS/REFERENCES
    subsumption, an unresolved hole sharing a widened name, and a module-named
    function (the dedup-id collapse case)."""
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "base.py").write_text(textwrap.dedent("""
        class Base:
            def work(self):
                return 1

            def only_base(self):
                return self.work()
    """))
    (root / "sub.py").write_text(textwrap.dedent("""
        from base import Base

        class Sub(Base):
            def work(self):
                return 2
    """))
    (root / "callers.py").write_text(textwrap.dedent("""
        from base import Base

        def by_name(obj):
            return obj.work()

        def precise():
            b = Base()
            return b.work()

        def reference_too(obj):
            f = obj.work
            return f() or missing_thing()

        def work():
            return 3
    """))
    (root / "compute.py").write_text(textwrap.dedent("""
        def compute():
            return 4

        def uses():
            return compute() + work()
    """))
    for i in range(4):
        (root / f"m{i}.py").write_text(
            f"def helper_{i}(x):\n    return x.work()\n\ndef work():\n    return {i}\n")
    return root


def _index(tmp_path, name, compression, streaming=None):
    store = sg.Store(str(tmp_path / name))
    store.edge_compression = compression and store._compression_env_ok
    r = sg.reindex(store, str(tmp_path / "proj"), streaming=streaming)
    assert r.ok
    # reindex re-derives the gate from config (default on); re-pin the arm.
    return store


def _rows(store):
    return sorted(tuple(r) for r in store.conn.execute(
        """SELECT src, relation, dst_symbol, dst_id, weight, provenance,
                  location, source, file, name_based FROM edges_all"""))


def _holes(store):
    return sorted((e.src, e.relation.value, e.dst_symbol)
                  for e in store.unresolved_edges())


def _battery(store):
    seeds = sorted(n.id for n in store.nodes_by_kind(NodeKind.MODULE))
    return {
        "scan": sg.scan(store).result,
        "stale": sg.find_stale(store).result,
        "holes_op": sg.find_holes(store).result,
        "orient": sg.orient(store).result,
        "impact": sg.impact_of(store, "base.py::Base.work").result,
        "callers": sg.get_callers(store, "base.py::Base.work").result,
        "reach": reach.reachable_from(store, seeds),
        "rev": reach.reverse_reachable_from(store, {"base.py::Base.work"}),
        "fan_in": dict(reach.fan_in(store)),
        "fan_out": dict(reach.fan_out(store)),
        "scc": reach.strongly_connected_components(store),
    }


def test_in_memory_reindex_differential(tmp_path):
    _tree(tmp_path)
    on = sg.Store(str(tmp_path / "on.db"))
    off = sg.Store(str(tmp_path / "off.db"))
    off._compression_env_ok = False  # simulate the kill switch for the control arm
    assert sg.reindex(on, str(tmp_path / "proj"), streaming=False).ok
    assert sg.reindex(off, str(tmp_path / "proj"), streaming=False).ok
    assert on.conn.execute("SELECT COUNT(*) FROM edge_groups").fetchone()[0] > 0, \
        "the corpus must actually compress, or this oracle proves nothing"
    assert off.conn.execute("SELECT COUNT(*) FROM edge_groups").fetchone()[0] == 0
    assert _rows(on) == _rows(off)
    assert _holes(on) == _holes(off)
    assert _battery(on) == _battery(off)
    on.close()
    off.close()


def test_streaming_reindex_differential(tmp_path):
    _tree(tmp_path)
    on = sg.Store(str(tmp_path / "on.db"))
    off = sg.Store(str(tmp_path / "off.db"))
    off._compression_env_ok = False
    assert sg.reindex(on, str(tmp_path / "proj"), streaming=True).ok
    assert sg.reindex(off, str(tmp_path / "proj"), streaming=True).ok
    assert on.conn.execute("SELECT COUNT(*) FROM edge_groups").fetchone()[0] > 0
    assert _rows(on) == _rows(off)
    assert _holes(on) == _holes(off)
    assert _battery(on) == _battery(off)
    on.close()
    off.close()


def test_streaming_equals_in_memory_compressed(tmp_path):
    """The existing streaming-vs-in-memory byte-identity contract must survive
    with BOTH paths compressing (each compresses at a different stage: the sink
    per-source vs the bulk partition)."""
    _tree(tmp_path)
    mem = sg.Store(str(tmp_path / "mem.db"))
    stream = sg.Store(str(tmp_path / "stream.db"))
    assert sg.reindex(mem, str(tmp_path / "proj"), streaming=False).ok
    assert sg.reindex(stream, str(tmp_path / "proj"), streaming=True).ok
    assert _rows(mem) == _rows(stream)
    assert _holes(mem) == _holes(stream)
    assert _battery(mem) == _battery(stream)
    mem.close()
    stream.close()


def test_incremental_converges_with_compressed_reindex(tmp_path):
    """Edit loop on a compressed index: replace_file end-state == fresh compressed
    reindex, through rows AND battery (the arc's incremental contract). The edit
    REMOVES a homonym, so every compressed 'work' group must expand, narrow, and
    re-compress. `precise()` (the declared-type mixed key) is dropped from the
    corpus first: on any name-universe change the store's `_rewiden_resolved`
    demotes such a key's EXTRACTED row to an AMBIGUOUS arm — the documented,
    pre-existing under-claiming seam ("pre-widen provenance is not recoverable
    at the store layer"), verified identical in the flat world and orthogonal to
    compression."""
    from stitchgraph.core.operations import reindex_incremental
    root = _tree(tmp_path)
    (root / "callers.py").write_text(textwrap.dedent("""
        def by_name(obj):
            return obj.work()

        def reference_too(obj):
            f = obj.work
            return f() or missing_thing()

        def work():
            return 3
    """))
    store = sg.Store(str(tmp_path / "inc.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    (root / "m0.py").write_text(
        "def helper_0(x):\n    return x.work() or x.only_base()\n")
    assert reindex_incremental(store, str(root), {"m0.py"}).ok
    twin = sg.Store(str(tmp_path / "twin.db"))
    assert sg.reindex(twin, str(root), streaming=False).ok
    assert _rows(store) == _rows(twin)
    assert _holes(store) == _holes(twin)
    assert _battery(store) == _battery(twin)
    store.close()
    twin.close()


def test_adjcache_structurally_identical(tmp_path):
    """The mmapped sidecar derived from a compressed index must encode the SAME
    GRAPH as one derived from the flat twin: identical node universe and, per
    node, identical neighbour multisets (dst, relation, confident) in both
    directions. Byte order WITHIN a node's CSR segment is read-order-dependent
    and carries no meaning (a widened fan-out's arms are unordered by
    construction) — every traversal result over it is order-free, and the
    same-store accelerated-vs-reference contract is pinned separately in
    test_adjcache.py."""
    import pytest
    np = pytest.importorskip("numpy")
    from stitchgraph.core.adjcache import load_cache
    _tree(tmp_path)
    on = sg.Store(str(tmp_path / "on.db"))
    off = sg.Store(str(tmp_path / "off.db"))
    off._compression_env_ok = False
    assert sg.reindex(on, str(tmp_path / "proj"), streaming=False).ok
    assert sg.reindex(off, str(tmp_path / "proj"), streaming=False).ok
    c_on, c_off = load_cache(on), load_cache(off)
    assert c_on is not None and c_off is not None
    assert c_on.ids == c_off.ids

    def segments(cache, prefix):
        """Per-node LOGICAL neighbour triples: the flat CSR segment plus the v2
        shared group arrays expanded (empty on the flat twin — same code runs
        on both caches, so the comparison is representation-blind)."""
        indptr = getattr(cache, f"{prefix}_indptr")
        idxs = getattr(cache, f"{prefix}_indices")
        rel = getattr(cache, f"{prefix}_rel")
        conf = np.unpackbits(getattr(cache, f"{prefix}_conf"),
                             count=idxs.size).astype(bool)
        segs = [list(zip(idxs[a:b].tolist(), rel[a:b].tolist(),
                         conf[a:b].tolist(), strict=True))
                for a, b in zip(indptr[:-1].tolist(), indptr[1:].tolist(),
                                strict=True)]
        gconf = np.unpackbits(cache.grp_conf, count=cache.grp_set.size).astype(bool)
        members = [cache.set_members[a:b].tolist()
                   for a, b in zip(cache.set_indptr[:-1].tolist(),
                                   cache.set_indptr[1:].tolist(), strict=True)]
        for src, (a, b) in enumerate(zip(cache.grp_indptr[:-1].tolist(),
                                         cache.grp_indptr[1:].tolist(),
                                         strict=True)):
            for g in range(a, b):
                s, r, c = (int(cache.grp_set[g]), int(cache.grp_rel[g]),
                           bool(gconf[g]))
                for m in members[s]:
                    segs[src if prefix == "fwd" else m].append(
                        (m, r, c) if prefix == "fwd" else (src, r, c))
        return [sorted(s) for s in segs]

    for prefix in ("fwd", "rev"):
        assert segments(c_on, prefix) == segments(c_off, prefix), prefix
    assert int(c_on.grp_set.size) > 0, \
        "compression-on sidecar must actually exercise the shared group arrays"
    on.close()
    off.close()
