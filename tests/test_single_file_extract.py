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
