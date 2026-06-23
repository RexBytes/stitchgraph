"""find_similar: token-similarity retrieval over the graph."""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg
from stitchgraph.core.similar import tokenise


def test_tokenise_splits_identifiers():
    assert "sql" in tokenise("parse_sql_query")
    assert "resolver" in tokenise("SqlResolver")
    assert "self" not in tokenise("self.value")  # stopword dropped


def _project(root: Path) -> sg.Store:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text(
        "def parse_sql_query(text):\n"
        '    "Parse a SQL query and extract the table names."\n'
        "    return tables(text)\n\n"
        "def tables(text):\n"
        "    return []\n\n"
        "def render_widget(w):\n"
        '    "Draw a UI widget on screen."\n'
        "    return str(w)\n"
    )
    store = sg.Store(":memory:")
    sg.reindex(store, str(root))
    return store


def test_find_similar_ranks_relevant_first(tmp_path):
    with _project(tmp_path) as store:
        res = sg.find_similar(store, "extract tables from a sql statement", limit=3)
        assert res.ok
        assert res.result[0]["id"].endswith("::parse_sql_query")


def test_find_similar_empty_query_refuses(tmp_path):
    with _project(tmp_path) as store:
        res = sg.find_similar(store, "!!! ???", limit=3)
        assert not res.ok and res.needs_review
