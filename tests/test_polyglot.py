"""Polyglot extraction (tree-sitter): JS / TS / Rust / Bash in one graph."""

from __future__ import annotations

from pathlib import Path

import pytest

import stitchgraph as sg

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")
from stitchgraph.core.extract import treesitter  # noqa: E402

if not treesitter.HAS_TREE_SITTER:
    pytest.skip("tree-sitter not installed", allow_module_level=True)


def _project(root: Path) -> sg.Store:
    (root / "app.js").write_text(
        "export function jsEntry(){ return jsHelper(); }\n"
        "function jsHelper(){ return 1; }\n"
        "function jsDead(){ return 2; }\n"
    )
    (root / "lib.rs").write_text(
        "pub fn rsEntry() -> i32 { rsHelper(1) }\n"
        "fn rsHelper(x: i32) -> i32 { x + 1 }\n"
        "fn rsDead() -> i32 { 0 }\n"
    )
    (root / "run.sh").write_text(
        "deploy(){ build; }\n"
        "build(){ echo hi; }\n"
    )
    store = sg.Store(":memory:")
    sg.reindex(store, str(root))
    return store


def test_languages_extracted(tmp_path):
    with _project(tmp_path) as store:
        names = {n.name for n in store.all_nodes_full()}
        assert {"jsEntry", "jsHelper", "rsEntry", "rsHelper", "deploy", "build"} <= names


def test_call_graph_per_language(tmp_path):
    with _project(tmp_path) as store:
        # rust entry calls helper; trace works.
        assert sg.trace_path(store, "rsEntry", "rsHelper").ok
        # js entry calls helper.
        assert sg.trace_path(store, "jsEntry", "jsHelper").ok


def test_dead_code_across_languages(tmp_path):
    with _project(tmp_path) as store:
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "jsDead" in stale and "rsDead" in stale     # unused -> dead
        assert "jsHelper" not in stale and "rsHelper" not in stale  # reached -> live


def test_go_java_ruby_php(tmp_path):
    (tmp_path / "m.go").write_text(
        "package m\nfunc GoEntry() int { return goWork() }\n"
        "func goWork() int { return 1 }\nfunc goDead() int { return 0 }\n")
    (tmp_path / "C.java").write_text(
        "class C { public int javaRun(){ return javaHelp(); }\n"
        "  int javaHelp(){ return 1; } int javaDead(){ return 2; } }\n")
    (tmp_path / "s.rb").write_text(
        "def rbEntry\n rbWork\nend\ndef rbWork\n 1\nend\ndef rbDead\n 0\nend\n")
    (tmp_path / "a.php").write_text(
        "<?php\nfunction phpEntry(){ return phpWork(); }\n"
        "function phpWork(){ return 1; }\nfunction phpDead(){ return 2; }\n")
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    names = {n.name for n in store.all_nodes_full()}
    assert {"GoEntry", "javaRun", "rbEntry", "phpEntry"} <= names
    # call graph per language
    for a, b in [("GoEntry", "goWork"), ("javaRun", "javaHelp"),
                 ("rbEntry", "rbWork"), ("phpEntry", "phpWork")]:
        assert sg.trace_path(store, a, b).ok, f"{a}->{b}"
    # dead code across all four
    stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert {"goDead", "C.javaDead", "rbDead", "phpDead"} <= stale
    store.close()


def test_no_cross_language_false_links(tmp_path):
    # jsHelper and rsHelper share a suffix but must not link across languages.
    (tmp_path / "a.js").write_text("function shared(){ return 1; }\nfunction useJs(){ return shared(); }\n")
    (tmp_path / "b.rs").write_text("fn shared() -> i32 { 1 }\nfn useRs() -> i32 { shared() }\n")
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    js_shared = next(n.id for n in store.nodes_by_name("shared") if n.id.endswith("a.js::shared"))
    callers = {e.src for e in store.callers_of(js_shared)}
    assert all(".js" in c for c in callers)  # only the JS caller, not the Rust one
    store.close()


def test_js_fetch_to_backend_route(tmp_path):
    """Frontend JS fetch links to a backend route → full-stack trace."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "__init__.py").write_text("")
    (tmp_path / "api" / "views.py").write_text(
        "app = object()\n@app.get('/api/users')\n"
        "def list_users():\n    return db.execute('SELECT email FROM users')\n")
    (tmp_path / "client.js").write_text(
        "export async function loadUsers(){ const r = await fetch('/api/users'); return r.json(); }\n")
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    res = sg.trace_path(store, "loadUsers", "users")
    assert res.ok, res.review_reasons
    assert res.result[0].endswith("client.js::loadUsers")
    assert res.result[-1] == "db::users"
    store.close()
