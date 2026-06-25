"""Regression tests for defects found by the multi-model review panels.

Each test pins a specific finding so it can never silently return. The panel and
finding are named in the test so `REVIEW_HISTORY.md` stays traceable to code.

Panel A (opus · sonnet · haiku): the late-stage defects were *symmetry gaps* — a
guard or liveness rule present in one extractor/resolver but missing in a sibling.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import stitchgraph as sg
from stitchgraph.core.model import Relation


def _mk(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return root


# -- Panel A / opus (HIGH): EMITS/HANDLES must propagate liveness --------------
def test_event_handlers_reachable_from_emit_are_not_stale(tmp_path):
    """A handler registered + fired from a live (exported) entry point must not be
    flagged dead. EMITS/HANDLES were missing from LIVENESS_RELATIONS — a symmetry
    gap with ROUTES_TO/RENDERS, which already cross a decoupled boundary."""
    _mk(tmp_path, {
        "app/__init__.py": '__all__ = ["run"]\nfrom .main import run\n',
        "app/main.py": """
            bus = object()

            def on_tick(x):
                return compute(x)

            def compute(x):
                return x + 1

            def run():
                bus.on("tick", on_tick)
                bus.emit("tick", 1)
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "on_tick" not in stale   # reached via EMITS -> event -> HANDLES
        assert "compute" not in stale   # reached via the handler


