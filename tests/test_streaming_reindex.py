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


def test_dedup_ors_name_based_onto_precise_survivor():
    """A (src,rel,dst_id) group with BOTH a precise (name_based=False) and a name-based arm
    must leave the survivor re-widenable (name_based=True), matching the store's dedup — even
    though the kept highest-weight row is the precise one. This is the panel-R50 fix: under
    --precise (jedi) the precise arm and the extractor's name-based arm hit the same target,
    and the full vs streaming paths would otherwise diverge on name_based."""
    precise = Edge(src="a::f", relation=Relation.CALLS, dst_symbol="g", dst_id="b::g",
                   weight=1.0, name_based=False)
    namebased = Edge(src="a::f", relation=Relation.CALLS, dst_symbol="g", dst_id="b::g",
                     weight=0.5, name_based=True)
    out = _dedup_edges([precise, namebased])
    assert len(out) == 1
    assert out[0].weight == 1.0          # precise (highest-weight) row is the survivor
    assert out[0].name_based is True     # ...but the group stays re-widenable


def test_dedup_pure_precise_group_stays_pinned():
    """A group with NO name-based arm keeps name_based=False — a precise resolution is never
    wrongly made re-widenable (R22A)."""
    a = Edge(src="a::f", relation=Relation.CALLS, dst_symbol="g", dst_id="b::g",
             weight=1.0, name_based=False)
    b = Edge(src="a::f", relation=Relation.CALLS, dst_symbol="g", dst_id="b::g",
             weight=0.8, name_based=False)
    out = _dedup_edges([a, b])
    assert len(out) == 1
    assert out[0].name_based is False


def test_streaming_equals_full_precise_jedi(tmp_path):
    """`reindex(precise=True)` (jedi adds precise edges) must ALSO be byte-identical between
    streaming and full — incl. name_based. Pins panel R50: jedi's precise arm arrives as a
    resolver edge in a different sink group than the extractor's name-based arm, so the mixed
    (src,rel,dst_id) group reaches the store; the _dedup_edges name_based-OR keeps both paths
    identical."""
    import pytest
    pytest.importorskip("jedi")
    src = tmp_path / "proj"
    _write(src, {
        # `foo` is a project homonym (defined in b and c) -> the extractor emits a name-based
        # AMBIGUOUS fan-out, while jedi resolves the import precisely to b.foo.
        "a.py": "from b import foo\ndef caller():\n    return foo()\n",
        "b.py": "def foo():\n    return 1\n",
        "c.py": "def foo():\n    return 2\n",
    })
    def snap(store):
        return sorted(tuple(r) for r in store.conn.execute(
            "SELECT src, relation, dst_symbol, COALESCE(dst_id,''), weight, provenance, "
            "name_based FROM edges"))
    with sg.Store(str(tmp_path / "full.db")) as full:
        sg.reindex(full, str(src), precise=True, streaming=False)
        fe = snap(full)
    with sg.Store(str(tmp_path / "stream.db")) as stream:
        sg.reindex(stream, str(src), precise=True, streaming=True)
        se = snap(stream)
    assert fe == se


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


# -- Review 2026-07-03 / F8: the AUTO-stream probe must count only extractable files ----
def test_auto_stream_ignores_skip_dirs(tmp_path):
    """The probe used a bare rglob('*') with no SKIP_DIRS/extension filter, so any repo
    with a populated .venv was forced onto the streaming path (slower, and non-crash-
    atomic) even when the actual project was tiny (review 2026-07-03, F8)."""
    from stitchgraph.core.operations import _STREAM_AUTO_FILES, _auto_stream

    # a tiny real project...
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    # ...next to a big vendored tree the extractor will never read
    dep = tmp_path / ".venv" / "lib" / "site-packages" / "dep"
    dep.mkdir(parents=True)
    for i in range(_STREAM_AUTO_FILES + 10):
        (dep / f"m{i}.py").write_text("x = 1\n")

    class _Disk:  # duck-typed: _auto_stream only reads .path
        path = str(tmp_path / "index.db")

    assert _auto_stream(str(tmp_path), _Disk()) is False, (
        "vendored .venv files must not push a tiny project onto the streaming path")
    # and a genuinely large first-party tree still streams
    big = tmp_path / "src"
    big.mkdir()
    for i in range(_STREAM_AUTO_FILES + 10):
        (big / f"m{i}.py").write_text("x = 1\n")
    assert _auto_stream(str(tmp_path), _Disk()) is True


