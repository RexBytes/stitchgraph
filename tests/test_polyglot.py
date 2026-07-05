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


def test_inheritance_imports_and_tests(tmp_path):
    (tmp_path / "Shapes.java").write_text(
        "class Animal { int sound(){ return 0; } }\n"
        "class Dog extends Animal { int bark(){ return 1; } }\n")
    (tmp_path / "util.js").write_text(
        "export function helper(){ return 1; }\nfunction jsDead(){ return 2; }\n")
    (tmp_path / "app.js").write_text(
        'import { helper } from "./util";\nexport function go(){ return helper(); }\n')
    (tmp_path / "svc_test.go").write_text(
        "package m\nfunc TestSvc(t *T){ used() }\nfunc used() int { return 1 }\n"
        "func goDead() int { return 2 }\n")
    from stitchgraph.core.model import Relation
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    # inheritance
    inh = {(e.src.split("::")[-1], e.dst_id.split("::")[-1])
           for e in store.resolved_edges(Relation.INHERITS)}
    assert ("Dog", "Animal") in inh
    assert any(d.endswith("::Dog") for d in sg.impact_of(store, "Animal").result["blast_radius"])
    # imports
    imp = {(e.src.split("::")[-1], e.dst_id.split("::")[-1])
           for e in store.resolved_edges(Relation.IMPORTS)}
    assert ("app", "helper") in imp
    # go test entry: TestSvc is a root, used is reached, goDead is dead
    stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "goDead" in stale and "used" not in stale and "TestSvc" not in stale
    assert "jsDead" in stale  # unused JS, not exported
    store.close()


def test_c_ruby_bash_imports(tmp_path):
    """v3.34.0: the three remaining import gaps. C/C++ quoted #include -> the header's
    module (system <...> headers emit nothing); Ruby require/require_relative and Bash
    source/. resolve their path argument's stem to the target module. External targets
    ("json", <stdio.h>) must produce no edge at all — precision over recall."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "main.c").write_text(
        '#include "util.h"\n#include <stdio.h>\nint main(void){ return helper(); }\n')
    (tmp_path / "util.h").write_text("int helper(void);\n")
    (tmp_path / "app.rb").write_text(
        'require "json"\nrequire_relative "./lib/helper"\ndef run\n  Helper.new\nend\n')
    (tmp_path / "lib" / "helper.rb").write_text("class Helper\nend\n")
    (tmp_path / "run.sh").write_text("source ./lib/common.sh\nsetup_env\n")
    (tmp_path / "lib" / "common.sh").write_text('setup_env() {\n  echo hi\n}\n')
    from stitchgraph.core.model import Relation
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    imp = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.IMPORTS)}
    assert ("main.c::main", "util.h::util") in imp
    assert ("app.rb::app", "lib/helper.rb::helper") in imp
    assert ("run.sh::run", "lib/common.sh::common") in imp
    # externals: no phantom targets minted for stdio / json
    all_ids = set(store.all_node_ids())
    assert not any("stdio" in i or "json" in i for i in all_ids)
    store.close()