# -- Panel A / haiku (HIGH): resolvers must link to *all* candidates -----------
def test_ambiguous_django_route_links_all_handlers(tmp_path):
    """When a route handler name matches several symbols, edge to ALL of them
    (AMBIGUOUS), never drop the edge — dropping risks calling a live handler dead.
    The `if len(cands) == 1:` guards had no else branch."""
    _mk(tmp_path, {
        "proj/__init__.py": "",
        "proj/urls.py": """
            from django.urls import path
            urlpatterns = [path('a/', view)]
        """,
        "proj/a.py": "def view():\n    return 1\n",
        "proj/b.py": "def view():\n    return 2\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        edges = store.resolved_edges(Relation.ROUTES_TO)
        targets = {e.dst_id for e in edges}
        assert len(targets) == 2  # both same-named handlers linked
        assert all(e.provenance.value == "ambiguous" for e in edges)


# -- Panel A / sonnet F1 (HIGH): CLI must preserve param types -----------------
def test_cli_preserves_int_and_bool_param_types():
    """The CLI rebuilt every param as `str`, so `--limit 5` arrived as "5" (a
    TypeError on int comparison) and bool flags inverted. Types must be preserved."""
    typer = pytest.importorskip("typer")
    from stitchgraph.adapters.cli import _make_command
    from stitchgraph.core.operations import registry

    by_name = {op.name: op for op in registry()}
    limit_anno = _make_command(typer, by_name["get_matrix"]).__signature__.parameters["limit"].annotation
    precise_anno = _make_command(typer, by_name["reindex"]).__signature__.parameters["precise"].annotation
    assert limit_anno is int
    assert precise_anno is bool


def test_cli_get_matrix_limit_is_parsed_as_int(tmp_path):
    """End-to-end: `get-matrix --limit` must not crash on the int comparison."""
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from stitchgraph.adapters.cli import build_app

    _mk(tmp_path, {"pkg/__init__.py": "",
                   "pkg/m.py": "def a():\n    return b()\ndef b():\n    return 1\n"})
    db = str(tmp_path / "g.db")
    runner = CliRunner()
    app = build_app()
    assert runner.invoke(app, ["reindex", str(tmp_path), "--db", db]).exit_code == 0
    # scope "pkg" has > 1 node, so --limit 1 should *refuse cleanly*, not TypeError.
    res = runner.invoke(app, ["get-matrix", "pkg", "--limit", "1", "--db", db])
    assert res.exit_code == 0, res.output
    assert "limit" in res.output.lower()


# -- Panel A / sonnet F2 (MEDIUM): scan red is gated by provenance -------------
def test_live_stub_via_inferred_path_is_orange_not_red(tmp_path):
    """A stub reachable only through an INFERRED resolver edge (a heuristic route)
    is uncertainly-live, so it caps at ORANGE — only an EXTRACTED-reachable stub
    shouts RED (envelope §7 provenance ceiling)."""
    _mk(tmp_path, {
        "svc/__init__.py": "",
        "svc/views.py": """
            app = object()

            @app.get('/x')
            def handler():
                raise NotImplementedError
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stubs = [i for i in sg.scan(store).result if i["kind"] == "live_stub"]
        assert stubs and all(i["urgency"] == "orange" for i in stubs)


def test_live_stub_via_extracted_path_is_red(tmp_path):
    """The RED case is preserved: a stub reached by a direct CALL from a live entry
    point is certainly-live and must still shout RED."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["run"]\nfrom .m import run\n',
        "pkg/m.py": """
            def run():
                return helper()

            def helper():
                raise NotImplementedError
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stubs = [i for i in sg.scan(store).result if i["kind"] == "live_stub"]
        assert any(i["urgency"] == "red" for i in stubs)


# -- Panel A / sonnet F3 (MEDIUM): generic impl names the base type ------------
def test_rust_generic_impl_attributes_methods_to_base_type(tmp_path):
    """`impl<T> Container<T>` must attach methods to `Container`, not the type
    parameter `T` (the trailing identifier inside the type arguments)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.rs": """
            struct Container<T> { val: T }
            impl<T> Container<T> {
                fn get(&self) -> &T { &self.val }
            }
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ids = set(store.all_node_ids())
        assert "lib.rs::Container.get" in ids
        assert "lib.rs::T.get" not in ids


# -- Panel A / sonnet F4 (MEDIUM): public methods of an exported class are live -
def test_public_methods_of_exported_class_are_not_stale(tmp_path):
    """Public methods of an exported class are public API (external callers), so
    they are never dead for lack of an internal caller."""
    _mk(tmp_path, {
        "lib/__init__.py": '__all__ = ["Widget"]\nfrom .core import Widget\n',
        "lib/core.py": """
            class Widget:
                def public_method(self):
                    return self._helper()

                def _helper(self):
                    return 1

                def never_called_externally(self):
                    return 42
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Widget.public_method" not in stale
        assert "Widget.never_called_externally" not in stale  # public API, unused


# -- Panel A / sonnet F5 (LOW): config threshold is actually wired -------------
def test_config_review_threshold_gates_needs_review(tmp_path):
    """`[review] threshold` in stitchgraph.toml must actually move the needs_review
    boundary — it was a documented knob that nothing consumed."""
    from stitchgraph.core import envelope
    from stitchgraph.core.config import load_config

    original = envelope.REVIEW_THRESHOLD
    try:
        (tmp_path / "stitchgraph.toml").write_text("[review]\nthreshold = 0.5\n")
        load_config(tmp_path)
        assert envelope.REVIEW_THRESHOLD == 0.5
        # A result at confidence 0.6 is now above threshold -> not flagged.
        assert envelope.ok({}, confidence=0.6).needs_review is False
    finally:
        envelope.set_review_threshold(original)


# -- Panel B / opus + sonnet (HIGH): tree-sitter twin of F4 --------------------
def test_public_methods_of_exported_jsts_class_are_not_stale(tmp_path):
    """The tree-sitter extractor must mirror the Python extractor: public methods
    of an exported JS/TS class are public API, never dead for lack of an internal
    caller. (opus and sonnet converged on this — the tree-sitter twin of F4.)"""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "widget.ts": """
            export class Widget {
                fetchUser(id) { return id; }
                render() { return 1; }
            }
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Widget.fetchUser" not in stale  # public API of an exported class
        assert "Widget.render" not in stale


# -- Panel B / haiku (MEDIUM): same-path routes must all be linked -------------
def test_html_form_links_to_all_routes_sharing_a_path(tmp_path):
    """A form action `/items` with both GET and POST handlers must SUBMITS_TO both
    routes — collapsing by path (dict setdefault) dropped one, so a `trace_path`
    through the missed method silently failed."""
    _mk(tmp_path, {
        "app/__init__.py": "",
        "app/views.py": """
            app = object()

            @app.get('/items')
            def list_items():
                return 1

            @app.post('/items')
            def create_item():
                return 2
        """,
        "templates/items.html":
            '<form action="/items" method="post"><input name="x"></form>\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        subs = [e for e in store.resolved_edges(Relation.SUBMITS_TO)]
        targets = {e.dst_id.split("::")[-1] for e in subs}
        assert {"route:GET /items", "route:POST /items"} <= targets


# -- Panel B / sonnet (LOW): risk() is polyglot, not Python-only ---------------
def test_risk_uses_non_python_git_history(tmp_path):
    """`risk()` churn must cover every indexed source language, not just .py — the
    git scraper hard-filtered to `.py`, so polyglot repos got an empty/misleading
    refuse."""
    pytest.importorskip("tree_sitter_language_pack")
    import os
    import subprocess

    from stitchgraph.core import gitrisk

    (tmp_path / "app.js").write_text("export function go(){ return 1; }\n")
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       capture_output=True, env=env, check=True)

    git("init")
    git("add", "-A")
    git("commit", "-m", "add js")
    assert gitrisk.churn(str(tmp_path)).get("app.js") == 1  # .js counted, not skipped


def test_risk_counts_unicode_filenames(tmp_path):
    """git octal-escapes AND double-quotes non-ASCII paths under the default
    core.quotepath=true (`"caf\\303\\251.py"`), so the trailing quote defeated the
    `.endswith(_SRC_EXTS)` filter and unicode-named source files silently vanished from
    churn/cochange/risk (panel NNN). `-c core.quotepath=false` prints them literally."""
    import os
    import subprocess

    from stitchgraph.core import gitrisk

    (tmp_path / "café.py").write_text("def a():\n    return 1\n")
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       capture_output=True, env=env, check=True)

    git("init")
    git("add", "-A")
    git("commit", "-m", "c1")
    (tmp_path / "café.py").write_text("def a():\n    return 2\n")
    git("commit", "-am", "c2")
    assert gitrisk.churn(str(tmp_path)).get("café.py") == 2  # unicode name counted, not dropped


# -- Panel C (HIGH): tree-sitter callback role symmetry gap ---------------------
def test_tree_sitter_methods_of_external_base_classes_get_callback_role(tmp_path):
    """Methods of a class that inherits from an external (framework) base class
    should have the 'callback' role so they're treated as entry points and never
    flagged dead. Python extractor did this; tree-sitter didn't — a symmetry gap.
    Framework callbacks (React.Component.render, EventEmitter.onError, etc.) are
    invoked by the framework, not internally, so they must not be flagged stale."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.tsx": """
            class MyButton extends React.Component {
                handleClick() {
                    console.log("clicked");
                }
                render() {
                    return <button onClick={this.handleClick}>Click</button>;
                }
            }
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        # Both callback methods must not be stale, even though they're not called
        # internally (React/framework invokes them).
        assert "MyButton.handleClick" not in stale
        assert "MyButton.render" not in stale
        # CARDINAL (panel PPP): the class itself must also stay live — a framework
        # subclass is framework-instantiated. The tree-sitter callback-role pass marked
        # the methods but not the enclosing class, so the class was a false-dead.
        assert "MyButton" not in stale


def test_tree_sitter_callback_class_itself_is_live_across_languages(tmp_path):
    """The 'method live, class dead' cardinal false-dead (panel PPP): tree-sitter
    `_seed_callback_roles` marked callback *methods* but not the enclosing *class*, so a
    framework subclass not otherwise rooted (Rails controller, etc.) had its class flagged
    dead while its methods were live. Mirror the Python extractor's class-rooting pass."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "handler.rb": (
            "class MyController < ApplicationController\n"
            "  def index\n    1\n  end\n"
            "  def get_data\n    2\n  end\nend\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "MyController" not in stale          # class live (was false-dead)
        assert "MyController.index" not in stale     # methods live
        assert "MyController.get_data" not in stale


def test_cpp_framework_subclass_class_and_methods_are_live(tmp_path):
    """CARDINAL (panel QQQ/RRR): C/C++ map every `function_definition` to FUNCTION, even
    for methods in a class body — so the method-based class-rooting passes (which key on
    METHOD) skipped C++ members, leaving a live framework subclass (a Qt widget) and its
    framework-invoked methods flagged dead. In-class member functions are now normalized to
    METHOD so every rooting pass works for every language."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "widget.cpp": (
            "class MyWidget : public QWidget {\n"
            "    void paintEvent() { return; }\n"
            "    void show() { return; }\n};\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "MyWidget" not in stale            # class live (was false-dead)
        assert "MyWidget.paintEvent" not in stale  # framework-invoked methods live
        assert "MyWidget.show" not in stale


def test_csharp_internal_main_class_is_live(tmp_path):
    """CARDINAL (panel RRR): idiomatic C# `internal class Program { static void Main }` —
    `Main` isn't public so the class never gets the `exported` role, and (unlike the Python
    extractor) no tree-sitter pass rooted the enclosing class of a `main`-role method, so
    the live entry-point class was flagged dead. `_seed_main_classes` now mirrors the
    Python rescue."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Program.cs": (
            "internal class Program {\n"
            "    private static void Main(string[] args) { var s = new Service(); s.Start(); }\n"
            "}\n"
            "internal class Service { public void Start() { } }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Program" not in stale            # entry-point class live (was false-dead)


def test_exported_interface_trait_members_are_live(tmp_path):
    """CARDINAL (panel SSS): members of an exported interface/trait are public API but are
    implicitly public (no visibility token), so `_roles` never marks them exported and the
    JS/TS-gated class pass skips them — leaving a `pub trait`/`public interface` member
    (incl. body-bearing default methods) flagged dead. `_seed_exported_interface_methods`
    down-propagates `exported` from the exported container."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.rs": "pub trait Summary {\n    fn summarize(&self) -> String { String::from(\"d\") }\n}\n",
        "Greeter.java": ("public interface Greeter {\n"
                         "    String greet();\n"
                         "    default String greetLoud() { return greet() + \"!\"; }\n}\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Summary.summarize" not in stale       # Rust trait default method live
        assert "Greeter.greet" not in stale            # Java abstract interface method live
        assert "Greeter.greetLoud" not in stale        # Java default interface method live


def test_cpp_class_in_h_header_is_live(tmp_path):
    """CARDINAL (panel TTT): `.h` was mapped to the C grammar, which has no class/namespace/
    template, so a C++ class in a `.h` header mis-parsed and was flagged dead (`.h` is the
    dominant C++ header extension). `.h` is now resolved to C or C++ by content."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "engine.h": "class Engine {\n    void start() { ignite(); }\n    void ignite() { return; }\n};\n",
        "main.cpp": "int main(){ Engine e; e.start(); return 0; }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Engine" not in stale                   # C++ class in .h header live
        assert "Engine.ignite" not in stale


def test_bash_script_function_named_like_file_stem_stays_live(tmp_path):
    """CARDINAL (panel SSS): a bash `run.sh` defining `function run()` collides ids
    (`run.sh::run` for both the MODULE node and the function), and the store's
    INSERT OR REPLACE dropped the MODULE node — and with it the `script` role (which lives
    ONLY on the module for bash), flagging every function dead. A shadowed module's roles
    are now merged into the surviving symbol node."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "run.sh": "#!/usr/bin/env bash\nfunction run() { helper; }\nfunction helper() { echo done; }\nrun\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "run" not in stale and "helper" not in stale  # script role survives collision


def test_js_test_file_class_named_like_stem_stays_live(tmp_path):
    """CARDINAL (panel TTT): a JS test file `tests/Service.js` defining `class Service`
    collides ids with the MODULE node, dropping the `test` role (the test variant of the
    bash collision). The module's roles are now merged into the surviving class node."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "tests/Service.js": ("class Service { run() { this.doWork(); } doWork() { return 1; } }\n"
                             "const svc = new Service();\nsvc.run();\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale_ids = {c["id"] for c in sg.find_stale(store).result}
        assert "tests/Service.js::Service" not in stale_ids   # test role survives collision


def test_js_reexport_does_not_root_same_named_symbol_in_other_language(tmp_path):
    """Precision (panel TTT LOW): `export { Widget }` is JS/TS-only, so a same-named dead
    class in another language must NOT be marked exported by it (cross-language false
    negative). The reexport pass is now language-guarded."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "widget.ts": "class Widget { render() { return 1; } }\nexport { Widget };\n",
        "rb_widget.rb": "class Widget\n  def render\n    1\n  end\nend\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale_ids = {c["id"] for c in sg.find_stale(store).result}
        assert "rb_widget.rb::Widget" in stale_ids     # genuinely-dead Ruby class flags


def test_cpp_struct_with_methods_in_h_used_cross_file_is_live(tmp_path):
    """CARDINAL (panel UUU): a C++ `struct` with member functions in a `.h` header (no
    class/namespace/template marker) was sniffed as C and bucketed in language 'c', while the
    `.cpp` using it is 'cpp' — and resolution binds within a language, so the cross-file use
    never resolved and the struct flagged dead. C and C++ now share one resolution bucket."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "v.h": "#pragma once\nstruct V3 { double x,y,z; double len() const; };\n",
        "m.cpp": ("#include \"v.h\"\ndouble V3::len() const { return x*x+y*y+z*z; }\n"
                  "int main(){ V3 v{1,2,2}; return (int)v.len(); }\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale_ids = {c["id"] for c in sg.find_stale(store).result}
        assert "v.h::V3" not in stale_ids             # struct used cross-file is live


def test_c_header_decl_used_from_c_file_stays_live(tmp_path):
    """Regression guard for the C/C++ resolution-bucket unification: a pure-C header decl
    called from a `.c` file must still bind (the unification must not regress pure-C)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "util.h": "int add(int a, int b);\n",
        "prog.c": "#include \"util.h\"\nint add(int a, int b){ return a+b; }\nint main(){ return add(1,2); }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale_ids = {c["id"] for c in sg.find_stale(store).result}
        assert "util.h::add" not in stale_ids         # header decl bound to .c use


def test_rust_trait_impl_method_invoked_via_sugar_is_live(tmp_path):
    """CARDINAL (panel UUU): a method in a Rust `impl Trait for X` block can't carry `pub`
    and is invoked via language sugar (`Display::fmt` through `{}`), so it got no `exported`
    role and no call node — flagged dead. `_seed_trait_impl_methods` roots trait-impl methods
    as callback (framework/contract-invoked); a bare inherent `impl X` is NOT rooted."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.rs": ("use std::fmt;\nstruct Point { x: i32, y: i32 }\n"
                   "impl fmt::Display for Point {\n"
                   "    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result { write!(f, \"{}\", self.x) }\n"
                   "}\nfn main() { let p = Point{x:1,y:2}; println!(\"{}\", p); }\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Point.fmt" not in stale               # trait-impl method live


def test_csharp_top_level_statements_root_local_functions(tmp_path):
    """CARDINAL (panel WWW): C# top-level statements (the default .NET 6+ `Program.cs`) ARE
    the program's Main entry point (like bash's top-level body / Python `__main__`), but local
    functions in a top-level program had no root and were flagged dead. A `.cs` with
    `global_statement` children is now rooted as a script."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Program.cs": ("int x = Compute(5);\nSystem.Console.WriteLine(x);\n"
                       "int Compute(int n) { return Square(n) + 1; }\n"
                       "int Square(int n) { return n * n; }\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Compute" not in stale and "Square" not in stale


def test_class_with_any_reachable_member_is_not_flagged_dead(tmp_path):
    """CARDINAL (panel XXX): a live method implies a live class — a class must never be
    flagged dead while any of its members is reachable. This is the general backstop for the
    class-vs-member family (covers C# partial classes split across files: a non-public part
    whose member is reached via the public part). A class with ALL members dead still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Service.cs": "public partial class Service { public string Process(string i){ return Normalize(i); } }\n",
        "Service.Helpers.cs": "partial class Service { string Normalize(string s){ return s.Trim(); } }\n",
        "Dead.cs": "class Dead { void unused() { } }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale_ids = {c["id"] for c in sg.find_stale(store).result}
        # the partial part whose member is reachable from the public part must stay live
        assert "Service.Helpers.cs::Service" not in stale_ids
        # but a class with no reachable member is still correctly flagged
        assert "Dead.cs::Dead" in stale_ids


def test_lookup_by_non_utf8_name_refuses_without_crashing():
    """CRASH/envelope (panel XXX): a symbol name with a lone surrogate (invalid-UTF-8 argv
    via surrogateescape) or an embedded NUL is bound into SQLite, which raises on encode. The
    store lookups must refuse (empty/None) so the op returns a Result, not a traceback."""
    from stitchgraph.core import operations as ops
    from stitchgraph.core.model import Node, NodeKind
    with sg.Store(":memory:") as store:
        store.add_node(Node(id="m.py::f", kind=NodeKind.FUNCTION, name="f"), file="m.py")
        assert store.nodes_by_name("\udcff\udcfe") == []      # lone surrogate -> no match
        assert store.nodes_by_name("a\x00b") == []            # embedded NUL -> no match
        assert store.get_node("\udcff") is None
        r = ops.impact_of(store, "\udcff\udcfe")              # public op must not raise
        assert r.ok is False


def test_reindex_survives_delete_table_sql_in_source(tmp_path):
    """CRASH (panel crash-sweep): a `DELETE TABLE ...` SQL string in analyzed source made
    sqlglot return a Delete whose `.this` is a bool, so the SQL resolver's `.find_all` raised
    AttributeError and aborted the whole reindex. Resolvers are heuristic enrichment and must
    never abort reindex — `run_resolvers` now skips a crashing resolver, and the SQL guard
    requires an Expression."""
    _mk(tmp_path, {"m.py": 'def wipe(db):\n    db.execute("DELETE TABLE archived WHERE 1=1")\n'})
    with sg.Store(":memory:") as store:
        res = sg.reindex(store, str(tmp_path))       # must not raise / abort
        assert res.ok
        assert "m.py::wipe" in set(store.all_node_ids())


def test_ingest_trace_bounds_go_coverprofile_span(tmp_path):
    """DoS (panel ZZZ): a corrupt Go coverprofile with a huge end-line made `_parse_go`
    materialize `range()` into a multi-GB set (OOM). The span is now bounded; a nonsensical
    line is dropped, honouring the 'empty on any problem' contract — no OOM, no crash."""
    from stitchgraph.core.runtime import load_coverage
    bad = tmp_path / "corrupt.go.cov"
    bad.write_text("mode: set\nfoo.go:1.1,999999999.1 1 1\n")
    cov, _ = load_coverage(str(bad))               # must return fast without OOM
    assert all(len(lines) <= 1_000_001 for lines in cov.values())


def test_path_ops_refuse_on_hostile_path_without_crashing(tmp_path):
    """CRASH/envelope (panels YYY/ZZZ): an over-long path, embedded NUL, or lone surrogate
    passed to a path-taking op raised OSError/ValueError/UnicodeError from a stat()/bind
    instead of returning a Result. reindex degrades to an empty index (like a missing path);
    ingest_trace/risk refuse cleanly."""
    long_p, nul_p, sur_p = "x" * 5000, "a\x00b", "\udc80"
    with sg.Store(":memory:") as store:
        for p in (long_p, nul_p, sur_p):
            r = sg.reindex(store, p)                 # must not raise
            assert r.ok and r.result["nodes"] == 0   # empty index, not a crash
            assert hasattr(sg.ingest_trace(store, p), "ok")  # refuse, not crash
            assert hasattr(sg.risk(store, p), "ok")


def test_malformed_threshold_does_not_disable_review(tmp_path):
    """Robustness (panel ZZZ): a `stitchgraph.toml` with `[review] threshold = "nan"` (or
    out-of-range) would make `confidence < nan` always False and silently disable
    needs_review. The threshold now clamps to the default on a non-[0,1] value."""
    from stitchgraph.core import config as cfg
    (tmp_path / "stitchgraph.toml").write_text('[review]\nthreshold = "nan"\n')
    c = cfg.load_config(str(tmp_path))
    assert c.threshold == 0.80


def test_reindex_survives_deep_expression_in_tree_sitter_resolver(tmp_path):
    """The route resolvers (express/jsfetch/spring) run their OWN recursive descent over a
    tree-sitter tree, bypassing ResolveContext.parse()'s RecursionError guard — and
    run_resolvers had no guard, so a deep `.js` expression aborted the whole reindex (panel
    QQQ/RRR, the resolver-side analogue of the per-file extractor guard). A guard in
    run_resolvers now degrades to 'no extra edges' instead of aborting."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    deep = "const T = " + " + ".join(['"x"'] * 3000) + ";\n"
    (tmp_path / "build.js").write_text(deep + "app.post('/save', h);\nfunction h(){ return T; }\n")
    (tmp_path / "other.py").write_text("def other():\n    return 1\n")
    with sg.Store(":memory:") as store:
        res = sg.reindex(store, str(tmp_path))       # must not raise / abort
        assert res.ok
        assert "other.py::other" in set(store.all_node_ids())


def test_reindex_survives_pathologically_deep_python_file(tmp_path):
    """A deep-but-valid AST (a huge flat `a + a + ... ` chain, realistic in generated
    code) overflows the recursive extractor walk with RecursionError. That wasn't in the
    per-file `except`, and the walk ran outside the try, so ONE bad file aborted the whole
    reindex and left an empty DB — defeating the per-file-skip contract (panel OOO)."""
    deep = "QUERY = (" + " + ".join(['"S "'] * 2000) + ")\n"
    (tmp_path / "gen.py").write_text(deep)
    (tmp_path / "other.py").write_text("def other():\n    return 1\n")
    with sg.Store(":memory:") as store:
        res = sg.reindex(store, str(tmp_path))       # must not raise / abort
        assert res.ok
        # the good file is still indexed — the pathological one is skipped, not fatal
        assert "other.py::other" in set(store.all_node_ids())


# -- Panel C / opus (MEDIUM): single-arg signal .connect(handler) ---------------
def test_signal_connect_handler_is_not_stale(tmp_path):
    """`signal.connect(handler)` (blinker/Django signals/Qt) is single-arg with a
    function, keyed on the receiver object — the resolver required a string event
    name and >=2 args, so registered signal handlers were flagged dead despite the
    docstring promising `.connect(handler)` support."""
    _mk(tmp_path, {
        "app/__init__.py": '__all__ = ["run"]\nfrom .main import run\n',
        "app/main.py": """
            post_save = object()

            def on_save(sender, **kw):
                return audit(sender)

            def audit(x):
                return x

            def run():
                post_save.connect(on_save)
                post_save.send(None)
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "on_save" not in stale   # registered via .connect, fired via .send
        assert "audit" not in stale     # reached through the handler


# -- Panel C / sonnet (MEDIUM): SQL CTE names aren't real tables ----------------
def test_sql_cte_is_not_a_phantom_table(tmp_path):
    """A `WITH recent AS (...)` CTE is a query-local alias, not a db table — it must
    not become a phantom `db::recent` node (it parses as a Table when referenced)."""
    pytest.importorskip("sqlglot")
    _mk(tmp_path, {
        "q/__init__.py": "",
        "q/svc.py": """
            def report():
                return db.execute(
                    "WITH recent AS (SELECT id FROM orders) SELECT id FROM recent")
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        from stitchgraph.core.model import NodeKind
        tables = {n.name for n in store.nodes_by_kind(NodeKind.DB_TABLE)}
        assert "orders" in tables       # the real table
        assert "recent" not in tables   # the CTE alias is not a table


# -- Panel D / opus + sonnet (MEDIUM): incremental re-resolution over-approximates
def test_replace_file_links_ambiguous_hole_to_all_candidates():
    """`_resolve_worklist` (the incremental path) must link an ambiguous hole to
    *every* candidate as AMBIGUOUS, like the extractors — resolving to just one
    could flag the other (live) candidate dead."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.reach import reachable_from
    from stitchgraph.core.store import Store

    with Store(":memory:") as s:
        s.add_node(Node(id="b.py::helper", kind=NodeKind.FUNCTION, name="helper"), file="b.py")
        s.add_node(Node(id="c.py::helper", kind=NodeKind.FUNCTION, name="helper"), file="c.py")
        caller = Node(id="a.py::caller", kind=NodeKind.FUNCTION, name="caller")
        hole = Edge(src="a.py::caller", relation=Relation.IMPORTS, dst_symbol="helper",
                    dst_id=None, provenance=Provenance.INFERRED)
        s.replace_file("a.py", [caller], [hole])
        targets = {e.dst_id for e in s.resolved_edges(Relation.IMPORTS)}
        assert targets == {"b.py::helper", "c.py::helper"}  # both, not one
        assert {"b.py::helper", "c.py::helper"} <= reachable_from(s, {"a.py::caller"})


# -- Panel D / haiku (LOW): tree-sitter must model self-recursion CALLS ---------
def test_tree_sitter_recursive_call_is_modeled(tmp_path):
    """A recursive function CALLS itself — the tree-sitter extractor dropped the
    self-edge (filtered for all relations), unlike the Python extractor. Both must
    model the same graph; the self-filter belongs only to INHERITS/IMPORTS."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"r.js": "function fact(n){ return n <= 1 ? 1 : n * fact(n-1); }\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        fid = next(n.id for n in store.nodes_by_name("fact") if n.id.endswith("r.js::fact"))
        assert any(e.dst_id == fid for e in store.callees_of(fid))  # self-CALLS edge


# -- Panel D / sonnet (LOW): malformed coverage JSON must not crash -------------
def test_malformed_executed_lines_do_not_crash_ingest(tmp_path):
    """Non-integer `executed_lines` in a hand-crafted coverage.json must be dropped,
    not crash the later `lo <= ln <= end` range test (LCOV/Go already int-cast)."""
    _mk(tmp_path, {"pkg/__init__.py": "",
                   "pkg/m.py": "def f():\n    return 1\n"})
    bad = tmp_path / "cov.json"
    # line 2 (f's body) is a real hit; "oops"/null are coerced away, not fatal.
    bad.write_text('{"files": {"pkg/m.py": {"executed_lines": ["oops", 2, null]}}}')
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.ingest_trace(store, str(bad))  # must not raise
        assert res.ok                            # the valid line still grounds f


def test_malformed_coverage_json_shape_does_not_crash_ingest(tmp_path):
    """Valid JSON of the WRONG SHAPE (not just bad values) must not crash `_parse_json`.
    Earlier it guarded `executed_lines` values but assumed `files` was a dict and each
    entry was a dict, so `files` as a list / an entry as a string|null raised an uncaught
    AttributeError through the public `ingest_trace` (panel LLL — the content-shape twin of
    the FIFO file-type fixes). Every shape must degrade to empty, honouring the docstring's
    'empty on any problem' contract."""
    from stitchgraph.core.runtime import load_coverage
    for payload in (
        '[1, 2, 3]',                                   # top-level not a dict
        '{"files": [1, 2, 3]}',                        # files not a dict
        '{"files": {"m.py": "nope"}}',                 # entry not a dict
        '{"files": {"m.py": null}}',                   # entry null
        '{"files": {"m.py": {"executed_lines": {}}}}',  # executed_lines not a list
        '"just a string"',                             # scalar
    ):
        bad = tmp_path / "cov.json"
        bad.write_text(payload)
        cov = load_coverage(str(bad))[0]               # must return, never an exception
        assert all(not lines for lines in cov.values())  # no real hits from garbage
    # and end-to-end through the public op
    _mk(tmp_path, {"m.py": "def f():\n    return 1\n"})
    bad.write_text('{"files": [1, 2, 3]}')
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.ingest_trace(store, str(bad))         # must not raise (was AttributeError)
        assert hasattr(res, "ok")                      # a real Result, not a crash
        # empty/garbage coverage grounds nothing -> honest refuse, never a traceback


def test_malformed_stitchgraph_toml_does_not_crash_load_config(tmp_path):
    """`_load` reads stitchgraph.toml on every CLI command and chained `.get().get()` over
    its sections. A hand-edited config can put any TOML type under any key (a section as a
    string, `threshold` non-numeric, `include` an int), which raised AttributeError/
    ValueError and crashed every command. Each must fall back to its default, never crash
    (same robustness class as the coverage-JSON shape guard)."""
    from stitchgraph.core import config as cfg
    for body in (
        'entry_points = "oops"\n',                     # section is a string, not a table
        'index = 42\n',                                # section is an int
        '[review]\nthreshold = "high"\n',              # non-numeric threshold
        '[entry_points]\ninclude = 5\n',               # include not a list
        '[similar]\nembed_model = ["a", "b"]\n',       # embed_model not a string
        'review = []\norient = "x"\n',                 # multiple bad sections
    ):
        (tmp_path / "stitchgraph.toml").write_text(body)
        c = cfg.load_config(str(tmp_path))             # must not raise
        assert c.threshold == 0.80 or isinstance(c.threshold, float)
        assert isinstance(c.include, set) and isinstance(c.ignore, list)


# -- Panel E / sonnet (HIGH): C/C++ functions must be extracted -----------------
def test_c_and_cpp_functions_are_extracted(tmp_path):
    """`_name_of` read the C/C++ *return type* before the declarator, so every
    function_definition resolved to None and was silently dropped — the whole
    C/C++ call graph was empty. The declarator must be read before the type field."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "m.c": "int helper(int x){ return x+1; }\nint main(void){ return helper(41); }\n",
        "w.cpp": "class Box {\npublic:\n  int get(){ return 1; }\n};\nBox* make(){ return 0; }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ids = set(store.all_node_ids())
        assert {"m.c::helper", "m.c::main"} <= ids          # C free functions
        assert "w.cpp::Box.get" in ids                       # C++ method
        assert "w.cpp::make" in ids                          # `Box* make()` -> make, not Box
        # and the C call graph is wired
        assert any(e.dst_id == "m.c::helper" for e in store.callees_of("m.c::main"))


# -- Panel E / opus (LOW): exported-class seeding is JS/TS-only -----------------
def test_private_methods_of_public_csharp_class_stay_dead_eligible(tmp_path):
    """`_seed_exported_class_methods` must not mark Java/C# private methods exported
    (they tokenize per-method visibility). Only JS/TS inherit visibility from the
    class. A genuinely-private, unreferenced C# method should be a stale candidate."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Server.cs": "public class Server {\n"
                     "  public void Run(){ Help(); }\n"
                     "  void Help(){ return; }\n"
                     "  private void DeadPrivate(){ return; }\n}\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Server.DeadPrivate" in stale   # private + unreferenced -> dead
        assert "Server.Run" not in stale        # public API -> live


# -- Panel E / sonnet (LOW): INSERT...SELECT reads its source table ------------
def test_insert_select_marks_source_as_read(tmp_path):
    """`INSERT INTO archive SELECT ... FROM users` writes `archive` and *reads*
    `users` — the source table was wrongly labelled WRITES (top-level stmt type
    applied to every table)."""
    pytest.importorskip("sqlglot")
    _mk(tmp_path, {
        "q/__init__.py": "",
        "q/svc.py": "def archive_old():\n"
                    "    return db.execute("
                    "'INSERT INTO archive SELECT id FROM users')\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        from stitchgraph.core.model import Relation
        writes = {e.dst_id for e in store.resolved_edges(Relation.WRITES)}
        reads = {e.dst_id for e in store.resolved_edges(Relation.READS)}
        assert "db::archive" in writes      # the target
        assert "db::users" in reads         # the SELECT source
        assert "db::users" not in writes    # not a write


# -- Panel F / opus + haiku (HIGH): Ruby bare (paren-less) method calls --------
def test_ruby_bare_method_calls_are_linked(tmp_path):
    """In Ruby, `validate` (no parens, no receiver) is an idiomatic call but parses
    as a bare `identifier`, not a `call` node — those CALLS edges were dropped, so a
    method reached only that way looked dead (precision violation)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "svc.rb": "class Service\n"
                  "  def run\n    validate\n    x = 1\n    y = x\n    process(x)\n  end\n"
                  "  def validate\n    1\n  end\n"
                  "  def process(n)\n    n\n  end\n"
                  "end\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        callees = {e.dst_id.split("::")[-1] for e in store.callees_of("svc.rb::Service.run")}
        assert "Service.validate" in callees   # bare call linked
        assert "Service.process" in callees     # paren call still linked
        assert sg.trace_path(store, "run", "validate").ok


# -- Panel F / opus (LOW): trace_path "no path" is a refusal, not vacuous ok ----
def test_trace_path_no_path_is_not_ok(tmp_path):
    """A genuine 'no path' must return ok=False — returning ok=True with an empty
    result let callers that check `.ok` believe a path was found (and masked the
    Ruby bare-call bug in the test suite)."""
    _mk(tmp_path, {"pkg/__init__.py": "",
                   "pkg/m.py": "def a():\n    return 1\ndef b():\n    return 2\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.trace_path(store, "a", "b")  # a and b are unconnected
        assert res.ok is False
        assert res.needs_review


# -- Panel F / sonnet (MEDIUM): TS `export { X }` re-export seeds exported ------
def test_ts_named_reexport_is_public_api(tmp_path):
    """`export { Widget }` (named re-export) marks Widget as public API just like
    `export class Widget` — without it the class and its methods were false-flagged
    dead (a symmetry gap with inline export and Python __all__)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"api.ts": "class Widget {\n  visible(){ return 1; }\n}\nexport { Widget };\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Widget" not in stale
        assert "Widget.visible" not in stale


# -- Panel F / sonnet (LOW): multi-statement SQL classifies each statement ------
def test_sql_multistatement_labels_each_statement(tmp_path):
    """`DELETE FROM old; SELECT FROM backup` parses as a Block via parse_one, so the
    DELETE target was mislabelled READS. parse() splits it; the DELETE target is a
    write, the SELECT source a read."""
    pytest.importorskip("sqlglot")
    from stitchgraph.core.model import Relation
    from stitchgraph.core.resolve.sql import _link
    nodes: dict = {}
    edges: list = []
    _link(nodes, edges, "f", "a.py", 1, "DELETE FROM old_users; SELECT id FROM backup")
    writes = {e.dst_id for e in edges if e.relation is Relation.WRITES}
    reads = {e.dst_id for e in edges if e.relation is Relation.READS}
    assert "db::old_users" in writes   # DELETE target
    assert "db::backup" in reads        # SELECT source


# -- Panel G / opus (MEDIUM): parallel edges are deduped (degree metrics) -------
def test_repeated_call_sites_are_one_edge(tmp_path):
    """Two call sites to the same target are one CALLS relationship, not two —
    parallel edges otherwise inflate fan_in/fan_out and the fan_in-fallback hubs
    (and `--precise` made it worse by re-confirming every AST edge via jedi)."""
    from stitchgraph.core.model import Relation
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {"pkg/__init__.py": "",
                   "pkg/m.py": "def caller():\n    helper()\n    helper()\n"
                               "def helper():\n    return 1\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        calls = [e for e in store.resolved_edges(Relation.CALLS)
                 if e.dst_id.endswith("::helper")]
        assert len(calls) == 1                       # collapsed, not two
        assert fan_in(store).get("pkg/m.py::helper") == 1


# -- Panel G / opus (LOW): get_matrix sparse cells are distinct -----------------
def test_get_matrix_cells_are_deduped(tmp_path):
    """`get_matrix` sparse cells (and density) must count distinct (src,dst) edges,
    not call sites — repeated calls produced duplicate cells and an inflated density
    (the dense grid was already correct)."""
    _mk(tmp_path, {"pkg/__init__.py": "",
                   "pkg/m.py": "def caller():\n    helper()\n    helper()\n"
                               "def helper():\n    return 1\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.get_matrix(store, "pkg/m.py", "CALLS")
        cells = res.result["cells"]
        keys = {(c["src"], c["dst"]) for c in cells}
        assert len(cells) == len(keys)   # no duplicate (src,dst)


# -- Panel G / sonnet (LOW): ORM relationship()/M2M are not columns ------------
def test_orm_relationship_is_not_a_phantom_column(tmp_path):
    """A SQLAlchemy `relationship()` (and a Django ManyToManyField) is not a table
    column — emitting it as a DBColumn made a phantom `db::<table>.<attr>` node that
    polluted the schema view and trace_path. Real `Column(...)` fields still map."""
    _mk(tmp_path, {
        "models.py": "from sqlalchemy.orm import DeclarativeBase, relationship\n"
                     "class Base(DeclarativeBase):\n    pass\n"
                     "class User(Base):\n"
                     '    __tablename__ = "users"\n'
                     "    id = Column(Integer, primary_key=True)\n"
                     "    email = Column(String)\n"
                     '    orders = relationship("Order")\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        from stitchgraph.core.model import NodeKind
        cols = {n.id for n in store.nodes_by_kind(NodeKind.DB_COLUMN)}
        assert {"db::users.id", "db::users.email"} <= cols  # real columns kept
        assert "db::users.orders" not in cols                # relationship is not a column


# -- Panel I / opus (HIGH): `new Foo()` constructor calls are edges ------------
def test_new_expression_constructor_is_a_call(tmp_path):
    """A class instantiated only via `new Foo()` inside a live function must not be
    flagged dead — JS/TS/C#/C++ `new`/object-creation expressions weren't in
    call_types (Java and Python already model constructor calls). Precision gap."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.model import Relation
    # JS/TS: exported entry instantiates an otherwise-unreferenced class.
    _mk(tmp_path, {
        "app.ts": "class Internal {\n  used(){ return 1; }\n}\n"
                  "export function api(){ const i = new Internal(); return i.used(); }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Internal" not in stale  # instantiated by a live exported function

    # C++: the constructor call must produce a CALLS edge (entry seeding aside).
    cpp = tmp_path / "cpp"
    cpp.mkdir()
    (cpp / "m.cpp").write_text(
        "class Widget { public: int g(){ return 1; } };\n"
        "int run(){ Widget* w = new Widget(); return w->g(); }\n")
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(cpp))
        calls = {(e.src.split("::")[-1], e.dst_id.split("::")[-1])
                 for e in store.resolved_edges(Relation.CALLS)}
        assert ("run", "Widget") in calls   # new Widget() -> CALLS edge


# -- Panel I / sonnet (LOW): read-only `global x` is not a write ---------------
def test_readonly_global_is_not_a_write(tmp_path):
    """`global x; return x` (declare-without-assign) must not emit a WRITES edge —
    that faked a read+write data feedback loop in scan(). Only an actual assignment
    to a declared global is a write."""
    from stitchgraph.core.model import Relation
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/m.py": "counter = 0\n"
                    "def reader():\n    global counter\n    return counter\n"
                    "def writer():\n    global counter\n    counter = counter + 1\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        writers = {e.src.split("::")[-1] for e in store.resolved_edges(Relation.WRITES)}
        assert "reader" not in writers   # read-only: no spurious WRITES
        assert "writer" in writers        # genuine assignment
        loops = [i for i in sg.scan(store).result if i["kind"] == "data_loop"]
        members = {m for i in loops for m in i["members"]}
        assert not any(m.endswith("::reader") for m in members)  # no false data loop


# -- Panel J / opus (HIGH): bare-name references keep a symbol live -------------
def test_python_bare_name_references_are_modeled(tmp_path):
    """A function/class used only by name from a live entry — passed as a callback,
    accessed as `Color.RED`, or a factory `Widget.create()` — must not be flagged
    dead. Genuinely-unreferenced code is still flagged (precision preserved)."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["run"]\nfrom .m import run\n',
        "pkg/m.py": (
            "import enum\n"
            "class Color(enum.Enum):\n    RED = 1\n"
            "class Widget:\n    @classmethod\n    def create(cls):\n        return cls()\n"
            "def handler(x):\n    return x\n"
            "def really_dead():\n    return 99\n"
            "def register(cb):\n    return cb(1)\n"
            "def run():\n    register(handler)\n    Color.RED\n    return Widget.create()\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert {"handler", "Color", "Widget"} & stale == set()  # all used by name
        assert "really_dead" in stale                            # truly unreferenced


# -- Panel J / opus + sonnet (HIGH): tree-sitter by-name refs + constructors ----
def test_tree_sitter_by_name_and_constructor_uses_keep_symbol_live(tmp_path):
    """The tree-sitter reference pass keeps a class/function used only by name live:
    a JS callback (`const cb = handler`), a PHP `new UserRepository()`, and a Ruby
    `Service.new` — the latter two are constructor idioms the Panel I `new` fix
    didn't reach. Genuinely-dead code stays flagged."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    cases = {
        "app.js": ("function handler(e){ return e; }\n"
                   "function jsDead(){ return 9; }\n"
                   "export function init(){ const cb = handler; return cb(1); }\n"),
        "app.php": ("<?php\nclass UserRepository { public function findById($id){ return $id; } }\n"
                    "function phpDead(){ return 9; }\n"
                    "function main(){ $r = new UserRepository(); return $r->findById(1); }\n"),
        "app.rb": ("class Service\n  def run; 1; end\nend\n"
                   "def rbDead; 9; end\n"
                   "def main\n  svc = Service.new\n  svc.run\nend\n"),
    }
    for fname, code in cases.items():
        d = tmp_path / fname.split(".")[-1]   # one dir per language
        d.mkdir()
        (d / fname).write_text(code)
        with sg.Store(":memory:") as store:
            sg.reindex(store, str(d))
            stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
            assert "handler" not in stale if fname.endswith(".js") else True
            assert "UserRepository" not in stale if fname.endswith(".php") else True
            assert "Service" not in stale if fname.endswith(".rb") else True
            dead = {"js": "jsDead", "php": "phpDead", "rb": "rbDead"}[fname.split(".")[-1]]
            assert dead in stale, f"{dead} should still be flagged in {fname}"


# -- Panel K / opus (HIGH): a class used only as a type annotation is live ------
def test_type_annotation_only_class_is_not_stale(tmp_path):
    """A class referenced only in a parameter/return annotation (`def f(x: Config)`,
    `-> Fwd`, `list[Gen]`) is a real use that lives in the signature, not the body —
    it must not be flagged dead."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["main"]\nfrom .m import main\n',
        "pkg/m.py": (
            "class Config: pass\n"
            "class Fwd: pass\n"
            "class Gen: pass\n"
            "class DeadCls: pass\n"
            "def annotated(cfg: Config) -> Fwd:\n    return cfg\n"
            "def generic(items: list[Gen]):\n    return items\n"
            "def main():\n    annotated(None)\n    return generic([])\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert {"Config", "Fwd", "Gen"} & stale == set()  # used as annotations
        assert "DeadCls" in stale                           # truly unreferenced


# -- Panel K / opus + sonnet (MEDIUM): no spurious REFERENCES self-loops -------
def test_tree_sitter_refs_no_self_loops_or_callee_double_edges(tmp_path):
    """`_direct_refs` must not emit a REFERENCES self-loop per def (the id()-skip was
    ineffective on tree-sitter's fresh wrappers) nor a REFERENCES duplicate of a CALLS
    edge — both inflated fan_in / get_matrix / god-object detection."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.model import Relation
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {"app.ts": "export function main(){ return helper(); }\n"
                             "function helper(){ return 1; }\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        refs = store.resolved_edges(Relation.REFERENCES)
        assert not any(e.src == e.dst_id for e in refs)       # no self-loops
        assert fan_in(store).get("app.ts::main") in (None, 0)  # main has no real callers
        assert fan_in(store).get("app.ts::helper") == 1        # one CALLS edge, not doubled


# -- Panel L / opus (MEDIUM): CALLS subsumes a redundant REFERENCES edge --------
def test_python_call_and_reference_to_same_target_not_doubled(tmp_path):
    """A function that both *calls* and *names/annotates* the same symbol
    (`def build() -> Node: return Node()`) must not get both a CALLS and a REFERENCES
    edge to it — that double-counted fan_in/pagerank (the Python twin of the
    tree-sitter callee-double-edge fixed in Panel K)."""
    from stitchgraph.core.model import Relation
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {"pkg/__init__.py": "",
                   "pkg/m.py": "class Node:\n    pass\n"
                               "def build() -> Node:\n    return Node()\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        rels = {e.relation for e in store.resolved_edges()
                if e.src.endswith("build") and e.dst_id and e.dst_id.endswith("::Node")}
        assert Relation.CALLS in rels and Relation.REFERENCES not in rels  # CALLS wins
        assert fan_in(store).get("pkg/m.py::Node") == 1                     # counted once


# -- Panel L / haiku (HIGH): constructing a class reaches its constructor ------
def test_constructor_body_is_reachable_python(tmp_path):
    """Constructing `Service()` implicitly runs `Service.__init__`, so a class built
    inside `__init__` (`self.r = Resource()`) is live — without a class->__init__ link
    the constructor body is unreachable and its constructions are flagged dead."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["main"]\nfrom .m import main\n',
        "pkg/m.py": (
            "class Resource:\n    def use(self):\n        return 1\n"
            "class Service:\n    def __init__(self):\n        self.r = Resource()\n"
            "    def run(self):\n        return self.r.use()\n"
            "class TrulyDead:\n    pass\n"
            "def main():\n    return Service().run()\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Resource" not in stale   # constructed in Service.__init__, live
        assert "TrulyDead" in stale       # genuinely unreferenced


def test_constructor_body_is_reachable_tree_sitter(tmp_path):
    """Same for tree-sitter: a JS `class Service { constructor(){ new Resource() } }`
    built via `new Service()` keeps Resource live (class -> constructor link)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.js": ("class Resource { use(){ return 1; } }\n"
                   "class Service { constructor(){ this.r = new Resource(); } "
                   "run(){ return this.r.use(); } }\n"
                   "export function main(){ return new Service().run(); }\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Resource" not in stale


# -- Panel L / sonnet (MEDIUM): Python REFERENCES self-loops are dropped --------
def test_python_reference_self_loop_is_dropped(tmp_path):
    """A function that names itself by value (`table = {"x": dispatcher}`) must not
    get a REFERENCES self-loop — it carries no liveness/impact meaning and inflated
    fan_in (the Python twin of the tree-sitter self-loop fixed in Panel K)."""
    from stitchgraph.core.model import Relation
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {"pkg/__init__.py": "",
                   "pkg/m.py": "def dispatcher():\n    table = {'x': dispatcher}\n    return table\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert not any(e.src == e.dst_id for e in store.resolved_edges(Relation.REFERENCES))
        assert fan_in(store).get("pkg/m.py::dispatcher") in (None, 0)


# -- Panel M / opus (HIGH): a PHP public class is public API (live) -------------
def test_php_public_class_is_not_stale(tmp_path):
    """PHP classes carry no `public` token (they're implicitly public), so the class
    node wasn't seeded `exported` though its `public function` methods were — flagging
    a library public class dead while its method is a live root. A class with an
    exported method is itself public API."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.php": ("<?php\n"
                    "class Helper { public function work(){ return 1; } }\n"
                    "class PublicApi { public function entry(){ $h = new Helper(); return $h->work(); } }\n"
                    "class TrulyDead { private function x(){ return 1; } }\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "PublicApi" not in stale   # public class = public API
        assert "TrulyDead" in stale        # only a private method -> not public API


# -- Panel M / sonnet (HIGH): ingest_trace that grounds nothing is not success --
def test_ingest_trace_grounding_nothing_is_refused(tmp_path):
    """Coverage that parses but maps to no indexed symbol must NOT report success
    or set has_runtime (which would wrongly raise find_stale confidence as if
    liveness were trace-grounded)."""
    import json as _json
    _mk(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": "def f():\n    return 1\n"})
    cov = tmp_path / "cov.json"
    cov.write_text(_json.dumps({"files": {"NOT_INDEXED.py": {"executed_lines": [1, 2]}}}))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.ingest_trace(store, str(cov))
        assert res.ok is False and res.needs_review     # grounded nothing -> refusal
        assert store.get_meta("has_runtime") is None      # not marked as runtime


# -- Panel M / sonnet (HIGH): config loads from the indexed root, not cwd -------
def test_config_loaded_from_indexed_root_not_cwd(tmp_path, monkeypatch):
    """A config-dependent operation (find_stale via the entry-point detector) must
    honour the indexed project's stitchgraph.toml even when run from another cwd."""
    proj = tmp_path / "proj"
    _mk(proj, {"pkg/__init__.py": "", "pkg/m.py": "def orphan():\n    return 1\n"})
    (proj / "stitchgraph.toml").write_text('[entry_points]\ninclude = ["pkg/m.py::orphan"]\n')
    other = tmp_path / "other"
    other.mkdir()
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(proj))
        monkeypatch.chdir(other)   # different cwd than the indexed root
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "orphan" not in stale   # the project's [entry_points] override is honoured


# -- Panel M / sonnet (MEDIUM): old index DBs migrate edge columns -------------
def test_old_index_db_migrates_edge_columns(tmp_path):
    """`_migrate` must add `edges.source`/`edges.file` to an index built before those
    columns existed — `_row_to_edge` reads them unconditionally, so reads would fail."""
    import sqlite3
    dbp = tmp_path / "old.db"
    con = sqlite3.connect(dbp)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,"
        "  location TEXT, file TEXT, is_stub INTEGER, arity INTEGER, summary TEXT);"
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL,"
        "  relation TEXT NOT NULL, dst_symbol TEXT NOT NULL, dst_id TEXT, weight REAL,"
        "  provenance TEXT, location TEXT);"
        "INSERT INTO nodes(id,kind,name,location,is_stub) VALUES ('a.py::f','Function','f','a.py:1:0',0);"
        "INSERT INTO edges(src,relation,dst_symbol,weight,provenance,location)"
        "  VALUES ('a.py::f','CALLS','g',0.6,'inferred','a.py:1:0');")
    con.commit()
    con.close()
    with sg.Store(str(dbp)) as store:      # opening triggers _migrate
        holes = store.unresolved_edges()    # reads edges.source/file -> must not raise
        assert any(e.src == "a.py::f" for e in holes)


# -- Panel N / opus (HIGH): a symbol used only as a default value is live ------
def test_default_value_references_are_modeled(tmp_path):
    """A class/function used only as a parameter *default value* (`def f(x=Strategy)`,
    `cb=handler`) executes at call time, so it must not be flagged dead — defaults
    live in `func.args`, which the body/annotation passes don't walk (the Python twin
    of the tree-sitter whole-def walk)."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["main"]\nfrom .m import main\n',
        "pkg/m.py": (
            "class Strategy:\n    def run(self):\n        return 1\n"
            "def handler():\n    return 2\n"
            "class TrulyDead:\n    pass\n"
            "def configure(strategy=Strategy, cb=handler):\n    return strategy(), cb\n"
            "def main():\n    return configure()\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert {"Strategy", "handler"} & stale == set()  # used as default values
        assert "TrulyDead" in stale                       # genuinely unreferenced


# -- Panel O / opus (MEDIUM): a metaclass used only via keyword is live --------
def test_metaclass_keyword_reference_is_modeled(tmp_path):
    """A class used only as `class X(metaclass=Meta)` governs X's creation and is
    live — class-definition keyword args sit at the same syntactic level as bases,
    which were already edged; `child.keywords` wasn't walked."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["Thing"]\nfrom .lib import Thing\n',
        "pkg/lib.py": (
            "class Meta(type):\n"
            "    def __call__(cls, *a, **k):\n        return super().__call__(*a, **k)\n"
            "class Thing(metaclass=Meta):\n    def run(self):\n        return 1\n"
            "class TrulyDead(type):\n    pass\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Meta" not in stale       # used as Thing's metaclass
        assert "TrulyDead" in stale       # genuinely unreferenced


# -- Panel P / opus (HIGH): references in the class *body* are live ------------
def test_class_body_references_are_modeled(tmp_path):
    """Symbols referenced directly in a class body — attribute assignments
    (`h = Helper`), dispatch tables (`TABLE = {"a": handle_a}`), class-level
    annotations — are live iff the class is reachable. The Python ast walked only
    method (FunctionDef) bodies, never the class body itself, so such symbols were
    flagged dead (the tree-sitter extractor walks the whole class node)."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["main"]\nfrom .m import main\n',
        "pkg/m.py": (
            "class Helper:\n    def assist(self):\n        return 1\n"
            "def handle_a():\n    return 'a'\n"
            "def handle_b():\n    return 'b'\n"
            "class TrulyDead:\n    pass\n"
            "class Container:\n    h = Helper\n"
            "class Router:\n    TABLE = {'a': handle_a, 'b': handle_b}\n"
            "def main():\n    return Container(), Router()\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Helper" not in stale                       # class-body attribute ref
        assert {"handle_a", "handle_b"} & stale == set()   # class-body dispatch table
        assert "TrulyDead" in stale                        # genuinely unreferenced


# -- Panel Q / sonnet (HIGH): public re-exports from __init__ are export roots -
def test_package_reexport_is_an_export_root(tmp_path):
    """`from .api import Public` in a package __init__ makes `pkg.Public` importable
    public API, so it's a live root even with no internal caller. ast.ImportFrom
    carries `.names` aliases (not a `.name`), so the __init__ export-surface scan
    missed re-exports and flagged live public API dead. Underscore-prefixed and
    non-re-exported symbols stay private."""
    _mk(tmp_path, {
        "pkg/__init__.py": "from .api import Public\nfrom .api import _hidden\n",
        "pkg/api.py": (
            "class Public:\n    def live_method(self):\n        return 1\n"
            "class _hidden:\n    pass\n"
            "class NotExported:\n    pass\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Public" not in stale          # re-exported public API
        assert "live_method" not in stale      # public method of an exported class
        assert "NotExported" in stale          # not re-exported -> genuinely dead
        assert "_hidden" in stale              # underscore re-export stays private


def test_reexport_root_survives_when_all_is_declared(tmp_path):
    """A re-export not listed in __all__ is still importable as `pkg.Public`, so it
    must remain a root (additive with __all__, never flagged dead)."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["other"]\nfrom .api import Public\ndef other():\n    return 0\n',
        "pkg/api.py": "class Public:\n    def m(self):\n        return 1\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Public" not in stale


# -- Panel Q / opus (CRITICAL): function-local defs and their uses are live ----
def test_function_local_class_use_is_live(tmp_path):
    """A symbol used only inside a function-local class/closure is live when the
    enclosing function is reachable. _def_node never descended into function bodies,
    so function-local classes/functions weren't nodes; _walk_scope emitted edges from
    their (phantom) qualnames that couldn't participate in reachability -> the used
    symbol was flagged dead. _def_node now models nested defs as real nodes."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["run"]\nfrom .m import run\n',
        "pkg/m.py": (
            "def run():\n"
            "    class Local:\n        def helper(self):\n            return Tool()\n"
            "    return Local().helper()\n"
            "class Tool:\n    pass\n"
            "class TrulyDead:\n    pass\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Tool" not in stale         # constructed inside a function-local class
        assert "TrulyDead" in stale         # genuinely unreferenced


def test_doubly_nested_closure_use_is_live(tmp_path):
    """The leak that saved single-level nested functions didn't reach two levels
    deep; a symbol used in a doubly-nested closure was still flagged dead."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["run"]\nfrom .m import run\n',
        "pkg/m.py": (
            "def run():\n"
            "    def mid():\n        def deep():\n            return Tool()\n"
            "        return deep()\n"
            "    return mid()\n"
            "class Tool:\n    pass\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Tool" not in stale


def test_decorator_registered_local_handler_is_live(tmp_path):
    """A function-local handler whose liveness comes from decorator registration
    (`@app.command()`), not a direct call, must not be flagged dead once it is a
    node: a function-local def is live iff its enclosing function is reachable
    (enclosing -> nested containment edge)."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["build_app"]\nfrom .m import build_app\n',
        "pkg/m.py": (
            "class App:\n    def command(self, fn):\n        return fn\n"
            "def build_app():\n"
            "    app = App()\n"
            "    @app.command\n    def handler():\n        return 1\n"
            "    return app\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "handler" not in stale      # live via decorator registration


# -- Panel R / opus + sonnet (MEDIUM): own-scope walk helpers must not descend
#    into nested defs -- their calls/refs/globals leaked up to the enclosing
#    scope, double-counting fan_in/pagerank and surfacing false god-objects ------
def test_nested_def_calls_not_attributed_to_enclosing_scope(tmp_path):
    """The five `_direct_*` helpers promise "not crossing nested defs", but their
    driver loop ran rec() on a top-level body statement that was *itself* a nested
    def, leaking that def's calls/refs up into the enclosing function. A symbol
    used only inside `nested` must be edged from `outer.nested`, never `outer`, and
    counted once (Panel Q made the leak observable by giving the nested def a node)."""
    from stitchgraph.core.model import Relation
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/m.py": (
            "def callee():\n    return 1\n"
            "class Strategy:\n    pass\n"
            "def outer():\n"
            "    def nested():\n        s = Strategy\n        return callee()\n"
            "    return nested\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        calls = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.CALLS)}
        refs = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.REFERENCES)}
        assert ("pkg/m.py::outer.nested", "pkg/m.py::callee") in calls    # correct scope
        assert ("pkg/m.py::outer", "pkg/m.py::callee") not in calls       # was leaked up
        assert ("pkg/m.py::outer.nested", "pkg/m.py::Strategy") in refs   # correct scope
        assert ("pkg/m.py::outer", "pkg/m.py::Strategy") not in refs      # was leaked up
        assert fan_in(store).get("pkg/m.py::callee") == 1                 # counted once


def test_class_body_does_not_absorb_method_body_references(tmp_path):
    """The same driver-loop leak hit Panel P's class-body walk (`_direct_names` on
    the ClassDef descended into method bodies), so a symbol used only inside a
    method was wrongly attributed to the class node. The class body's own refs are
    kept; method-body refs belong to the method, not the class."""
    from stitchgraph.core.model import Relation
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/m.py": (
            "def helper_a():\n    return 1\n"
            "def helper_b():\n    return 2\n"
            "class C:\n"
            "    table = helper_a\n"                                # class-body ref
            "    def meth(self):\n        return helper_b()\n"      # method-body call
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        refs = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.REFERENCES)}
        calls = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.CALLS)}
        assert ("pkg/m.py::C", "pkg/m.py::helper_a") in refs        # class body's own ref
        assert ("pkg/m.py::C", "pkg/m.py::helper_b") not in refs    # method body, not class
        assert ("pkg/m.py::C.meth", "pkg/m.py::helper_b") in calls  # correct scope


# -- Panel R / haiku: tree-sitter must nest function-local defs under their
#    enclosing function (not module scope, where same-named siblings collide into
#    one node) and keep them live via a containment edge -- Python (Panel Q) parity
def test_tree_sitter_function_local_def_is_nested_and_live(tmp_path):
    """A function-local def was created at module scope (`app.ts::handler`), so two
    same-named defs merged into one node and the qual lost its scope. Now nested as
    `app.ts::setup.handler`, kept live by an enclosing->nested containment edge so a
    nested def whose liveness comes from execution (not a by-name call) isn't flagged
    dead, while a genuinely-unreferenced top-level def still is."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.ts": (
            "export function setup(){\n"
            "    function handler(){ return doWork(); }\n"
            "    return handler;\n"
            "}\n"
            "function doWork(){ return 1; }\n"
            "function neverReached(){ return 2; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ids = set(store.all_node_ids())
        assert "app.ts::setup.handler" in ids        # nested under its enclosing fn
        assert "app.ts::handler" not in ids          # not at module scope
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "handler" not in stale                # live via containment edge
        assert "doWork" not in stale                 # called by the nested handler
        assert "neverReached" in stale               # genuinely unreferenced


def test_tree_sitter_same_name_nested_and_module_def_do_not_merge(tmp_path):
    """A function-local def sharing a name with a module-level def must not collapse
    into one node (the module-scope-id collision merged their callers/callees,
    corrupting get_callers/get_callees/impact_of/get_matrix for that name)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.ts": (
            "export function main(){\n"
            "    function helper(){ return 1; }\n"
            "    return helper();\n"
            "}\n"
            "function helper(){ return 2; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ids = set(store.all_node_ids())
        assert "app.ts::main.helper" in ids          # the nested one
        assert "app.ts::helper" in ids               # the distinct module-level one


# -- Panel S / opus (CRITICAL): Panel R's body-skip dropped a nested def's *header*
#    expressions (decorator args, class bases), which run in the ENCLOSING scope ---
def test_nested_def_decorator_argument_call_is_attributed_to_enclosing(tmp_path):
    """`@registry(make_validator())` on a function-local `handler` calls
    `make_validator` in the *enclosing* function's scope at definition time. Panel R
    skipped the whole nested-def statement, dropping that call (it isn't recovered:
    `_decorator_edges` edges only the decorator's name) -> `make_validator` had zero
    inbound edges and was flagged dead (a cardinal-invariant regression). The body
    leak must still NOT return: a call inside the nested def's *body* stays on the
    nested def, not the enclosing function."""
    from stitchgraph.core.model import Relation
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["main"]\nfrom .m import main\n',
        "pkg/m.py": (
            "def make_validator():\n    return 1\n"
            "def body_only():\n    return 2\n"
            "def registry(v):\n    def deco(fn):\n        return fn\n    return deco\n"
            "def build():\n"
            "    @registry(make_validator())\n"
            "    def handler(req):\n        return body_only()\n"
            "    return handler\n"
            "def main():\n    return build()\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "make_validator" not in stale          # decorator arg runs in build's scope
        calls = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.CALLS)}
        assert ("pkg/m.py::build", "pkg/m.py::make_validator") in calls   # header -> enclosing
        assert ("pkg/m.py::build", "pkg/m.py::body_only") not in calls    # body stays nested
        assert ("pkg/m.py::build.handler", "pkg/m.py::body_only") in calls


def test_nested_class_base_expression_is_attributed_to_enclosing(tmp_path):
    """A function-local class whose base is a *call* (`class L(make_base())`) invokes
    `make_base` in the enclosing scope at definition time; Panel R's statement-skip
    dropped it. `_walk_scope` only edges a base via `_name_of`, which is None for a
    Call base, so it isn't recovered there either."""
    from stitchgraph.core.model import Relation
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["main"]\nfrom .m import main\n',
        "pkg/m.py": (
            "def make_base():\n    return object\n"
            "def build():\n"
            "    class Local(make_base()):\n        pass\n"
            "    return Local\n"
            "def main():\n    return build()\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "make_base" not in stale               # base expr runs in build's scope
        # reached from the enclosing fn via a liveness edge (CALLS or REFERENCES — the
        # outermost call's func name resolves as a REFERENCES, which still keeps it live)
        reached = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.CALLS)} \
            | {(e.src, e.dst_id) for e in store.resolved_edges(Relation.REFERENCES)}
        assert ("pkg/m.py::build", "pkg/m.py::make_base") in reached


# -- Panel T: the nested-scope class, remaining hosts (systematic audit) --------
# A def can be nested in: a function body (Panel Q), a class body (Panel P), a
# control-flow block, or a function-expression/arrow. The last two were unmodeled
# -> live code flagged dead. These pin every host in both extractors.

# Panel T / haiku (CRITICAL): Python def inside a control-flow block
@pytest.mark.parametrize("block", [
    "if True:\n        {d}",
    "for _ in range(1):\n        {d}",
    "while True:\n        {d}\n        break",
    "try:\n        {d}\n    except Exception:\n        pass",
    "with open('x') as _f:\n        {d}",
])
def test_python_def_inside_control_flow_block_is_live(tmp_path, block):
    """`_def_node`/`_walk_scope` only walked a function's *direct* body, so a def
    nested in an `if`/`for`/`while`/`try`/`with` was never modeled as a node, yet
    `_walk_scope` emitted edges from its qualname -> phantom source -> a symbol used
    only there was flagged dead. Now both look *through* control flow via
    `_scope_defs` (the block adds no qual level)."""
    inner = "def inner():\n            return helper()"
    body = block.format(d=inner)
    src = (
        "def helper():\n    return 1\n"
        f"def process():\n    {body}\n    return inner()\n"
    )
    _mk(tmp_path, {"pkg/__init__.py": '__all__ = ["process"]\nfrom .m import process\n',
                   "pkg/m.py": src})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "helper" not in stale                       # reached via process->inner->helper
        assert "pkg/m.py::process.inner" in set(store.all_node_ids())  # modeled as a node


def test_python_class_inside_control_flow_block_is_live(tmp_path):
    """The control-flow gap applied to a nested *class* too. A class defined in an
    `if` inside a live function, referencing `Tool` in its class body, is live iff the
    function is reachable -> `Tool` must stay live (containment edge + Panel P
    class-body walk, both now reaching through control flow)."""
    _mk(tmp_path, {
        "pkg/__init__.py": '__all__ = ["build"]\nfrom .m import build\n',
        "pkg/m.py": (
            "class Tool:\n    pass\n"
            "def build():\n"
            "    if True:\n        class Local:\n            x = Tool\n"
            "    return Local\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Tool" not in stale                          # referenced in the nested class body
        assert "pkg/m.py::build.Local" in set(store.all_node_ids())  # nested class modeled


def test_python_control_flow_nested_def_body_does_not_leak_to_enclosing(tmp_path):
    """The body-leak fix (Panel R) must still hold for control-flow-nested defs: a
    call inside the nested def's body stays on the nested def, not the enclosing fn."""
    from stitchgraph.core.model import Relation
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/m.py": (
            "def callee():\n    return 1\n"
            "def process():\n    if True:\n        def inner():\n            return callee()\n"
            "    return inner()\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        calls = {(e.src, e.dst_id) for e in store.resolved_edges(Relation.CALLS)}
        assert ("pkg/m.py::process.inner", "pkg/m.py::callee") in calls   # correct scope
        assert ("pkg/m.py::process", "pkg/m.py::callee") not in calls     # not leaked up


# Panel T / opus (CRITICAL): tree-sitter def inside a JS/TS arrow function
def test_tree_sitter_def_inside_arrow_function_is_live(tmp_path):
    """The arrow-function declarator branch created the node but never recursed into
    the arrow body, so a def nested in an arrow (pervasive in JS/TS) was never
    modeled -> a symbol used only there was flagged dead. Now it recurses + threads
    the containment edge, mirroring the regular-def branch."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.ts": (
            "function privHelper(){ return 1; }\n"
            "export const handler = () => {\n"
            "  function worker(){ return privHelper(); }\n"
            "  return worker();\n"
            "};\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "privHelper" not in stale                    # handler->worker->privHelper
        assert "app.ts::handler.worker" in set(store.all_node_ids())  # nested def modeled


# -- Issue #8 (field, v1.0.0): Rust #[test] / #[cfg(test)] items are test roots ----
def test_rust_cfg_test_inline_tests_are_not_flagged_dead(tmp_path):
    """Idiomatic Rust inline unit tests live in `#[cfg(test)] mod tests` with
    free-form names, so the test*/Benchmark*/Example* name convention never fires.
    The `#[test]`/`#[tokio::test]` attribute must seed the `test` role (root) so the
    tests — and the helpers they reach — aren't flagged dead (which flooded find_stale
    on a real Rust crate). A test helper reached by no test, and unused production
    code, must still be flagged (consistent with a dead helper in any test file)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"src/lib.rs": (
        "pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
        "fn truly_unused() -> i32 { 0 }\n"
        "#[cfg(test)]\nmod tests {\n    use super::*;\n"
        "    fn fixture() -> i32 { 7 }\n"
        "    fn unreached() -> i32 { 9 }\n"
        "    #[test]\n    fn add_works() { assert_eq!(add(2, 3), 5); let _ = fixture(); }\n"
        "    #[tokio::test]\n    async fn async_case() { assert_eq!(add(1, 1), 2); }\n}\n"
    )})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "add_works" not in stale and "async_case" not in stale  # #[test] roots
        assert "fixture" not in stale                                  # reached by a test
        assert "truly_unused" in stale and "unreached" in stale        # genuinely dead


# -- Issue #7 (field, v1.0.0): grammar-load failure must not be a silent empty graph -
def test_tree_sitter_grammar_load_failure_warns_not_silent(tmp_path, monkeypatch):
    """A tree-sitter grammar that can't load (offline/proxied/version drift) must NOT
    collapse into a silent empty graph with exit ok — it warns and names the skipped
    language, while Python extraction is unaffected (issue #7)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.extract import extract_project, treesitter
    _mk(tmp_path, {"app.js": "export function jsFn(){ return 1; }\n",
                   "m.py": "def py_fn():\n    return 1\n"})

    def _boom(_lang):  # simulate grammar unavailable
        raise RuntimeError("grammar download failed")
    monkeypatch.setattr(treesitter, "get_language", _boom)

    with pytest.warns(RuntimeWarning, match="grammar"):
        nodes, _edges = extract_project(str(tmp_path))
    names = {n.name for n in nodes}
    assert "py_fn" in names      # Python results unaffected by the tree-sitter failure
    assert "jsFn" not in names   # JS skipped — but loudly, not silently


# -- Issue #9 (field, v1.0.0): impact_of homonyms — list candidates + allow scoping --
def test_impact_of_ambiguous_name_lists_candidates_and_scopes(tmp_path):
    """`impact_of` on a bare common name must not blank-refuse (nor silently union):
    it surfaces the candidates (alternatives) and accepts a qualified `Type.method`
    or a full `path::qual` id to scope to exactly one (issue #9)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"src/a.rs": (
        "pub struct LmdbStorage;\nimpl LmdbStorage { pub fn get(&self) -> i32 { 1 } }\n"
        "pub struct Cache;\nimpl Cache { pub fn get(&self) -> i32 { 2 } }\n"
        "pub fn use_lmdb(s: &LmdbStorage) -> i32 { s.get() }\n"
    )})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        amb = sg.impact_of(store, "get")
        assert amb.ok is False                       # not unioned, not blank-refused
        assert len(amb.alternatives) == 2            # both homonyms surfaced
        assert all("::" in a["id"] for a in amb.alternatives)  # scopable ids shown
        scoped = sg.impact_of(store, "LmdbStorage.get")        # qualified scopes
        assert scoped.ok and scoped.result["symbol"].endswith("::LmdbStorage.get")
        full = sg.impact_of(store, "src/a.rs::Cache.get")      # full id scopes
        assert full.ok and full.result["symbol"] == "src/a.rs::Cache.get"


# -- Panel W (1.0.1 confirmation): #8 attr match must be path-based, not substring ----
def test_rust_non_test_attribute_is_not_a_test_root(tmp_path):
    """The #8 fix must match the attribute PATH, not a raw "test" substring: a
    production fn carrying `#[cfg(feature="testing")]` or `#[doc="...test..."]` must
    NOT be seeded a test root (which would hide it from dead-code detection). Genuine
    `#[test]`/`#[cfg(test)]` still count (opus+haiku converged, Panel W)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"src/lib.rs": (
        '#[cfg(feature = "testing")]\n'
        "fn feature_gated_unused() -> i32 { 1 }\n"        # private + not a test → flag dead
        '#[doc = "a test of the docs"]\n'
        "fn documented_unused() -> i32 { 2 }\n"           # private + not a test → flag dead
        "#[cfg(test)]\nmod tests {\n"
        "    #[test]\n    fn real_test() { assert_eq!(1, 1); }\n}\n"  # genuine test → root
    )})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "feature_gated_unused" in stale    # #[cfg(feature="testing")] is NOT a test
        assert "documented_unused" in stale        # #[doc="...test..."] is NOT a test
        assert "real_test" not in stale             # genuine #[test] is a root


# -- Polyglot test detection (1.0.1, generalising issue #8 across languages) ---------
# The Rust #[test] fix exposed the same class everywhere: file-level test context never
# seeds the `test` role, so only the test*/Test* NAME convention did — flagging idiomatic
# tests (and their helpers) dead in every language whose tests aren't name-convention.

def test_java_junit5_package_private_tests_not_flagged_dead(tmp_path):
    """JUnit5's idiomatic test is a *package-private* `@Test void` (no `public`, so no
    `exported` mask) with a free-form name — the whole test class, its `@Test`/
    `@BeforeEach` methods, and private helpers were all flagged dead. The `@Test`/
    `@BeforeEach` annotations (Rust `#[test]` analog) must seed the `test` role; a
    genuinely-uncalled method still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"CalcTest.java": (
        "import org.junit.jupiter.api.Test;\nimport org.junit.jupiter.api.BeforeEach;\n"
        "class CalcTest {\n  private int helper() { return 7; }\n"
        "  @BeforeEach void setUp() {}\n"
        "  @Test void addWorks() { int h = helper(); }\n"
        "  void deadHelper() {}\n}\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        def hit(s):
            return any(x == s or x.endswith("." + s) for x in stale)
        assert not hit("addWorks") and not hit("setUp") and not hit("helper")  # all live
        assert not any(x == "CalcTest" for x in stale)                          # class live
        assert hit("deadHelper")                                                # still dead


def test_csharp_xunit_internal_class_tests_not_flagged_dead(tmp_path):
    """C# `[Fact]`/`[Theory]`/NUnit `[Test]` attributes on an internal class seed the
    `test` role (the public-method `exported` mask doesn't cover internal classes)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"CalcTests.cs": (
        "using Xunit;\nclass CalcTests {\n  int Helper() { return 7; }\n"
        "  [Fact] public void AddWorks() { int h = Helper(); }\n}\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert not any(s.endswith("AddWorks") or s.endswith("Helper") or s == "CalcTests"
                       for s in stale)


def test_js_call_based_test_helpers_not_flagged_dead(tmp_path):
    """JS/TS suites (Jest/Mocha/Vitest) define no named test function — `test()`/`it()`
    take anonymous callbacks at module scope. A helper called only inside such a
    callback was flagged dead. Module-level calls of a test file are now rooted from
    the module node; a helper called by nothing still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"calc.test.js": (
        "function makeFixture() { return 7; }\nfunction deadHelper() { return 0; }\n"
        "describe('calc', () => { it('adds', () => { expect(makeFixture()).toBe(7); }); });\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "makeFixture" not in stale   # reached from a test() callback
        assert "deadHelper" in stale         # called by nothing — still dead


def test_ruby_rspec_spec_file_helpers_not_flagged_dead(tmp_path):
    """RSpec `*_spec.rb` with `describe/it` blocks: `_is_test_file` now recognises the
    `_spec.` convention, and helpers called inside `it` blocks are rooted (Bug B)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"calc_spec.rb": (
        'RSpec.describe "Calc" do\n  def make_fixture; 7; end\n  def dead_helper; 1; end\n'
        '  it("adds") { expect(make_fixture).to eq(7) }\nend\n')})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert not any(s.endswith("make_fixture") for s in stale)   # reached from it block
        assert any(s.endswith("dead_helper") for s in stale)         # still dead


def test_python_unittest_testcase_subclass_not_flagged_dead(tmp_path):
    """A `unittest.TestCase` subclass is framework-instantiated; its methods were kept
    live (callback role) but the class itself was flagged. A framework subclass that
    overrides hook methods is now rooted too — but a bare `class Meta(type): pass`
    metaclass (no hooks) must still flag."""
    _mk(tmp_path, {"test_u.py": (
        "import unittest\nclass T(unittest.TestCase):\n    def setUp(self):\n        self.x = 1\n"
        "    def test_a(self):\n        assert self.x == 1\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "T" not in stale


def test_production_testing_dir_not_misclassified_as_tests(tmp_path):
    """Panel Y: `_is_test_file` must NOT treat `testing/` or `specs/` directories as
    tests — they are plausible *production* dirs (Go `testing` helpers, shipped test
    utilities, OpenAPI/webpack `specs`). Misclassifying one would root its module-level
    calls and hide genuinely-dead code there (the Panel W over-marking class)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"src/testing/fixtures.js": (
        "function registerFixture() { return 1; }\nregisterFixture();\n"
        "function neverUsed() { return 2; }\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        # module-referenced-but-otherwise-dead code in a production testing/ dir must
        # still surface — it is NOT a test file, so module calls are not rooted.
        assert "registerFixture" in stale and "neverUsed" in stale


def test_python_test_class_with_only_test_methods_not_flagged_dead(tmp_path):
    """Panel Z (CARDINAL): pytest's dominant idiom groups tests in `class TestWidget:`
    whose methods are all `test_*` (recorded as NodeKind.TEST, not METHOD). The class
    itself must be seeded `test` so it isn't flagged dead while its methods are live —
    the 'method live, class dead' shape. The earlier unittest fix (callback path) only
    rescued classes with a non-test override like `setUp`; this covers the all-tests
    case. A genuinely-unused non-test class is still flagged."""
    _mk(tmp_path, {
        "test_widget.py": ("class TestWidget:\n    def test_create(self):\n        assert True\n"
                           "    def test_destroy(self):\n        assert True\n"),
        "test_case.py": ("import unittest\nclass OnlyTests(unittest.TestCase):\n"
                         "    def test_b(self):\n        assert True\n"),
        "app.py": "class Unused:\n    def m(self):\n        return 1\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "TestWidget" not in stale   # pytest test class — live
        assert "OnlyTests" not in stale     # unittest all-test-methods class — live
        assert "Unused" in stale            # non-test class — still flagged


def test_test_class_inherited_and_nested_not_flagged_dead(tmp_path):
    """Panel AA (CARDINAL siblings of Panel Z): a test class that inherits all its
    tests from a custom base (idiomatic JUnit abstract-base + thin-subclass; pytest
    inherited tests), or is the *outer* of a nested test class, was flagged dead — the
    same 'container live, but flagged' shape, one level removed. `_seed_test_classes`
    now propagates the `test` role transitively across inheritance and up the enclosing
    chain. A non-test subclass is still flagged."""
    _mk(tmp_path, {
        "test_inh.py": ("class BaseTest:\n    def test_shared(self):\n        assert True\n"
                        "class TestB(BaseTest):\n    pass\n"),          # inherits all tests
        "test_nested.py": ("class TestOuter:\n    class TestInner:\n"
                           "        def test_a(self):\n            assert True\n"),
        "app.py": "class Base:\n    def run(self):\n        return 1\nclass Child(Base):\n    pass\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "TestB" not in stale and "BaseTest" not in stale   # inherited-tests class live
        assert not any(s == "TestOuter" or s.endswith(".TestOuter") for s in stale)  # nested outer live
        assert "Child" in stale                                    # non-test subclass still flagged


def test_tree_sitter_inherited_test_class_not_flagged_dead(tmp_path):
    """Tree-sitter twin of the inheritance sibling: a Java test class that inherits its
    `@Test` methods from an abstract base (standard JUnit) must not be flagged dead."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "AbstractFooTest.java": ("import org.junit.jupiter.api.Test;\n"
                                 "abstract class AbstractFooTest { @Test void shared() {} }\n"),
        "FooTest.java": "class FooTest extends AbstractFooTest {}\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "FooTest" not in stale and "AbstractFooTest" not in stale


def test_test_class_combined_nested_and_inherited_fixed_point(tmp_path):
    """Panel BB (CARDINAL): the two seed axes (enclosing-chain + inheritance) must
    iterate to a *combined* fixed point. A class discovered as a test class via
    inheritance can itself be nested, so its enclosing container needs re-walking —
    running the axes once each (in order) left the outer flagged. Idiomatic pytest:
    a grouping class whose inner classes inherit shared cases."""
    _mk(tmp_path, {"test_api.py": (
        "class _SharedCases:\n    def test_get(self):\n        assert True\n"
        "    def test_post(self):\n        assert True\n"
        "class TestApi:\n    class TestV1(_SharedCases):\n        pass\n"
        "    class TestV2(_SharedCases):\n        pass\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert not stale   # TestApi (outer), TestV1/V2 (inherited), _SharedCases all live


def test_tree_sitter_test_class_seeding_is_language_scoped(tmp_path):
    """Panel BB (over-marking): tree-sitter base resolution must be per-language — a
    same-named test class in another language must NOT seed a production class as a
    test (which would hide it from dead-code detection). Here a dead JS `Prod extends
    Base` must still flag even though an unrelated Java test class is also named `Base`."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Base.java": "import org.junit.jupiter.api.Test;\nclass Base { @Test void t() {} }\n",
        "prod.js": "class Base { real() { return 1; } }\nclass Prod extends Base { go() { return 2; } }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert any(s == "Prod" for s in stale)   # dead JS production class still flagged


def test_python_inherited_test_base_in_conftest_not_flagged_dead(tmp_path):
    """Panel CC (CARDINAL): Python recognised test files by FILENAME only, while
    tree-sitter also checked directories. A shared abstract test-case base in
    `tests/conftest.py` (canonical pytest location) thus got no `test` role, so a thin
    subclass inheriting its tests was flagged dead. The `is_test_file` heuristic is now
    shared + directory-aware across both extractors."""
    _mk(tmp_path, {
        "tests/conftest.py": ("class SharedAPICases:\n    def test_get(self):\n        assert True\n"
                              "    def test_post(self):\n        assert True\n"),
        "tests/test_v1.py": "from conftest import SharedAPICases\nclass TestV1(SharedAPICases):\n    pass\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "SharedAPICases" not in stale and "TestV1" not in stale


def test_rust_cfg_not_test_is_production_not_a_test_root(tmp_path):
    """Panel CC: `#[cfg(not(test))]` gates *production*-only code — it must NOT be marked
    a test root (which would hide it from dead-code detection). `_is_rust_test_attr`
    now drops `not(...)` predicates before scanning the cfg for a bare `test` token."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"src/lib.rs": (
        "#[cfg(not(test))]\nfn production_only() -> i32 { 1 }\n"
        "#[cfg(test)]\nmod t { #[test] fn real() { assert!(true); } }\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "production_only" in stale   # production-only, unused -> flagged (not a test)


# -- Panel FF (post-1.0.1, sonnet): two cardinal false-deads sonnet's fresh eyes caught -
def test_js_export_default_predefined_identifier_is_exported(tmp_path):
    """Panel FF (CARDINAL, pre-existing): `export default Foo;` where `Foo` is defined
    earlier in the file (the canonical React/Angular/Vue/Node idiom) never set the
    `exported` role, so the default-exported class/fn was flagged dead. `_reexport_names`
    now feeds the default-exported identifier into the reexport->exported path."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "utils.ts": "export function seedMe() { return 1; }\n",  # an exported root
        "service.ts": "class UserService { getUser() { return 1; } }\nexport default UserService;\n",
        "handler.ts": "function handler() { return 1; }\nexport default handler;\n",
        "dead.ts": "function deadOne() { return 1; }\nexport default () => {};\n",  # anon default
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "UserService" not in stale and "handler" not in stale  # default-exported -> live
        assert "deadOne" in stale  # genuinely dead (anon default doesn't export it)


def test_ruby_constant_receiver_in_rspec_block_keeps_class_live(tmp_path):
    """Panel FF (CARDINAL, introduced by the 1.0.1 Bug-B call-based rooting): a class used
    as a call receiver inside an RSpec `it` block (`Service.run`) got a CALLS edge to the
    method but no REFERENCES edge to the class, so the live class was flagged dead.
    `_module_uses` now collects name-references (incl. `constant` receivers) like
    `_direct_refs`."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "src/service.rb": "class Service\n  def self.run; 1; end\nend\n",
        "src/spec/service_spec.rb": 'describe "Service" do\n  it "runs" do\n    Service.run\n  end\nend\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "Service" not in stale  # live via RSpec; class receiver now referenced


def test_js_commonjs_and_ts_export_assignment_are_exported(tmp_path):
    """Panel GG (CARDINAL sibling of F1, pre-existing): the whole-module export forms
    `module.exports = Foo` (CommonJS), `export = Foo` (TS interop), `module.exports =
    {A, B}`, and `exports.x = Foo` never set the `exported` role, so the live public
    export was flagged dead. `_reexport_names` now handles them. A symbol not exported
    this way still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "seed.ts": "export function seed() { return 1; }\n",  # an exported root
        "cjs.js": "class CjsCls { m() { return 1; } }\nmodule.exports = CjsCls;\n",
        "eq.ts": "class EqExp { m() { return 1; } }\nexport = EqExp;\n",
        "obj.js": ("function A() { return 1; }\nfunction B() { return 2; }\n"
                   "function deadC() { return 3; }\nmodule.exports = { A, B };\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "CjsCls" not in stale and "EqExp" not in stale  # CJS / export= -> live
        assert "A" not in stale and "B" not in stale            # object export -> live
        assert "deadC" in stale                                  # not exported -> dead


def test_module_uses_does_not_descend_into_uncalled_function_expression(tmp_path):
    """Panel GG (precision): `_module_uses` must skip a `const helper = function(){…}`
    in a test file (it is itself a def, scanned per-def). Otherwise its body's refs are
    over-rooted from the module, hiding a dead class referenced only inside an *uncalled*
    helper. The dead class must still flag."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {"x.test.js": (
        "class DeadCls { m() { return 1; } }\n"
        "const helper = function() { DeadCls.m(); };\n"  # helper never called by a test
        "test('a', () => { expect(1).toBe(1); });\n")})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::", 1)[-1] for c in sg.find_stale(store).result}
        assert "DeadCls" in stale   # referenced only inside an uncalled helper -> still dead


# -- Issue #12 (1.0.3): offline-by-default grammars + adaptive load + doctor self-check -
def test_grammar_status_and_backend_probe(tmp_path):
    """`grammar_status`/`grammar_backend` power the `doctor` self-check: every supported
    language's grammar is probed, and the backend reports the install model (bundled vs
    download). In the test env all supported grammars load."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.extract import treesitter as ts
    ok, rows = ts.grammar_status()
    langs = {lang for lang, _, _ in rows}
    assert {"rust", "javascript", "typescript", "go", "java", "ruby"} <= langs
    assert ok  # all supported grammars load in the test environment
    b = ts.grammar_backend()
    assert b["installed"] and b["version"]
    assert b["model"] in ("bundled (offline)", "download-on-demand")


def test_load_grammar_retries_via_download_when_unbundled(monkeypatch):
    """`_load_grammar` gets a grammar "the easiest way available": if `get_language`
    fails and the installed pack exposes a `download()` API (the 1.x model), it fetches
    once and retries — so a runtime download is used when possible (issue #12, Option 1).
    On the bundled line (no `download`) a real failure propagates (→ issue-#7 warning)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    import tree_sitter_language_pack as pack

    from stitchgraph.core.extract import treesitter as ts
    calls = {"get": 0, "download": 0}
    real_get = ts.get_language
    def fake_get(lang):
        calls["get"] += 1
        if calls["get"] == 1:
            raise RuntimeError("not present yet")
        return real_get(lang)            # succeeds after the download
    def fake_download(names):
        calls["download"] += 1
        return len(names)
    monkeypatch.setattr(ts, "get_language", fake_get)
    monkeypatch.setattr(pack, "download", fake_download, raising=False)
    assert ts._load_grammar("rust") is not None
    assert calls == {"get": 2, "download": 1}  # tried, downloaded, retried


def test_doctor_strict_exit_code(monkeypatch):
    """`stitchgraph doctor --strict` exits non-zero when a supported grammar can't load
    (CI gate); plain `doctor` reports and exits 0."""
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from stitchgraph.adapters.cli import build_app
    from stitchgraph.core.extract import treesitter as ts
    monkeypatch.setattr(ts, "grammar_status", lambda: (False, [("rust", False, "missing")]))
    assert CliRunner().invoke(build_app(), ["doctor", "--strict"]).exit_code == 1
    assert CliRunner().invoke(build_app(), ["doctor"]).exit_code == 0


# -- Issue #10: single-candidate receiver calls are INFERRED, not EXTRACTED ----
def test_method_call_to_single_candidate_is_inferred_not_extracted(tmp_path):
    """A receiver-based call (`r.save()`) that resolves to a lone same-named project
    symbol is a guess (the receiver's type is unknown without inference), so its edge
    is INFERRED — not asserted as EXTRACTED. A direct call (`persist()`) stays
    EXTRACTED. Weight is unchanged on both, so reachability/find_stale are unaffected
    (cardinal-safe: the demotion never drops or under-counts a live caller)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.js": """
            class Repo {
              save() { return persist(); }
            }
            function persist() { return 1; }
            export function caller(r) { return r.save() + persist(); }
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        calls = store.resolved_edges(Relation.CALLS)
        by_dst = {e.dst_id.split("::")[-1]: e for e in calls
                  if e.src == "app.js::caller"}
        # caller -> Repo.save (receiver call, single candidate) is INFERRED
        assert by_dst["Repo.save"].provenance.value == "inferred"
        assert by_dst["Repo.save"].weight == 1.0          # full weight: still reachable
        # caller -> persist (direct call) stays EXTRACTED
        assert by_dst["persist"].provenance.value == "extracted"
        # cardinal invariant: the live method reached only via the receiver call is
        # NOT flagged stale despite the demotion.
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Repo.save" not in stale


# -- Issue #11: scan structural findings reflect participating-edge provenance --
def test_scan_cycle_built_from_ambiguous_edges_is_demoted(tmp_path):
    """A cycle that exists *only* because of AMBIGUOUS (homonym over-approximated)
    edges is a resolution artifact, not a real coupling smell. scan must cap its
    urgency below ORANGE, lower its confidence, and set needs_review — while a cycle
    backed by confident EXTRACTED edges keeps its ORANGE 'look closer' (issue #11)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        # build()<->run() in a.js form the cycle, but both names are homonyms (also in
        # b.js), so every linking edge is AMBIGUOUS — the cycle is an artifact.
        "a.js": """
            export function build(){ return run(); }
            export function run(){ return build(); }
        """,
        "b.js": """
            export function build(){ return 2; }
            export function run(){ return 3; }
        """,
        # alpha()<->beta() have unique names -> EXTRACTED edges -> a real cycle.
        "c.js": """
            export function alpha(){ return beta(); }
            export function beta(){ return alpha(); }
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        cycles = {tuple(sorted(i["members"])): i
                  for i in sg.scan(store).result if i["kind"] == "cycle"}
        artifact = cycles[tuple(sorted(["a.js::build", "a.js::run"]))]
        assert artifact["urgency"] == "green"           # capped below ORANGE
        assert artifact["needs_review"] is True
        assert artifact["confidence"] < 0.5
        assert artifact["confident_edges"] == 0

        real = cycles[tuple(sorted(["c.js::alpha", "c.js::beta"]))]
        assert real["urgency"] == "orange"              # confident cycle unchanged
        assert real["needs_review"] is False
        assert real["confidence"] >= 0.8


def test_scan_god_object_from_ambiguous_edges_is_demoted(tmp_path):
    """A god object whose high coupling rests on name-ambiguous edges (homonym calls
    that edge to every same-named def) is a resolution artifact: scan caps it below
    ORANGE, sets needs_review, and reports the confident-only degree (issue #11)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    callers = "\n".join(f"export function caller{i}(){{ return hub(); }}"
                        for i in range(5))
    units = "\n".join(f"export function u{i}(){{ return {i}; }}" for i in range(5))
    hub_body = "; ".join(f"u{i}()" for i in range(5))
    _mk(tmp_path, {
        # hub() is a homonym (also in dup.js) so every call to it is AMBIGUOUS; its
        # fan-in of 5 collapses to 0 confident edges.
        "a.js": f"export function hub(){{ {hub_body}; return 0; }}\n{units}\n{callers}\n",
        "dup.js": "export function hub(){ return 9; }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        god = next(i for i in sg.scan(store).result
                   if i["kind"] == "god_object" and i["node"] == "a.js::hub")
        assert god["urgency"] == "green"            # capped below ORANGE
        assert god["needs_review"] is True
        assert god["confident_fan_in"] == 0         # the coupling vanishes when confident
        assert god["confident_fan_out"] == 5


def test_python_unresolved_receiver_call_is_inferred_not_extracted(tmp_path):
    """Mirror of #10 in the Python ast extractor: an attribute call whose receiver
    type can't be resolved scope-aware (`x.save()` where `x` is an unknown/external
    type) is a name-only guess even with a single same-named project method — so it's
    INFERRED, not EXTRACTED. A self/local-typed call (`r.save()` with `r = Repo()`)
    stays EXTRACTED, and a bare call stays EXTRACTED. Weight is 1.0 throughout, so
    reachability/find_stale are unchanged (cardinal-safe)."""
    _mk(tmp_path, {
        "m.py": """
            class Repo:
                def save(self):
                    return 1

            def unknown_receiver(x):
                return x.save()

            def self_call():
                r = Repo()
                return r.save()

            if __name__ == "__main__":
                unknown_receiver(None)
                self_call()
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        calls = store.resolved_edges(Relation.CALLS)
        prov = {(e.src.split("::")[-1], e.dst_id.split("::")[-1]): e for e in calls}
        unk = prov[("unknown_receiver", "Repo.save")]
        assert unk.provenance.value == "inferred"      # receiver type unknown -> guess
        assert unk.weight == 1.0                        # still fully reachable
        typed = prov[("self_call", "Repo.save")]
        assert typed.provenance.value == "extracted"    # local-typed receiver -> certain
        # cardinal: the method reached only via the unresolved receiver call is not stale
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Repo.save" not in stale


def test_csharp_qualified_constructor_stays_extracted(tmp_path):
    """Panel KK (sonnet): a namespace-qualified C# constructor `new MyApp.Widget()`
    has an `object_creation_expression` whose `type` field is a `qualified_name` node.
    The #10 receiver demotion must NOT fire for constructors (they name a type
    directly, no receiver ambiguity) — the edge stays EXTRACTED. A genuine method
    call on the constructed object still demotes to INFERRED."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.cs": """
            namespace MyApp {
                public class Program {
                    public static void Main() { var w = new MyApp.Widget(); w.Run(); }
                }
                public class Widget { public void Run() {} }
            }
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        calls = store.resolved_edges(Relation.CALLS)
        ctor = next(e for e in calls if e.dst_symbol == "Widget")
        assert ctor.provenance.value == "extracted"   # constructor not demoted
        assert ctor.weight == 1.0
        run = next((e for e in calls if e.dst_symbol == "Run"), None)
        if run is not None:                            # receiver method call still demoted
            assert run.provenance.value == "inferred"


# -- Issue #18: risk scopes git history from the indexed root, not cwd ----------
def test_risk_defaults_to_indexed_root_not_cwd(tmp_path, monkeypatch):
    """`risk()` with no path must use the indexed root recorded in the DB (so
    `risk --db <db>` works from any cwd, like every other read op), not the process
    cwd. We index a git repo under tmp_path, then run risk from a *different* cwd and
    confirm the hotspot is the indexed repo's file (proving it didn't use cwd)."""
    import os
    import subprocess

    (tmp_path / "m.py").write_text("def a():\n    return b()\n\ndef b():\n    return 1\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       capture_output=True, env=env, check=True)
    git("init")
    git("add", "-A")
    git("commit", "-m", "init")

    # Query from a cwd that is NOT the indexed repo (use the parent dir).
    monkeypatch.chdir(tmp_path.parent)
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.risk(store)                      # no path -> indexed root from DB
        assert res.ok, res.reasons               # not a "not a git repository" refuse
        files = {h["file"] for h in res.result["hotspots"]}
        assert "m.py" in files                    # used the indexed repo, not cwd


# -- Issue #19: `stitchgraph --version` --------------------------------------
def test_cli_version_flag():
    """`stitchgraph --version` prints the package version and the active
    tree-sitter-language-pack line, then exits 0 (issue #19)."""
    pytest.importorskip("typer")
    import re

    from typer.testing import CliRunner

    from stitchgraph.adapters.cli import build_app
    app = build_app()
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert re.search(r"stitchgraph \d+\.\d+\.\d+", result.stdout)
    assert "tree-sitter-language-pack" in result.stdout
    # The --version callback must not swallow bare invocation: `stitchgraph` with no
    # command still shows help, not a silent exit 0.
    bare = CliRunner().invoke(app, [])
    assert bare.exit_code != 0
    assert "Usage" in bare.stdout or "Commands" in bare.stdout


def test_report_includes_risk_from_foreign_cwd(tmp_path, monkeypatch):
    """`report` passed repo='.' to risk(), so the risk section was silently skipped
    when run from outside the analysed repo — the same #18 root-scoping bug. With
    repo defaulting to None (→ indexed root), `report --db <db>` includes risk from
    any cwd."""
    import os
    import subprocess

    from stitchgraph.adapters.report import build_report

    (tmp_path / "m.py").write_text("def a():\n    return b()\n\ndef b():\n    return 1\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       capture_output=True, env=env, check=True)
    git("init")
    git("add", "-A")
    git("commit", "-m", "init")

    db = str(tmp_path / "g.db")
    with sg.Store(db) as store:
        sg.reindex(store, str(tmp_path))

    monkeypatch.chdir(tmp_path.parent)         # query from a foreign cwd
    report = build_report(db)                    # repo=None -> indexed root
    assert "## Risk" in report
    assert "not a git repository" not in report  # risk ran, wasn't skipped
    assert "m.py" in report                       # hotspot from the indexed repo


def test_version_attr_matches_installed_metadata():
    """`stitchgraph.__version__` must not drift from the installed distribution
    version (it was left stale at 1.0.3 through 1.0.4/1.0.5). It now derives from
    importlib.metadata, the same source `--version` uses (panel OO/PP)."""
    import re
    from importlib.metadata import version

    import stitchgraph
    assert stitchgraph.__version__ == version("stitchgraph")
    assert re.match(r"\d+\.\d+\.\d+", stitchgraph.__version__)


def test_design_section9_lists_only_real_operations():
    """design.md §9 must not advertise operations/params the CLI doesn't have — the
    recurring doc-vs-code drift behind #19.2. Pins the removed phantoms (panel
    NN/OO/PP): structure_smells, type_at, and trace_path's non-existent relations?."""
    from pathlib import Path

    from stitchgraph.core.operations import registry
    design = Path(__file__).resolve().parent.parent / "docs" / "design.md"
    text = design.read_text()
    names = {op.name for op in registry()}
    assert "structure_smells" not in text       # folded into scan
    assert "type_at" not in text                  # LSP roadmap, lives in STATUS.md
    assert "relations?" not in text               # trace_path takes only (src, sink)
    assert "type_at" not in names                 # sanity: really not an op


def test_risk_empty_churn_is_a_refusal_not_vacuous_ok(tmp_path):
    """risk() on a git repo whose indexed source files have no commit history must
    return ok=False (a real refusal), not ok=True with result={} — the latter made
    `report` render a blank Risk section and broke the no-vacuous-ok envelope
    contract (panels QQ/RR). report must show a 'skipped' line."""
    import os
    import subprocess

    from stitchgraph.adapters.report import build_report

    # Commit only a README; the indexed .py file is never committed → empty churn.
    (tmp_path / "README").write_text("readme\n")
    (tmp_path / "app.py").write_text("def main():\n    return 1\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       capture_output=True, env=env, check=True)
    git("init")
    git("add", "README")
    git("commit", "-m", "init")

    db = str(tmp_path / "g.db")
    with sg.Store(db) as store:
        sg.reindex(store, str(tmp_path))
        res = sg.risk(store)
        assert res.ok is False                 # real refusal, not vacuous ok=True
        assert res.result is None
    report = build_report(db, str(tmp_path))
    risk_section = report.split("## Risk", 1)[1]
    assert "skipped" in risk_section            # explained, not a blank section


def test_report_risk_section_never_blank_when_no_hotspots(tmp_path):
    """When risk() runs successfully but finds nothing (churn exists, but every file
    has zero centrality and there's no hidden coupling), risk legitimately returns
    ok=True with empty lists — like find_stale returning []. The report must render an
    explicit '(no risk ...)' line, never a blank section (panels SS/TT)."""
    import os
    import subprocess

    from stitchgraph.adapters.report import build_report

    # A single isolated function: committed (so churn>0) but no caller (centrality 0).
    (tmp_path / "app.py").write_text("def isolated():\n    return 1\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args],
                       capture_output=True, env=env, check=True)
    git("init")
    git("add", "-A")
    git("commit", "-m", "init")

    db = str(tmp_path / "g.db")
    with sg.Store(db) as store:
        sg.reindex(store, str(tmp_path))
        res = sg.risk(store)
        assert res.ok is True                       # ran fine, just found nothing
        assert res.result == {"hotspots": [], "hidden_coupling": []}
    risk_section = build_report(db, str(tmp_path)).split("## Risk", 1)[1]
    assert risk_section.strip()                      # not blank
    assert "no risk" in risk_section.lower()         # explicit empty marker


# -- Issue #21: [project.scripts] console entry points are roots ---------------
def test_pyproject_console_script_is_a_root(tmp_path):
    """A `[project.scripts]` target (a CLI's `main`) is the product, not dead code —
    design §4 lists it as a root, but the extractor never parsed pyproject.toml, so
    `find_stale` falsely flagged it. It's now tagged role `script` (issue #21). A
    genuinely-unused private fn still flags."""
    _mk(tmp_path, {
        "pyproject.toml": (
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
            '[project.scripts]\nmytool = "mypkg.cli:main"\n'
            '[project.gui-scripts]\nmygui = "mypkg.cli:gui"\n'
        ),
        "mypkg/__init__.py": "",
        "mypkg/cli.py": "from .core import public_api\n"
                        "def main():\n    return public_api()\n"
                        "def gui():\n    return public_api()\n",
        "mypkg/core.py": "def public_api():\n    return 1\n"
                         "def _dead_private():\n    return 2\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        main = next(n for n in store.nodes_by_name("main"))
        assert "script" in main.roles
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "main" not in stale          # console-script entry point = root
        assert "gui" not in stale           # gui-scripts target too
        assert "_dead_private" in stale     # genuinely unused, still flagged


def test_pyproject_script_role_requires_matching_module(tmp_path):
    """The script-root match requires BOTH the object name AND the module path, so a
    same-named function in an UNrelated module isn't mis-rooted (precision)."""
    _mk(tmp_path, {
        "pyproject.toml": (
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
            '[project.scripts]\nmytool = "mypkg.cli:main"\n'
        ),
        "mypkg/__init__.py": "",
        "mypkg/cli.py": "def main():\n    return 1\n",
        # a homonym `main` in an unrelated module must NOT get the script role
        "mypkg/other.py": "def main():\n    return 2\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        roles = {n.id: n.roles for n in store.nodes_by_name("main")}
        cli_main = next(i for i in roles if i.startswith("mypkg/cli.py::"))
        other_main = next(i for i in roles if i.startswith("mypkg/other.py::"))
        assert "script" in roles[cli_main]
        assert "script" not in roles[other_main]


# -- Issue #22: bash run-directly script top-level body is a root --------------
def test_bash_top_level_script_roots_its_functions(tmp_path):
    """A bash script that runs its work as bare top-level statements (no main()) is the
    bash analogue of #8: its module body is the entry point. The script node is now
    seeded as a root and its top-level calls — direct, via `$(...)`, and `trap NAME` —
    are rooted, so those functions aren't false-flagged. A function called nowhere
    still flags (issue #22)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "run.sh": (
            "#!/usr/bin/env bash\n"
            "get_versions() { echo v1; }\n"
            "check_config() { echo ok; }\n"
            "monitor() { echo go; }\n"
            "get_indices() { echo 0; }\n"
            "cleanup() { echo bye; }\n"
            "orphan() { echo unused; }\n"   # called nowhere -> stays flagged
            "\n"
            "trap cleanup EXIT\n"
            "versions=$(get_versions)\n"
            "check_config\n"
            "indices=$(get_indices)\n"
            "monitor\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        for live in ("get_versions", "check_config", "monitor", "get_indices", "cleanup"):
            assert live not in stale, f"{live} wrongly flagged stale"
        assert "orphan" in stale            # genuinely unused -> still flagged


def test_pyproject_class_method_target_matches_exact_qualified_name(tmp_path):
    """A `Class.method` console-script target roots only that method, not a same-named
    method on a different class in the same file (panel WW — leaf-only over-rooting)."""
    _mk(tmp_path, {
        "pyproject.toml": '[project]\nname = "m"\nversion = "0.1"\n'
                          '[project.scripts]\nt = "m.cli:App.run"\n',
        "m/__init__.py": "",
        "m/cli.py": "class App:\n    def run(self):\n        return 1\n"
                    "class Dead:\n    def run(self):\n        return 2\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        roles = {n.id: n.roles for n in store.nodes_by_name("run")}
        app = next(i for i in roles if i.endswith("App.run"))
        dead = next(i for i in roles if i.endswith("Dead.run"))
        assert "script" in roles[app]
        assert "script" not in roles[dead]   # different class, not the target


def test_bash_trap_does_not_root_signal_name_functions(tmp_path):
    """`trap cleanup EXIT` roots only the handler `cleanup`; the signal word `EXIT` is
    not rooted, so a function that happens to be named after a signal isn't spuriously
    kept live (panel XX)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "s.sh": "#!/usr/bin/env bash\n"
                "EXIT() { echo signal-named; }\n"   # shares a signal name, unused
                "cleanup() { echo real; }\n"
                "trap cleanup EXIT\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "cleanup" not in stale       # the handler is rooted
        assert "EXIT" in stale              # signal name, not a rooted handler


def test_pyproject_package_init_entry_point_is_rooted(tmp_path):
    """A console-script target whose module is a *package* (`pkg:_main`) lives in
    `pkg/__init__.py`, not `pkg.py`. The suffix matcher must try the `__init__.py`
    candidate too, or an underscore/non-__all__ entry-point function is false-flagged
    dead — the #21 bug for the package-init case (panel ZZ, cardinal-class)."""
    _mk(tmp_path, {
        "pyproject.toml": '[project]\nname = "pkg"\nversion = "0.1"\n'
                          '[project.scripts]\nmy-tool = "pkg:_main"\n',
        "pkg/__init__.py": '__all__ = ["public"]\n'
                           "def public():\n    return 1\n"
                           "def _main():\n    return public()\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        main = next(n for n in store.nodes_by_name("_main"))
        assert "script" in main.roles
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "_main" not in stale          # package-level entry point = root


def test_bash_trap_handler_parsing_matrix(tmp_path):
    """The trap handler slot is parsed precisely (panels XX/YY/ZZ): root only the
    handler ARG, never a trailing signal word, handle the `-` reset, option flags,
    quoted-identifier handlers, and inline/empty/dynamic strings."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language

    from stitchgraph.core.extract import treesitter as ts
    parser = Parser(get_language("bash"))
    cases = {
        "trap cleanup EXIT": ["cleanup"],
        "trap - EXIT": [],                       # reset, no handler
        "trap -- cleanup EXIT": ["cleanup"],     # -- option skipped
        "trap -p": [],                           # list mode, no handler
        'trap "" EXIT': [],                      # empty handler, signal not promoted
        "trap 'cleanup' EXIT": ["cleanup"],      # quoted identifier handler
        "trap 'rm -f x' EXIT": [],               # inline command, not an identifier
        "trap cleanup EXIT INT TERM": ["cleanup"],  # multi-signal, handler only
        "trap -p EXIT": [],                      # print mode: signal arg, not a handler
        "trap -l EXIT": [],                      # list mode: no handler
    }
    for code, expected in cases.items():
        src = ("#!/usr/bin/env bash\n" + code + "\n").encode()
        tree = parser.parse(src)
        got = [n for n, _ in ts._bash_trap_handlers(tree.root_node, src)]
        assert got == expected, f"{code!r} -> {got}, expected {expected}"


def test_class_method_console_script_keeps_class_live(tmp_path):
    """A `Class.method` console-script target roots the method AND its enclosing class —
    the class is genuinely live (the entry point can't reach the method without it).
    The "method live, class dead" cardinal shape (panel DDD)."""
    _mk(tmp_path, {
        "pyproject.toml": '[project]\nname = "m"\nversion = "0.1"\n'
                          '[project.scripts]\nt = "m.cli:App.run"\n',
        "m/__init__.py": "",
        "m/cli.py": "class App:\n    def run(self):\n        return 1\n"
                    "class Dead:\n    def run(self):\n        return 2\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "App" not in stale           # enclosing class of the entry method = live
        assert "App.run" not in stale
        assert "Dead" in stale              # genuinely unused class still flags


def test_class_instantiated_in_main_block_is_live(tmp_path):
    """A class instantiated in `if __name__ == "__main__":` (`Worker().run()`) is a live
    entry root, like a called function — the class and the methods it invokes must not be
    flagged dead (panel DDD, cardinal; a very common Python script idiom)."""
    _mk(tmp_path, {
        "app.py": "class Worker:\n    def run(self):\n        return 1\n\n"
                  'if __name__ == "__main__":\n    Worker().run()\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Worker" not in stale
        assert "Worker.run" not in stale


def test_reindex_survives_broken_symlink(tmp_path):
    """A broken symlink (common with submodules / CI) must not abort the whole reindex —
    the Python ast extractor and resolve context now skip an unreadable file like the
    tree-sitter extractor already does (panel DDD)."""
    (tmp_path / "good.py").write_text("def main():\n    return 1\n")
    import os
    os.symlink(str(tmp_path / "nonexistent.py"), str(tmp_path / "broken.py"))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))         # must not raise
        assert "good.py::main" in set(store.all_node_ids())


def test_entrypoint_class_rescue_does_not_root_all_public_methods(tmp_path):
    """The class-rescue for entry points is narrow: a class instantiated in `__main__`
    keeps the methods it *invokes* live, NOT every public method. So a same-named class
    in an unrelated module doesn't get its whole method surface hidden via a global
    name collision (panel EEE — bounding the over-root blast radius)."""
    _mk(tmp_path, {
        "p/__init__.py": "",
        "p/cli.py": "class Worker:\n    def run(self):\n        return 1\n\n"
                    'if __name__ == "__main__":\n    Worker().run()\n',
        # unrelated, never-used Worker; `extra` is not invoked anywhere
        "p/other.py": "class Worker:\n    def run(self):\n        return 1\n"
                      "    def extra(self):\n        return 2\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "p/cli.py::Worker" not in stale       # the real entry class stays live
        # the unrelated class's NON-invoked public method must still be flaggable
        assert "p/other.py::Worker.extra" in stale


def test_reindex_skips_named_pipe_without_hanging(tmp_path):
    """A FIFO (or other non-regular file) named `*.py` must not hang reindex: `open()`
    on a FIFO with no writer blocks forever, and the OSError guard never fires because
    the open succeeds. The file walk now skips non-regular files (panel FFF)."""
    import os
    if not hasattr(os, "mkfifo"):
        import pytest as _pytest
        _pytest.skip("mkfifo not available on this platform")
    (tmp_path / "good.py").write_text("def good():\n    return 1\n")
    os.mkfifo(str(tmp_path / "blocker.py"))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))             # must return, not hang
        assert "good.py::good" in set(store.all_node_ids())


def test_reindex_skips_named_pipe_in_resolver_pipeline(tmp_path):
    """The FFF FIFO-skip guard must also cover the route/template resolvers, which do
    their OWN rglob walks + read_bytes()/read_text() (express/jsfetch/spring/html). The
    Express and Spring resolvers run unconditionally on every reindex, so a FIFO named
    `*.js`/`*.java`/`*.html` would hang reindex even though the extractors are guarded
    (panel GGG — two independent opus reviewers, exit 124 before the fix)."""
    import os
    if not hasattr(os, "mkfifo"):
        import pytest as _pytest
        _pytest.skip("mkfifo not available on this platform")
    (tmp_path / "good.py").write_text("def good():\n    return 1\n")
    os.mkfifo(str(tmp_path / "blocker.js"))      # express resolver (unconditional)
    os.mkfifo(str(tmp_path / "Blocker.java"))    # spring resolver (unconditional)
    os.mkfifo(str(tmp_path / "blocker.html"))    # html template resolver
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))             # must return, not hang
        assert "good.py::good" in set(store.all_node_ids())


def test_reindex_skips_fifo_pyproject_toml_without_hanging(tmp_path):
    """`_console_script_targets` reads `<root>/pyproject.toml` on every reindex (the #21
    console-script path). It must guard with `is_file()`, not `exists()`: `exists()` is True
    for a FIFO, and the subsequent `read_text()` opens it and blocks forever — the OSError
    guard never fires on a blocking open (panel JJJ — a second instance of the FIFO hang
    class, in a fixed-path read rather than an rglob walk)."""
    import os
    if not hasattr(os, "mkfifo"):
        import pytest as _pytest
        _pytest.skip("mkfifo not available on this platform")
    (tmp_path / "good.py").write_text("def good():\n    return 1\n")
    os.mkfifo(str(tmp_path / "pyproject.toml"))  # _console_script_targets read site
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))             # must return, not hang
        assert "good.py::good" in set(store.all_node_ids())


def test_reindex_skips_named_pipe_in_route_gated_resolvers(tmp_path):
    """The jsfetch/html resolvers only walk when ROUTE nodes already exist, so the
    unconditional-resolver test above doesn't exercise their guard. Seed a real Python
    route, then plant FIFO `*.js`/`*.html` siblings: reindex must not hang and the route
    must still be linked (panel III — route-gated coverage)."""
    import os
    if not hasattr(os, "mkfifo"):
        import pytest as _pytest
        _pytest.skip("mkfifo not available on this platform")
    (tmp_path / "app.py").write_text(
        "import flask\n"
        "app = flask.Flask(__name__)\n"
        "@app.route('/api/x')\n"
        "def handler():\n"
        "    return 'ok'\n"
    )
    os.mkfifo(str(tmp_path / "blocker.js"))      # jsfetch resolver (route-gated)
    os.mkfifo(str(tmp_path / "blocker.html"))    # html resolver (route-gated)
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))             # must return, not hang
        assert "app.py::handler" in set(store.all_node_ids())


def test_load_coverage_returns_empty_on_fifo_without_hanging(tmp_path):
    """`load_coverage` (reached via `ingest_trace`) reads a user-named trace path and
    promises "Empty on any problem", swallowing OSError to return ({}, ""). A FIFO would
    block forever in read_text() instead — the OSError guard never fires on a blocking
    open. It must guard with is_file() so a FIFO honours the empty-on-problem contract
    (FIFO hang class, proactively closed)."""
    import os
    if not hasattr(os, "mkfifo"):
        import pytest as _pytest
        _pytest.skip("mkfifo not available on this platform")
    from stitchgraph.core.runtime import load_coverage
    fifo = tmp_path / "coverage.fifo"
    os.mkfifo(str(fifo))
    assert load_coverage(str(fifo)) == ({}, "")      # must return empty, not hang


# -- Panel R11A / opus (release-blocking CRASH): non-UTF-8 ids must not abort --
def test_store_add_node_edge_skip_non_utf8_id_without_crashing(tmp_path):
    """A source file/dir with a non-UTF-8 name (Latin-1/Shift-JIS bytes on POSIX) is
    decoded via surrogateescape into a lone-surrogate node id; sqlite can't bind it, so
    `add_node`/`add_edge`'s INSERT raised UnicodeEncodeError and aborted reindex's bulk
    insert. The write must skip the unstorable row (read side already refuses it), not
    crash (panel R11A)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.store import Store
    bad = "bad\udcffname.py::fn"
    with Store(":memory:") as store, store.conn:
        store.add_node(Node(id=bad, kind=NodeKind.FUNCTION, name="fn", location="x:1:0"))
        store.add_node(Node(id="good.py::g", kind=NodeKind.FUNCTION, name="g", location="g.py:1:0"))
        store.add_edge(Edge(src=bad, relation=Relation.CALLS, dst_symbol="g", dst_id=None,
                            weight=1.0, provenance=Provenance.INFERRED, location="x:1:0", source="t"))
        ids = set(store.all_node_ids())
        assert "good.py::g" in ids        # the storable node survives
        assert bad not in ids             # the surrogate node is skipped, not crashed on


def test_reindex_survives_non_utf8_source_filename(tmp_path):
    """End-to-end of the R11A class: a real `*.py` whose filename contains a raw non-UTF-8
    byte must not abort reindex — the surrogate-id nodes are dropped and the rest of the
    project still indexes."""
    import os
    try:
        raw = os.fsencode(str(tmp_path)) + b"/bad\xffname.py"
        with open(raw, "wb") as fh:
            fh.write(b"def orphan():\n    return 1\n")
    except OSError:
        import pytest as _pytest
        _pytest.skip("filesystem rejects non-UTF-8 names")
    (tmp_path / "good.py").write_text("def good():\n    return 1\n")
    with sg.Store(":memory:") as store:
        res = sg.reindex(store, str(tmp_path))       # must not raise / abort
        assert res.ok
        assert "good.py::good" in set(store.all_node_ids())


# -- Panel R11B / sonnet (LOW): replace_file phantom hole ----------------------
def test_replace_file_drops_phantom_hole_when_ambiguous_target_deleted(tmp_path):
    """When one arm of an ambiguous fan-out (caller -> [h1, h2] as two AMBIGUOUS edges)
    is removed by deleting its file, `_invalidate_dangling` turned that edge into a hole
    even though the reference is still satisfied by the surviving sibling — over-counting
    find_holes by one. replace_file now drops the redundant hole (panel R11B)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.store import Store
    h1 = Node(id="h1.py::helper", kind=NodeKind.FUNCTION, name="helper", location="h1.py:1:0")
    h2 = Node(id="h2.py::helper", kind=NodeKind.FUNCTION, name="helper", location="h2.py:1:0")
    caller = Node(id="caller.py::call_me", kind=NodeKind.FUNCTION, name="call_me", location="caller.py:1:0")

    def mk(dst):
        return Edge(src="caller.py::call_me", relation=Relation.CALLS, dst_symbol="helper",
                    dst_id=dst, weight=0.5, provenance=Provenance.AMBIGUOUS,
                    location="caller.py:2:0", source="test")
    with Store(":memory:") as store:
        with store.conn:
            store.add_node(h1, file="h1.py")
            store.add_node(h2, file="h2.py")
            store.add_node(caller, file="caller.py")
            store.add_edge(mk("h1.py::helper"), file="caller.py")
            store.add_edge(mk("h2.py::helper"), file="caller.py")
        store.replace_file("h1.py", [], [])
        assert len(store.unresolved_edges()) == 0     # no phantom hole
        # exactly the surviving sibling remains — no duplicate edge inflating fan_in
        assert [e.dst_id for e in store.resolved_edges()] == ["h2.py::helper"]


# -- Panel R11B / sonnet (LOW): phantom db::TABLE from non-standard SQL --------
def test_sql_delete_update_table_do_not_create_phantom_table_node():
    """`DELETE TABLE x` / `UPDATE TABLE x` are non-standard (MySQL-isms); sqlglot misparses
    the `TABLE` keyword itself as the table, creating a phantom `db::TABLE` node while missing
    the real one. The resolver now skips a bare `table` identifier (panel R11B)."""
    pytest.importorskip("sqlglot")
    from stitchgraph.core.resolve.sql import _link
    for sql in ("DELETE TABLE users", "UPDATE TABLE users SET x = 1"):
        nodes: dict = {}
        edges: list = []
        _link(nodes, edges, "some.py::fn", "some.py", 1, sql)
        assert "db::TABLE" not in nodes               # no phantom keyword node


# -- Panel R11B / opus (LOW): non-UTF-8 template must not disable resolver ------
def test_html_resolver_survives_non_utf8_template(tmp_path):
    """A non-UTF-8 HTML template raised UnicodeDecodeError in `read_text(encoding="utf-8")`,
    swallowed by run_resolvers' broad except — silently disabling the HTML resolver for the
    whole project. read_text now decodes lossily so other templates' forms still link, and
    the bad one is scanned for what decodes (panel R11B)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    (tmp_path / "app.py").write_text(
        "import flask\n"
        "app = flask.Flask(__name__)\n"
        "@app.route('/submit')\n"
        "def handler():\n"
        "    return 'ok'\n"
    )
    (tmp_path / "bad.html").write_bytes(
        b'<form action="/submit" method="post">\xff caf\xe9</form>')   # Latin-1 bytes
    (tmp_path / "good.html").write_text('<form action="/submit" method="post"></form>')
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))             # must not disable the resolver
        submits = [e for e in store.resolved_edges(Relation.SUBMITS_TO)]
        srcs = {e.src for e in submits}
        # the well-formed template still links despite the bad sibling
        assert "good.html::template" in srcs


# -- Panel R12B / opus (CARDINAL): module-level use keeps a symbol live ---------
def test_python_class_used_only_at_module_level_is_not_stale(tmp_path):
    """A class instantiated only by module-level code (a registry value / dispatch table /
    `REGISTRY = Spec(1)`) runs when the module loads, so it is live whenever the module is
    loaded. The Python extractor edged only def/class-body scope, never module scope, so
    such a class had no incoming edge and was flagged dead — reproduced in stitchgraph's own
    `LangSpec` (panel R12, cardinal). A genuinely-unused class is still flagged."""
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/conf.py": """
            class Spec:
                def __init__(self, n): self.n = n
            class TrulyDead:
                def gone(self): return 1
            REGISTRY = Spec(1)
            def get_spec():
                return REGISTRY
        """,
        "pkg/api.py": """
            from .conf import get_spec
            __all__ = ["run"]
            def run():
                return get_spec()
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "pkg/conf.py::Spec" not in stale          # live via module-level REGISTRY
        assert "pkg/conf.py::TrulyDead" in stale          # genuinely unused, still flagged


def test_js_toplevel_call_chain_is_not_stale(tmp_path):
    """JS/TS/Ruby module top-level code runs on load; a function called only from a
    top-level statement (`bootstrap()`) is live. The tree-sitter extractor ran the
    module-scope use scan only for test/script files, so an ordinary exported module's
    top-level call chain was flagged dead (panel R12, cardinal)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "index.js": """
            export function publicApi() { return 1; }
            function bootstrap() { return configure(); }
            function configure() { return 2; }
            bootstrap();
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "index.js::bootstrap" not in stale
        assert "index.js::configure" not in stale


def test_cpp_out_of_line_method_keeps_its_class_live(tmp_path):
    """A C++ class declared in a header with all members defined out-of-line in a .cpp
    (`int StringUtils::length(...){...}`) had the `StringUtils::` qualifier stripped, so the
    method became a bare free function with no link to its class — the class was flagged dead
    though a member is called (panel R12B, cardinal). A genuinely-unused member is still
    flagged."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "util.h": "class StringUtils { public: static int length(const char* s); "
                  "static int hash(const char* s); };\n",
        "util.cpp": '#include "util.h"\n'
                    "int StringUtils::length(const char* s){ return 1; }\n"
                    "int StringUtils::hash(const char* s){ return 2; }\n",
        "main.cpp": '#include "util.h"\nint main(){ return StringUtils::length("hi"); }\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "util.h::StringUtils" not in stale         # live: length() is called
        assert "util.cpp::hash" in stale                  # genuinely unused, still flagged


# -- Panel R12A+B / haiku (crash): adapter must refuse on an unusable --db ------
def test_cli_command_refuses_on_unusable_db_without_traceback(tmp_path):
    """`--db <a directory>` (or FIFO / device / unwritable path) made `Store()` raise an
    uncaught sqlite OperationalError that escaped as a traceback. The CLI/MCP/report
    adapters must return an envelope/clean message, not crash (panel R12)."""
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from stitchgraph.adapters.cli import build_app
    bad = tmp_path / "is_a_dir"
    bad.mkdir()
    res = CliRunner().invoke(build_app(), ["find-symbol", "x", "--db", str(bad)])
    assert res.exit_code == 0          # clean refusal, not a crash
    assert "cannot open index database" in res.stdout


def test_report_adapter_refuses_on_unusable_db(tmp_path):
    """The report adapter opens its own Store; an unusable --db must yield a one-line
    report, not a sqlite traceback (panel R12)."""
    from stitchgraph.adapters.report import build_report
    bad = tmp_path / "dir.db"
    bad.mkdir()
    out = build_report(db=str(bad))
    assert "cannot open index database" in out


# -- Panel R12B / sonnet (metric): replace_file must not inflate fan_in ---------
def test_replace_file_dedups_resolved_edges_matching_reindex(tmp_path):
    """The incremental `replace_file` path bulk-inserts + resolves edges without reindex's
    `_dedup_edges` pass, so a function named like its module (two collapsed nodes → two
    edges per call site) and a hole resolved alongside a resolved sibling left duplicate
    rows that inflated fan_in / pagerank (panel R12B). replace_file now dedups to match
    reindex."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.extract.python import extract_project
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {
        "hub.py": "def hub(): pass\n",
        "main.py": "from hub import hub\ndef c1(): hub()\ndef c2(): hub()\ndef c3(): hub()\n",
    })
    with sg.Store(":memory:") as ref:
        sg.reindex(ref, str(tmp_path))
        expected = fan_in(ref).get("hub.py::hub")
    nodes, edges = extract_project(str(tmp_path))
    with sg.Store(":memory:") as store:
        for n in nodes:
            store.add_node(n)
        for e in edges:
            store.add_edge(e)
        store.commit()
        for f in ("hub.py", "main.py"):
            fn = [n for n in nodes if n.id.split("::", 1)[0] == f]
            fe = [e for e in edges if e.src.split("::", 1)[0] == f]
            store.replace_file(f, fn, fe)
        assert fan_in(store).get("hub.py::hub") == expected   # no inflation vs reindex


def test_replace_file_no_duplicate_when_hole_resolves_alongside_sibling(tmp_path):
    """If the edges handed to replace_file contain both a resolved edge and a hole for the
    same (src, relation, dst_symbol), resolving the hole must not add a second row to a
    target that already has a resolved edge (panel R12B finding 2)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.reach import fan_in
    with sg.Store(":memory:") as store:
        a = Node(id="main.py::A", kind=NodeKind.FUNCTION, name="A", location="main.py:1:0")
        foo = Node(id="lib.py::Foo", kind=NodeKind.FUNCTION, name="Foo", location="lib.py:1:0")
        store.add_node(a, file="main.py")
        store.add_node(foo, file="lib.py")
        resolved = Edge(src="main.py::A", relation=Relation.CALLS, dst_symbol="Foo",
                        dst_id="lib.py::Foo", weight=1.0, provenance=Provenance.EXTRACTED,
                        location="main.py:2:0", source="ast")
        store.add_edge(resolved, file="main.py")
        store.commit()
        hole = Edge(src="main.py::A", relation=Relation.CALLS, dst_symbol="Foo", dst_id=None,
                    weight=0.7, provenance=Provenance.INFERRED, location="main.py:3:0",
                    source="ast")
        store.replace_file("main.py", [a], [resolved, hole])
        assert fan_in(store).get("lib.py::Foo") == 1      # one logical caller, not two


# -- Panel R13A / opus (CARDINAL): C++ nested-class / operator out-of-line defs --
def test_cpp_nested_class_out_of_line_method_keeps_helper_live(tmp_path):
    """An out-of-line method whose declarator is a NESTED qualified_identifier
    (`Outer::Inner::tick`) or an `operator_name` (`Vec::operator+`) returned None from
    `_name_of`, so the WHOLE function_definition was silently dropped — a helper called only
    from such a body was then flagged dead (panel R13A, cardinal). Operators are implicitly
    invoked, so they (and their callees) are rooted."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.h": "class Outer { public: class Inner { public: int tick(); }; };\n"
                 "struct Vec { int x; Vec operator+(const Vec& o); };\n"
                 "int helper();\nint combine();\n",
        "lib.cpp": '#include "lib.h"\n'
                   "int helper(){ return 7; }\nint combine(){ return 9; }\n"
                   "int Outer::Inner::tick(){ return helper(); }\n"
                   "Vec Vec::operator+(const Vec& o){ combine(); return *this; }\n",
        "main.cpp": '#include "lib.h"\n'
                    "int main(){ Outer::Inner i; Vec a; Vec b; (void)(a+b); return i.tick(); }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "lib.cpp::helper" not in stale     # live via Outer::Inner::tick
        assert "lib.cpp::combine" not in stale     # live via Vec::operator+ (rooted)


# -- Panel R13A+B / opus (over-suppression): same-basename import disambiguation -
def test_import_does_not_falsely_link_same_basename_module_in_other_package(tmp_path):
    """`from pkg1 import helper` must not bind `helper` (function OR the same-basename
    `pkg2.helper` module, which the index aliases globally) to pkg2 — that falsely made
    pkg2's module reachable, masking its genuinely-dead class and coupling `main` to pkg2 in
    impact_of (panel R13B)."""
    _mk(tmp_path, {
        "pkg1/__init__.py": "",
        "pkg2/__init__.py": "",
        "pkg1/helper.py": "def helper():\n    return 1\n",
        "pkg2/helper.py": "class DeadHelper:\n    def m(self): return 2\n"
                          "INST = DeadHelper()\ndef helper():\n    return 3\n",
        "main.py": 'from pkg1 import helper\n__all__ = ["main"]\ndef main():\n    return helper()\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "pkg2/helper.py::DeadHelper" in stale       # never imported -> genuinely dead
        impact = sg.impact_of(store, "pkg2.helper")
        assert "main.py::main" not in (impact.result or {}).get("blast_radius", [])


# -- Panel R13A / sonnet (metric): orient hubs exclude module pseudo-nodes -------
def test_orient_hubs_exclude_module_nodes(tmp_path):
    """Module nodes carry high import-coupling (amplified by the module->module IMPORTS
    edges that make module-level liveness work), which crowded real functions out of
    orient()'s "read these first" hub list. Hubs are now code entities only (panel R13A)."""
    from stitchgraph.core.model import NodeKind
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/hub.py": "def hub_func():\n    return 1\n",
        **{f"pkg/c{i}.py": "from .hub import hub_func\n"
           f"def use{i}():\n    return hub_func()\n" for i in range(6)},
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        hubs = sg.orient(store).result["top_hubs"]
        kinds = {store.get_node(h["id"]).kind for h in hubs}
        assert NodeKind.MODULE not in kinds               # no module pseudo-nodes in hubs


# -- Panel R14A / opus (CARDINAL): C++ conversion operators must not be dropped --
def test_cpp_conversion_operator_keeps_callee_live(tmp_path):
    """A C++ conversion operator (`operator bool`, `operator int`) parses as an
    `operator_cast` node whose `type` field the name-walk followed to the target type,
    yielding None — so the WHOLE function_definition was dropped and a helper called only
    from its body was flagged dead (panel R14A, cardinal). operator_cast is now named and
    rooted like other operators."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "n.h": "struct Wrap { int x; Wrap(int v): x(v) {} operator bool() const; };\n"
               "int only_from_conv();\n",
        "n.cpp": '#include "n.h"\n'
                 "int only_from_conv(){ return 42; }\n"
                 "Wrap::operator bool() const { return only_from_conv() > 0; }\n",
        "main.cpp": '#include "n.h"\nint main(){ Wrap w(3); if((bool)w) return 1; return 0; }\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "n.cpp::only_from_conv" not in stale     # live via Wrap::operator bool()


# -- Panel R14A / opus (CARDINAL): relative `from . import sib` module-load --------
def test_relative_bare_from_import_keeps_module_scope_code_live(tmp_path):
    """`from . import sib` / `from .. import x` have node.module=None, so the ImportFrom
    branch (and the module-load edge that carries module-level liveness) was skipped — a
    class used only at the imported sibling module's scope was flagged dead (panel R14A,
    cardinal)."""
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/svc.py": "class Service:\n    def handle(self): return helper_fn()\n"
                      "def helper_fn(): return 1\nSVC = Service()\nSVC.handle()\n",
        "pkg/boot.py": "from . import svc\ndef boot(): return svc.SVC\n",
        "main.py": 'from pkg.boot import boot\n__all__ = ["main"]\ndef main(): return boot()\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "pkg/svc.py::Service" not in stale
        assert "pkg/svc.py::Service.handle" not in stale
        assert "pkg/svc.py::helper_fn" not in stale


# -- Panel R14A / sonnet (CARDINAL regression): function named like its module ----
def test_root_function_named_like_its_module_is_not_dropped(tmp_path):
    """A root-level `utils.py` defining `def utils()` gives the MODULE and FUNCTION nodes
    the SAME id (`utils.py::utils`); the round-13 module-filter in _ref_edges then dropped
    the call edge, flagging the function dead — the near-universal `main.py`+`def main()`
    pattern (panel R14A, cardinal). A shared id is kept out of the module filter."""
    _mk(tmp_path, {
        "utils.py": "def utils():\n    return 42\n",
        "app.py": "def run():\n    utils()\n",
        "pyproject.toml": '[project.scripts]\nrun = "app:run"\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "utils.py::utils" not in stale          # called by run() (a console script)


# -- Panel R14A / sonnet (metric): MODULE nodes excluded from scan cycle/god_object
def test_scan_excludes_module_pseudo_nodes_from_structural_findings(tmp_path):
    """A module->module import cycle (circular import) and a heavily-imported module that
    makes many module-level calls are not OOP cycles/god_objects — scan must not surface
    MODULE pseudo nodes under those code-entity findings (panel R14A)."""
    from stitchgraph.core.model import NodeKind
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        # circular import: a <-> b at module scope
        "pkg/a.py": "from pkg import b\nVALUE_A = 1\ndef fa(): return b.VALUE_B\n",
        "pkg/b.py": "from pkg import a\nVALUE_B = 2\ndef fb(): return a.VALUE_A\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        issues = sg.scan(store).result
        module_findings = [i for i in issues
                           if i.get("kind") in ("cycle", "god_object")
                           and (n := store.get_node(i["node"])) is not None
                           and n.kind is NodeKind.MODULE]
        assert module_findings == []


# -- Panel R15A / opus (CARDINAL): C/C++ reference-return defs must not be dropped -
def test_cpp_reference_return_method_keeps_helper_live(tmp_path):
    """A C/C++ function/method returning a reference (`int& W::refMethod()`, `const T&`)
    has a `reference_declarator` that exposes its inner function_declarator as an UNNAMED
    child, so `_name_of` dead-ended and the WHOLE def was dropped — a helper called only
    from its body was flagged dead (panel R15A, cardinal). The declarator walk now descends
    through reference (and all) declarator wrappers."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "a.h": "class W { public: int& refMethod(); };\nint helper();\n",
        "a.cpp": '#include "a.h"\nint helper(){ return 5; }\n'
                 "int& W::refMethod(){ static int x; helper(); return x; }\n",
        "main.cpp": '#include "a.h"\nint main(){ W w; return w.refMethod(); }\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "a.cpp::helper" not in stale       # live via W::refMethod()


# -- Panel R15B / opus (CARDINAL): module-level decorator registry / bare decorator -
def test_module_level_decorated_defs_and_decorator_are_live(tmp_path):
    """A module-level decorated def is registered/wrapped when the module loads — a plugin
    handler (`@register('x') def x`), a `@memo`-wrapped def, and the bare-name decorator
    `memo` itself were all flagged dead while the equivalent dict-literal registry was
    rescued (panel R15B). They are now edged from the module node."""
    _mk(tmp_path, {
        "app/__init__.py": "",
        "app/plugins.py": """
            PLUGINS = {}
            def plugin(name):
                def wrap(fn):
                    PLUGINS[name] = fn
                    return fn
                return wrap
            @plugin('greet')
            def greet(): return "hi"
            def memo(fn): return fn
            @memo
            def cached_thing(): return 1
        """,
        "app/run.py": """
            from .plugins import PLUGINS
            __all__ = ["run"]
            def run(name): return PLUGINS[name]()
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        for nid in ("app/plugins.py::greet", "app/plugins.py::cached_thing",
                    "app/plugins.py::memo"):
            assert nid not in stale


# -- Panel R15B / sonnet (LOW crash): replace_file on a lone-surrogate filename ----
def test_replace_file_surrogate_filename_does_not_crash():
    """`replace_file`'s `DELETE ... WHERE file = ?` binds failed on a lone-surrogate
    filename (POSIX surrogateescape on a non-UTF-8 path); add_node/add_edge already guard
    this, so replace_file must too (panel R15B)."""
    from stitchgraph.core.store import Store
    with Store(":memory:") as store:
        store.replace_file("\ud800.py", [], [])   # must not raise


# -- Panel R15B / sonnet (metric): function named like its module — call deduped ---
def test_function_named_like_module_call_is_extracted_not_ambiguous(tmp_path):
    """A function named like its file (`def compute()` in `compute.py`) makes the MODULE
    and FUNCTION nodes share one id, listed twice in by_name; without dedup a within-file
    call resolved AMBIGUOUS (0.5) and a live stub was demoted RED->ORANGE. The candidate
    list is now deduped, so the stub stays RED (panel R15B)."""
    _mk(tmp_path, {
        "compute.py": "def compute(): ...\n"
                      "def compute_twice(): return compute() * 2\n"
                      'if __name__ == "__main__":\n    compute_twice()\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stubs = [i for i in sg.scan(store).result if i["kind"] == "live_stub"]
        assert any(i["urgency"] == "red" for i in stubs)   # not demoted by a false AMBIGUOUS


# -- Panel R16A2 / opus (CARDINAL): Rust trait impl emits INHERITS Type->Trait ----
def test_rust_trait_kept_live_via_impl_inherits_edge(tmp_path):
    """A Rust `impl Trait for Type` means Type satisfies Trait. Without an INHERITS edge
    Type->Trait, a private trait whose method is reached (but whose name never appears in a
    reachable body) was flagged dead (panel R16A, cardinal) — the analogue of Ruby
    `include`. The impl block now emits the edge."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "src/lib.rs": """
            trait Validator { fn validate(&self, input: &str) -> bool; }
            struct LengthValidator { min: usize }
            impl Validator for LengthValidator {
                fn validate(&self, input: &str) -> bool { input.len() >= self.min }
            }
            pub fn check(input: &str) -> bool {
                let v = LengthValidator { min: 3 };
                v.validate(input)
            }
            fn never_called() -> bool { false }
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "src/lib.rs::Validator" not in stale       # live: LengthValidator impls it
        assert "src/lib.rs::never_called" in stale          # genuinely unused, still flagged


# -- Panel R16B2 / opus (non-blocking): client HTTP calls aren't phantom routes ---
def test_client_http_call_is_not_a_phantom_express_route(tmp_path):
    """`http.get("/x")` / `axios.post("/x")` are client HTTP calls, not Express server
    route registrations — they must not become phantom ROUTE nodes (panel R16B). The
    express resolver skips known client-library receivers."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "client.js": 'const http = require("http");\nhttp.get("/api/data");\n'
                     'const axios = require("axios");\naxios.post("/users", data);\n',
    })
    from stitchgraph.core.model import NodeKind as _NK
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert store.nodes_by_kind(_NK.ROUTE) == []        # no phantom routes


def test_express_real_route_still_detected(tmp_path):
    """The client-receiver skip must NOT drop a genuine `app.get`/`router.post` route —
    that would orphan its handler (cardinal risk). Real routes still link (panel R16B)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.model import NodeKind as _NK
    _mk(tmp_path, {
        "server.js": 'const app = express();\n'
                     'function handler(req, res) { return res.send("ok"); }\n'
                     'app.get("/health", handler);\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert store.nodes_by_kind(_NK.ROUTE)              # the real route survives


# -- Panel R16B2 / opus (metric): transitive_fan_in excludes self for cyclic nodes -
def test_transitive_fan_in_excludes_self_for_cycles():
    """A node on a cycle reaches itself in the boolean closure; counting that made
    transitive_fan_in report the node as its own depender, inconsistent with
    reverse_reachable_from/impact_of (panel R16B). The diagonal is now dropped."""
    pytest.importorskip("graphblas")
    from stitchgraph.core import algebra
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    with sg.Store(":memory:") as store:
        for nid in ("m.py::a", "m.py::b"):
            store.add_node(Node(id=nid, kind=NodeKind.FUNCTION, name=nid.split("::")[1]))
        for s, d in (("m.py::a", "m.py::b"), ("m.py::b", "m.py::a")):
            store.add_edge(Edge(src=s, relation=Relation.CALLS, dst_symbol=d.split("::")[1],
                                dst_id=d, weight=1.0, provenance=Provenance.EXTRACTED))
        store.commit()
        tfi = algebra.transitive_fan_in(store)
        assert tfi == {"m.py::a": 1, "m.py::b": 1}          # each other, not self


# -- Panel R16B2 / opus (input validation): find_similar negative limit ------------
def test_find_similar_negative_limit_does_not_return_near_all(tmp_path):
    """A negative `limit` sliced from the end (`scored[:-5]`), returning nearly all results
    instead of bounding to none (panel R16B). limit is now clamped to >=0."""
    _mk(tmp_path, {
        "m.py": "\n".join(f"def f{i}(): return store_edge()" for i in range(8))
                + "\ndef store_edge(): return 1\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.find_similar(store, "store edge reachable", limit=-5)
        # clamped: refuses with no payload rather than returning ~all-but-5
        assert res.result in (None, [])
