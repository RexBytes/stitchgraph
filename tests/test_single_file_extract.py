"""Single-file extraction convergence (research/21 stage B): for an edit
matrix over a multi-file corpus, `extract_single_file` + `replace_file` must
land on the SAME graph as today's `reindex_incremental` (whole-project
extraction) — rows, holes, and roles alike."""
from __future__ import annotations

import textwrap

import stitchgraph as sg
from stitchgraph.core.extract.single import extract_single_file
from stitchgraph.core.operations import reindex_incremental


def _tree(tmp_path, name):
    root = tmp_path / name
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("from .svc import Service\n__all__ = ['Service']\n")
    (pkg / "base.py").write_text(textwrap.dedent("""
        class Base:
            def work(self):
                return 1
    """))
    (pkg / "svc.py").write_text(textwrap.dedent("""
        from .base import Base

        class Service(Base):
            def work(self):
                return 2

        def helper(obj):
            return obj.work()

        LIMIT = 10
    """))
    (pkg / "app.py").write_text(textwrap.dedent("""
        from .svc import helper, LIMIT

        def main():
            return helper(None) or LIMIT

        def work():
            return 3
    """))
    (root / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef env():\n    return {}\n")
    (root / "test_app.py").write_text(
        "def test_main(env):\n    assert env == {}\n")
    return root


def _graph(store):
    nodes = {(r["id"], r["kind"], r["name"], r["roles"]) for r in store.conn.execute(
        "SELECT id, kind, name, roles FROM nodes")}
    edges = {tuple(r) for r in store.conn.execute(
        "SELECT src, relation, dst_symbol, dst_id, weight, provenance, name_based "
        "FROM edges_all")}
    holes = {tuple(r) for r in store.conn.execute(
        "SELECT src, relation, dst_symbol FROM edges WHERE dst_id IS NULL")}
    sym = {tuple(r) for r in store.conn.execute("SELECT file, kind, name FROM symtab")}
    return nodes, edges, holes, sym


def _converge(tmp_path, edit_rel, new_content):
    """Apply one edit two ways — single-file vs whole-project incremental —
    and compare the end states."""
    ra, rb = _tree(tmp_path, "a"), _tree(tmp_path, "b")
    sa = sg.Store(str(tmp_path / "a.db"))
    sb = sg.Store(str(tmp_path / "b.db"))
    assert sg.reindex(sa, str(ra), streaming=False).ok
    assert sg.reindex(sb, str(rb), streaming=False).ok

    (ra / edit_rel).write_text(new_content)
    (rb / edit_rel).write_text(new_content)

    nodes, edges, contribution = extract_single_file(sa, str(ra), edit_rel)
    sa.replace_file(edit_rel, nodes, edges, symtab=contribution)

    assert reindex_incremental(sb, str(rb), {edit_rel}).ok

    ga, gb = _graph(sa), _graph(sb)
    sa.close()
    sb.close()
    for part, a, b in zip(("nodes", "edges", "holes", "symtab"), ga, gb, strict=True):
        assert a == b, (f"{part} diverge:\n  single-only: {sorted(a - b)}\n"
                        f"  incremental-only: {sorted(b - a)}")


def test_body_edit_converges(tmp_path):
    _converge(tmp_path, "pkg/app.py", textwrap.dedent("""
        from .svc import helper, LIMIT

        def main():
            return helper(1) or LIMIT or extra()

        def extra():
            return 4

        def work():
            return 3
    """))


def test_homonym_add_converges(tmp_path):
    _converge(tmp_path, "pkg/app.py", textwrap.dedent("""
        from .svc import helper, LIMIT

        def main():
            return helper(None) or LIMIT

        def work():
            return 3

        class Extra:
            def work(self):
                return 5
    """))


def test_homonym_remove_converges(tmp_path):
    _converge(tmp_path, "pkg/app.py", textwrap.dedent("""
        from .svc import helper, LIMIT

        def main():
            return helper(None) or LIMIT
    """))


def test_const_and_import_converges(tmp_path):
    _converge(tmp_path, "pkg/svc.py", textwrap.dedent("""
        from .base import Base

        class Service(Base):
            def work(self):
                return 2

        def helper(obj):
            return obj.work()

        LIMIT, EXTRA = 10, 20
    """))


def test_fixture_add_converges(tmp_path):
    _converge(tmp_path, "conftest.py",
              "import pytest\n\n@pytest.fixture\ndef env():\n    return {}\n\n"
              "@pytest.fixture\ndef db():\n    return None\n")


def test_subclass_of_other_file_converges(tmp_path):
    _converge(tmp_path, "pkg/app.py", textwrap.dedent("""
        from .svc import helper, LIMIT
        from .base import Base

        class AppService(Base):
            def work(self):
                return 9

        def main():
            return helper(None) or LIMIT
    """))


# -- the shipped dispatcher (reindex_singlefile) ------------------------------

def _converge_dispatch(tmp_path, edit_rel, new_content):
    """Same edit through the SHIPPED fast path vs whole-project incremental."""
    from stitchgraph.core.operations import reindex_singlefile
    ra, rb = _tree(tmp_path, "a"), _tree(tmp_path, "b")
    sa = sg.Store(str(tmp_path / "a.db"))
    sb = sg.Store(str(tmp_path / "b.db"))
    assert sg.reindex(sa, str(ra), streaming=False).ok
    assert sg.reindex(sb, str(rb), streaming=False).ok
    (ra / edit_rel).write_text(new_content)
    (rb / edit_rel).write_text(new_content)
    r = reindex_singlefile(sa, str(ra), {edit_rel})
    assert r is not None and r.ok, "the fast path must apply to this edit"
    assert reindex_incremental(sb, str(rb), {edit_rel}).ok
    ga, gb = _graph(sa), _graph(sb)
    sa.close()
    sb.close()
    for part, a, b in zip(("nodes", "edges", "holes", "symtab"), ga, gb, strict=True):
        assert a == b, (f"{part} diverge:\n  single-only: {sorted(a - b)}\n"
                        f"  incremental-only: {sorted(b - a)}")


def test_dispatch_export_surface_edit_converges(tmp_path):
    """The cross-file role direction: changing an __init__'s __all__ must re-tag
    OTHER files' nodes exactly as the whole-project path does (the store-side
    exported_ids recomputation, incl. the ancestor-closure rule)."""
    _converge_dispatch(tmp_path, "pkg/__init__.py",
                       "from .svc import Service, helper\n"
                       "__all__ = ['Service', 'helper']\n")


def test_dispatch_body_edit_converges(tmp_path):
    _converge_dispatch(tmp_path, "pkg/app.py", textwrap.dedent("""
        from .svc import helper, LIMIT

        def main():
            return helper(2) or LIMIT

        def work():
            return 3
    """))


def test_dispatch_declines_resolver_shapes(tmp_path):
    """The honest gate: route/event/ORM/SQL shapes must decline the fast path
    (returns None) so the whole-project resolvers keep running. The SQL case is
    gated on sqlglot exactly like the resolver itself: without it the resolver
    is a no-op, no divergence is possible, and the fast path SHOULD apply —
    the core-only CI job runs that arm."""
    from stitchgraph.core.operations import reindex_singlefile
    from stitchgraph.core.resolve.sql import _HAVE_SQLGLOT
    root = _tree(tmp_path, "a")
    store = sg.Store(str(tmp_path / "a.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    cases = [
        "def h(request):\n    return 1\n\nurlpatterns = [path('u/', h)]\n",
        "class M:\n    def go(self, sig, cb):\n        sig.connect(cb)\n",
    ]
    sql = "Q = \"SELECT * FROM users\"\n"
    if _HAVE_SQLGLOT:
        cases.append(sql)
    for content in cases:
        (root / "pkg" / "app.py").write_text(content)
        assert reindex_singlefile(store, str(root), {"pkg/app.py"}) is None, content
    if not _HAVE_SQLGLOT:
        (root / "pkg" / "app.py").write_text(sql)
        assert reindex_singlefile(store, str(root), {"pkg/app.py"}) is not None, \
            "without sqlglot the sql resolver cannot fire — the fast path applies"
    store.close()


def test_dispatch_declines_pre_symtab_index(tmp_path):
    from stitchgraph.core.operations import reindex_singlefile
    root = _tree(tmp_path, "a")
    store = sg.Store(str(tmp_path / "a.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    store.conn.execute("DELETE FROM meta WHERE key = 'packages'")  # old index
    store.commit()
    (root / "pkg" / "app.py").write_text("def main():\n    return 1\n")
    assert reindex_singlefile(store, str(root), {"pkg/app.py"}) is None
    store.close()


def test_dispatch_declines_non_python_and_syntax_error(tmp_path):
    from stitchgraph.core.operations import reindex_singlefile
    root = _tree(tmp_path, "a")
    store = sg.Store(str(tmp_path / "a.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    (root / "notes.txt").write_text("hello")
    assert reindex_singlefile(store, str(root), {"notes.txt"}) is None
    (root / "pkg" / "app.py").write_text("def broken(:\n")
    assert reindex_singlefile(store, str(root), {"pkg/app.py"}) is None
    store.close()
