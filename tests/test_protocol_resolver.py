"""v3.39.0: the protocol-method resolver — dunders the source never NAMES
(research/18's biggest recall-tail slice: `TemplateContextManager.__exit__`
executed by 389 of 2,056 HA tests, statically reached by none). `with` /
`for` / subscripts now resolve to the classes defining the protocol dunders:
exactly when the receiver is resolvable, name-based (INFERRED/AMBIGUOUS)
when it isn't. Builtin receivers resolve to no project symbol — no noise."""

from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core.reach import reachable_from


def _reach(store, seed):
    return reachable_from(store, {seed})


def test_with_unknown_receiver_reaches_exit(tmp_path):
    """The exact HA shape: the CM comes from a helper's return value (receiver
    type unknown), so the old exact-only binding saw nothing."""
    (tmp_path / "cm.py").write_text(
        "class TemplateContext:\n"
        "    def __enter__(self):\n        return self\n"
        "    def __exit__(self, *exc):\n        return self._cleanup()\n"
        "    def _cleanup(self):\n        return None\n\n"
        "def make_context():\n    return TemplateContext()\n\n"
        "def render(template):\n"
        "    ctx = make_context()\n"
        "    with ctx:\n        return template\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "cm.py::render")
        assert "cm.py::TemplateContext.__exit__" in reach
        assert "cm.py::TemplateContext.__enter__" in reach
        assert "cm.py::TemplateContext._cleanup" in reach  # THROUGH the dunder


def test_async_with_reaches_aexit(tmp_path):
    (tmp_path / "acm.py").write_text(
        "class Session:\n"
        "    async def __aenter__(self):\n        return self\n"
        "    async def __aexit__(self, *exc):\n        return None\n\n"
        "async def fetch(s):\n"
        "    async with s:\n        return 1\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "acm.py::fetch")
        assert "acm.py::Session.__aexit__" in reach
        assert "acm.py::Session.__aenter__" in reach


def test_subscript_assignment_reaches_setitem(tmp_path):
    """`vars[k] = v` runs __setitem__ — the ScriptRunVariables shape (missed by
    120 HA tests)."""
    (tmp_path / "sv.py").write_text(
        "class RunVariables:\n"
        "    def __setitem__(self, k, v):\n        return self._assign(k, v)\n"
        "    def __getitem__(self, k):\n        return 1\n"
        "    def __delitem__(self, k):\n        return None\n"
        "    def _assign(self, k, v):\n        return v\n\n"
        "def execute(variables, key):\n"
        "    variables[key] = 1\n"
        "    x = variables[key]\n"
        "    del variables[key]\n"
        "    return x\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "sv.py::execute")
        assert "sv.py::RunVariables.__setitem__" in reach
        assert "sv.py::RunVariables.__getitem__" in reach
        assert "sv.py::RunVariables.__delitem__" in reach
        assert "sv.py::RunVariables._assign" in reach


def test_for_loop_and_comprehension_reach_iter(tmp_path):
    (tmp_path / "it.py").write_text(
        "class Registry:\n"
        "    def __iter__(self):\n        return self\n"
        "    def __next__(self):\n        raise StopIteration\n\n"
        "def scan_all(reg):\n"
        "    for item in reg:\n        pass\n"
        "    return [x for x in reg]\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "it.py::scan_all")
        assert "it.py::Registry.__iter__" in reach
        assert "it.py::Registry.__next__" in reach


def test_exact_receiver_binds_only_that_class(tmp_path):
    """A resolvable receiver keeps the precise pre-v3.39 binding: the OTHER
    class's dunder gains no edge from this function."""
    (tmp_path / "two.py").write_text(
        "class Used:\n"
        "    def __enter__(self):\n        return self\n"
        "    def __exit__(self, *e):\n        return None\n\n"
        "class Other:\n"
        "    def __enter__(self):\n        return self\n"
        "    def __exit__(self, *e):\n        return None\n\n"
        "def go():\n"
        "    with Used():\n        return 1\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        reach = _reach(store, "two.py::go")
        assert "two.py::Used.__exit__" in reach
        assert "two.py::Other.__exit__" not in reach


def test_builtin_receivers_add_no_edges(tmp_path):
    """dict/list subscripts and loops over builtins resolve to no project
    symbol — a project with no protocol dunders gains zero edges from them."""
    (tmp_path / "plain.py").write_text(
        "def busy(d):\n"
        "    d['a'] = 1\n"
        "    xs = [i for i in range(3)]\n"
        "    for x in xs:\n        d[x] = x\n"
        "    return d['a']\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        n = store.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE dst_symbol LIKE '__%__'").fetchone()[0]
        assert n == 0