def test_streaming_orphan_edges_swept_after_extractor_failure(tmp_path, monkeypatch):
    """Review 2026-07-03 F9: a swallowed tree-sitter failure mid-extract leaves committed
    edge batches whose nodes were never inserted — phantom resolved edges that flood
    find_holes/scan. The streaming path now sweeps edges whose src/dst has no node."""
    import warnings

    import stitchgraph as sg
    from stitchgraph.core.extract import treesitter
    from stitchgraph.core.model import Edge, Provenance, Relation

    (tmp_path / "app.py").write_text("def real():\n    return 1\n\nreal()\n")

    def exploding_extract(root, ignore=None, *, cache_trees=True, edge_sink=None):
        # simulate: some batches already committed, then the grammar dies
        if edge_sink is not None:
            for i in range(3):
                edge_sink.append(Edge(
                    src=f"ghost.rb::caller{i}", relation=Relation.CALLS,
                    dst_symbol="phantom", dst_id="ghost.rb::phantom", weight=1.0,
                    provenance=Provenance.EXTRACTED, location="ghost.rb:1:0",
                    source="tree-sitter"))
            edge_sink.flush()
        raise RuntimeError("simulated grammar segfault-class failure")

    monkeypatch.setattr(treesitter, "HAS_TREE_SITTER", True, raising=False)
    monkeypatch.setattr(treesitter, "extract", exploding_extract)

    db = tmp_path / "idx.db"
    with sg.Store(str(db)) as store:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            res = sg.reindex(store, str(tmp_path), streaming=True)
        assert res.ok
        orphans = store.conn.execute(
            "SELECT COUNT(*) c FROM edges WHERE src LIKE 'ghost%' OR dst_id LIKE 'ghost%'"
        ).fetchone()["c"]
        assert orphans == 0, "phantom edges from the failed extractor must be swept"
        holes = sg.find_holes(store)
        assert holes.result == [], "no phantom holes from the failed extractor"


