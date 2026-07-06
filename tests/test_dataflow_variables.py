"""research/22 deliverable 1: mutation-aware module-state tracking. A
module-level container mutated in place (`CACHE[k] = v`, `REGISTRY.append(x)`)
needs no `global` statement and was invisible to data-loop detection; it now
emits the same var::/READS/WRITES slice as declared globals — advisory only,
precision-biased (closed mutator allowlist; read-only tables emit nothing)."""
from __future__ import annotations

import textwrap

import stitchgraph as sg
from stitchgraph.core.dataloop import find_data_loops
from stitchgraph.core.model import NodeKind
from stitchgraph.core.operations import reindex_incremental


def _index(tmp_path, files):
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    for rel, content in files.items():
        (root / rel).write_text(textwrap.dedent(content))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    return store


def _var_ids(store):
    return {n.id for n in store.nodes_by_kind(NodeKind.VARIABLE)}


def test_container_mutation_loop_detected(tmp_path):
    """The canonical shape the `global`-gated slice missed: subscript-store
    writer + reader that calls the writer -> a data feedback loop."""
    store = _index(tmp_path, {"cache.py": """
        CACHE = {}

        def remember(k, v):
            CACHE[k] = v

        def recall(k):
            if k not in CACHE:
                remember(k, compute(k))
            return CACHE[k]

        def compute(k):
            return k * 2
    """})
    assert "var::cache.py::CACHE" in _var_ids(store)
    loops = find_data_loops(store)
    assert any("var::cache.py::CACHE" in comp for comp in loops), loops
    edges = {(e.src, e.relation.value) for e in store.resolved_edges()
             if e.dst_id == "var::cache.py::CACHE"}
    assert ("cache.py::remember", "WRITES") in edges
    assert ("cache.py::recall", "READS") in edges
    store.close()


def test_mutating_method_call_is_a_write(tmp_path):
    store = _index(tmp_path, {"reg.py": """
        HANDLERS = []

        def register(fn):
            HANDLERS.append(fn)

        def dispatch():
            for h in HANDLERS:
                h()
    """})
    edges = {(e.src, e.relation.value) for e in store.resolved_edges()
             if e.dst_id == "var::reg.py::HANDLERS"}
    assert ("reg.py::register", "WRITES") in edges
    assert ("reg.py::dispatch", "READS") in edges
    # register's receiver Name must not double as a READ
    assert ("reg.py::register", "READS") not in edges
    store.close()


def test_read_only_container_emits_nothing(tmp_path):
    """A module table that is only READ is configuration, not feedback state —
    no var node, no graph flooding (the precision gate)."""
    store = _index(tmp_path, {"conf.py": """
        DEFAULTS = {"a": 1}

        def get(k):
            return DEFAULTS.get(k)

        def has(k):
            return k in DEFAULTS
    """})
    assert "var::conf.py::DEFAULTS" not in _var_ids(store)
    store.close()


def test_unknown_method_emits_nothing(tmp_path):
    """The mutator allowlist is CLOSED: an unrecognised method is not assumed
    to mutate (a missed loop beats a phantom loop)."""
    store = _index(tmp_path, {"m.py": """
        THING = {}

        def use():
            return THING.copy()
    """})
    assert "var::m.py::THING" not in _var_ids(store)
    store.close()


def test_local_rebind_is_not_module_state(tmp_path):
    """`X = ...` inside a function WITHOUT `global` binds a local — it must not
    count as a write to the module container of the same name."""
    store = _index(tmp_path, {"m.py": """
        ITEMS = []

        def shadow():
            ITEMS = [1]
            return ITEMS
    """})
    assert "var::m.py::ITEMS" not in _var_ids(store)
    store.close()


def test_declared_global_slice_unchanged(tmp_path):
    """The original v1 contract: a `global`-declared scalar still gets its node
    and read/write edges, including the read-only-declarer guard."""
    store = _index(tmp_path, {"g.py": """
        COUNT = 0

        def bump():
            global COUNT
            COUNT += 1

        def peek():
            global COUNT
            return COUNT
    """})
    edges = {(e.src, e.relation.value) for e in store.resolved_edges()
             if e.dst_id == "var::g.py::COUNT"}
    assert ("g.py::bump", "WRITES") in edges
    assert ("g.py::peek", "READS") in edges
    assert ("g.py::peek", "WRITES") not in edges  # declare-only, no assign
    store.close()


def test_incremental_converges_and_zombie_var_cleared(tmp_path):
    """Removing the last mutation must remove the var node on the incremental
    path exactly as a fresh reindex would (the replace_file var-row sweep)."""
    files = {"cache.py": """
        CACHE = {}

        def remember(k, v):
            CACHE[k] = v

        def recall(k):
            return CACHE.get(k)
    """}
    store = _index(tmp_path, files)
    assert "var::cache.py::CACHE" in _var_ids(store)
    (tmp_path / "src" / "cache.py").write_text(textwrap.dedent("""
        CACHE = {}

        def recall(k):
            return CACHE.get(k)
    """))
    assert reindex_incremental(store, str(tmp_path / "src"), {"cache.py"}).ok
    assert "var::cache.py::CACHE" not in _var_ids(store), \
        "zombie var node survived the edit"
    twin = sg.Store(str(tmp_path / "twin.db"))
    assert sg.reindex(twin, str(tmp_path / "src")).ok
    assert _var_ids(store) == _var_ids(twin)
    twin.close()
    store.close()
