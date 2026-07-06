"""The persisted per-file symbol table (research/21 stage A): both reindex paths
and replace_file maintain `symtab` (file, kind, name) rows recording the four
raw pass-1 name-sets — module constants, pytest fixtures, export surface,
__main__ calls — plus the packages/source_prefix meta. Pure persistence: no
behaviour change until the single-file extraction mode consumes it."""
from __future__ import annotations

import json
import textwrap

import stitchgraph as sg
from stitchgraph.core.operations import reindex_incremental


def _tree(tmp_path):
    root = tmp_path / "proj"
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        "from .impl import Engine as Public\n__all__ = ['Public', 'helper']\n")
    (pkg / "impl.py").write_text(textwrap.dedent("""
        HORIZONTAL, VERTICAL = 1, 2
        LIMIT: int = 10

        class Engine:
            def run(self):
                return 1

        def helper():
            return HORIZONTAL

        if __name__ == "__main__":
            helper()
    """))
    (root / "conftest.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef env():\n    return {}\n")
    return root


def _rows(store):
    return {(r["file"], r["kind"], r["name"]) for r in store.conn.execute(
        "SELECT file, kind, name FROM symtab")}


def test_reindex_populates_symtab_in_memory(tmp_path):
    root = _tree(tmp_path)
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    rows = _rows(store)
    assert ("pkg/impl.py", "const", "HORIZONTAL") in rows
    assert ("pkg/impl.py", "const", "VERTICAL") in rows       # tuple unpack
    assert ("pkg/impl.py", "const", "LIMIT") in rows          # AnnAssign
    assert ("pkg/impl.py", "main", "helper") in rows          # __main__ call
    assert ("conftest.py", "fixture", "env") in rows
    assert ("pkg/__init__.py", "export", "Public") in rows    # __all__ + rename
    assert ("pkg/__init__.py", "export", "Engine") in rows    # renamed re-export leaf
    assert ("pkg/__init__.py", "export", "helper") in rows
    # import-internality meta (a root-level conftest.py is itself an importable
    # top-level name, so it belongs in packages alongside pkg)
    assert json.loads(store.get_meta("packages")) == ["conftest", "pkg"]
    assert store.get_meta("source_prefix") == ""
    store.close()


def test_reindex_populates_symtab_streaming(tmp_path):
    root = _tree(tmp_path)
    a = sg.Store(str(tmp_path / "a.db"))
    b = sg.Store(str(tmp_path / "b.db"))
    assert sg.reindex(a, str(root), streaming=False).ok
    assert sg.reindex(b, str(root), streaming=True).ok
    assert _rows(a) == _rows(b), "the two paths must persist identical records"
    a.close()
    b.close()


def test_replace_file_maintains_symtab(tmp_path):
    root = _tree(tmp_path)
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    # edit: drop VERTICAL, add DIAGONAL; drop the __main__ block
    (root / "pkg" / "impl.py").write_text(textwrap.dedent("""
        HORIZONTAL = 1
        DIAGONAL = 3

        class Engine:
            def run(self):
                return 1

        def helper():
            return HORIZONTAL
    """))
    assert reindex_incremental(store, str(root), {"pkg/impl.py"}).ok
    rows = _rows(store)
    assert ("pkg/impl.py", "const", "DIAGONAL") in rows
    assert ("pkg/impl.py", "const", "VERTICAL") not in rows
    assert ("pkg/impl.py", "main", "helper") not in rows
    assert ("conftest.py", "fixture", "env") in rows  # other files untouched
    store.close()


def test_replace_file_without_symtab_clears(tmp_path):
    """A caller with no extraction data (hand-built nodes) must CLEAR the file's
    rows — stale entries would mis-suppress holes / mis-root tests."""
    root = _tree(tmp_path)
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    assert any(f == "pkg/impl.py" for f, _, _ in _rows(store))
    store.replace_file("pkg/impl.py", [], [])
    assert not any(f == "pkg/impl.py" for f, _, _ in _rows(store))
    store.close()


def test_symtab_names_union_and_exclusion(tmp_path):
    root = _tree(tmp_path)
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root), streaming=False).ok
    assert "env" in store.symtab_names("fixture")
    consts = store.symtab_names("const")
    assert {"HORIZONTAL", "VERTICAL", "LIMIT"} <= consts
    # the single-file swap: exclude impl.py's contribution
    assert store.symtab_names("const", exclude_file="pkg/impl.py") < consts
    store.close()
