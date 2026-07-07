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


def test_instance_attribute_loop_detected(tmp_path):
    """research/22 deliverable 2: methods of one class reading and writing the
    same self.attr — the classic non-global feedback shape."""
    store = _index(tmp_path, {"worker.py": """
        class Worker:
            def __init__(self):
                self.queue = []

            def enqueue(self, item):
                self.queue.append(item)

            def drain(self):
                while self.queue:
                    item = self.queue.pop()
                    if needs_retry(item):
                        self.enqueue(item)

        def needs_retry(item):
            return False
    """})
    vid = "var::worker.py::Worker.queue"
    assert vid in _var_ids(store)
    loops = find_data_loops(store)
    assert any(vid in comp for comp in loops), loops
    store.close()


def test_write_only_attribute_emits_nothing(tmp_path):
    """__init__ seeding + a setter with no reader is not feedback state."""
    store = _index(tmp_path, {"m.py": """
        class Box:
            def __init__(self):
                self.value = None

            def set(self, v):
                self.value = v
    """})
    assert not any(v.startswith("var::m.py::Box.") for v in _var_ids(store))
    store.close()


def test_attribute_ids_are_class_scoped(tmp_path):
    """Two classes with the same attribute name must never share a var node."""
    store = _index(tmp_path, {"m.py": """
        class A:
            def put(self, x):
                self.buf = x

            def get(self):
                return self.buf

        class B:
            def put(self, x):
                self.buf = x

            def get(self):
                return self.buf
    """})
    vids = _var_ids(store)
    assert "var::m.py::A.buf" in vids
    assert "var::m.py::B.buf" in vids
    for e in store.resolved_edges():
        if e.dst_id == "var::m.py::A.buf":
            assert e.src.startswith("m.py::A.")
    store.close()


def test_unused_params_surfaced_in_scan(tmp_path):
    """research/22 deliverable 3: a parameter never loaded in the body is a
    GREEN advisory; interface shapes are excluded, not hedged."""
    store = _index(tmp_path, {"m.py": """
        from abc import abstractmethod

        def leaky(a, b, _ignored, *args, **kwargs):
            return a

        def clean(x):
            return x + 1

        class Base:
            @abstractmethod
            def handle(self, event):
                ...

        class Impl(Base):
            def handle(self, event):
                return 1   # unused param, but the signature is Base's contract

        if __name__ == "__main__":
            leaky(1, 2, 3)
            clean(4)
            Impl().handle(None)
    """})
    issues = {i["node"]: i for i in sg.scan(store).result
              if i["kind"] == "unused_params"}
    assert "m.py::leaky" in issues
    assert issues["m.py::leaky"]["params"] == ["b"]  # _ignored/*args/**kwargs excluded
    assert "m.py::clean" not in issues
    assert "m.py::Base.handle" not in issues        # abstract
    assert "m.py::Impl.handle" not in issues        # overrides a first-party base
    assert issues["m.py::leaky"]["urgency"] == "green"
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