def test_streaming_python_edges_bounded_memory(tmp_path):
    """Constant-memory is a CORE claim and was never gated (field report 2026-07-03: Home
    Assistant OOM'd at ~7 GB with streaming=True — the Python extractor materialised its whole
    edge list before the sink drained it; only tree-sitter ever truly streamed). This pins the
    fix black-box: index a homonym-fanout pure-Python corpus in a subprocess under a hard
    address-space cap. CALIBRATED (2026-07-03, identical corpus shape at 610 files): pre-fix
    peaks at ~190 B/edge linear (412 MB at 2.16M edges), post-fix is flat at ~43 MB. At this
    test's ~1.2M edges the pre-fix code needs ~230 MB and dies at the 130 MB cap about a third
    of the way through (verified); the streamed path passes with ~3x headroom.

    The corpus MUST contain class inheritance with overrides: the endgame override widening
    (`Store._propagate_overrides`) early-returns on an inheritance-free graph, and its first
    cut fetchall'd the whole edge table — this gate passed while real Home Assistant still
    OOM'd in that endgame (field report #2, same day). The Base/Impl pairs below keep that
    path exercised, and the widened-edge assertion proves it ran rather than early-returned."""
    import subprocess
    import sys
    import textwrap

    for i in range(450):
        d = tmp_path / "corpus" / f"pkg{i // 50}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("")
        (d / f"m{i}.py").write_text(textwrap.dedent(f"""
            def work{i}(x):
                a = get(); b = setup(); c = run()
                d = load(); e = save(); f = mk()
                return a, b, c, d, e, f, get(), setup(), run()
            def get(): return 1
            def setup(): return 2
            def run(): return 3
            def load(): return 4
            def save(): return 5
            def mk(): return 6
            class Base{i}:
                def start(self):
                    return self.step()
                def step(self):
                    return 0
            class Impl{i}(Base{i}):
                def step(self):
                    return get()
            def boot{i}():
                return Impl{i}().start()
        """))
    script = textwrap.dedent(f"""
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (130 * 1024 * 1024,) * 2)  # 130 MB hard cap
        import stitchgraph as sg
        with sg.Store({str(str(tmp_path / 'i.db'))!r}) as store:
            r = sg.reindex(store, {str(str(tmp_path / 'corpus'))!r}, streaming=True)
            assert r.ok and r.result["nodes"] > 3000, r.result
            n = store.conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
            assert n > 1_000_000, f"fan-out corpus must produce bulk edges, got {{n}}"
            w = store.conn.execute("SELECT COUNT(*) c FROM edges WHERE provenance = "
                                   "'ambiguous' AND name_based = 0").fetchone()["c"]
            assert w >= 400, f"override widening must run on this corpus, got {{w}} edges"
        print("BOUNDED-OK")
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"}, timeout=600)
    assert "BOUNDED-OK" in proc.stdout, (
        f"streaming reindex exceeded the 130 MB memory cap (rc={proc.returncode}):\n"
        f"{proc.stderr[-800:]}")

    # scan over the index it just built must stay at ADJACENCY scale (compact ints), never
    # Edge-object scale: its provenance-share step indexed every resolved edge into Python
    # dicts and MemoryError'd at a 6 GB cap on Home Assistant's 16M-edge graph (field
    # analysis 2026-07-03). CALIBRATED on this corpus's ~1.2M edges: pre-fix scan peaks at
    # 1,486 MB, the SQL-share rewrite at 185 MB — the 400 MB cap kills the former with the
    # same margin it grants the latter. Separate subprocess so the caps stay independent.
    scan_script = textwrap.dedent(f"""
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (400 * 1024 * 1024,) * 2)  # 400 MB hard cap
        import stitchgraph as sg
        with sg.Store({str(str(tmp_path / 'i.db'))!r}) as store:
            r = sg.scan(store)
            assert r.ok, r
        print("SCAN-BOUNDED-OK")
    """)
    proc = subprocess.run([sys.executable, "-c", scan_script], capture_output=True,
                          text=True, env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
                          timeout=600)
    assert "SCAN-BOUNDED-OK" in proc.stdout, (
        f"scan exceeded the 400 MB memory cap (rc={proc.returncode}):\n"
        f"{proc.stderr[-800:]}")


# ---------------------------------------------------------------------------
# Planner statistics: reindex must leave approximate ANALYZE stats behind.


def _stat1_indexes(store):
    if not store.conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()[0]:
        return set()
    return {r[0] for r in store.conn.execute("SELECT idx FROM sqlite_stat1")}


def test_reindex_leaves_planner_stats_in_memory_path(tmp_path):
    """Both reindex paths must end with `sqlite_stat1` covering the edge indexes. A
    stat-less db lets the planner choose by schema order, not selectivity — on the
    16M-edge field graph that walked idx_edges_rel (12.9M entries) per scan candidate
    (v3.29.0 planner trap). The hot shipped queries are pinned by shape; the stats
    protect every other query by default."""
    _write(tmp_path, {"a.py": "def f():\n    return g()\n\ndef g():\n    return 1\n"})
    with sg.Store(str(tmp_path / "idx.db")) as store:
        assert sg.reindex(store, str(tmp_path), streaming=False).ok
        idxs = _stat1_indexes(store)
        assert {"idx_edges_src", "idx_edges_dst"} <= idxs, idxs


def test_reindex_leaves_planner_stats_streaming_path(tmp_path):
    _write(tmp_path, {"a.py": "def f():\n    return g()\n\ndef g():\n    return 1\n"})
    with sg.Store(str(tmp_path / "idx.db")) as store:
        assert sg.reindex(store, str(tmp_path), streaming=True).ok
        idxs = _stat1_indexes(store)
        assert {"idx_edges_src", "idx_edges_dst"} <= idxs, idxs
        # The endgame's temporary covering index must not leak into the stats.
        assert "idx_edges_dedup" not in idxs
