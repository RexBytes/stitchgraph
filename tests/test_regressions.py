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


def test_renamed_reexport_target_is_an_export_root(tmp_path):
    """`from .core import Engine as PublicEngine` (with/without __all__) makes the DEFINED
    symbol `Engine` public API under an alias. The export-role match is by defined name, so
    the alias-only `exported_names` entry missed `Engine`, flagging the live public class and
    its methods dead at conf 0.6 (panel R25A, cardinal). Register the original name too,
    gated on the bound (public) alias; a privately-bound re-export stays dead."""
    _mk(tmp_path, {
        "mypkg/__init__.py": (
            "from .core import Engine as PublicEngine\n"
            "from .core import secret as _priv\n"
            "__all__ = [\"PublicEngine\"]\n"
        ),
        "mypkg/core.py": (
            "class Engine:\n    def run(self):\n        return 1\n"
            "def secret():\n    return 2\n"
            "def truly_dead():\n    return 3\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Engine" not in stale          # renamed-re-exported public class
        assert "run" not in stale              # its public method
        assert "secret" in stale               # bound privately (as _priv) -> stays dead
        assert "truly_dead" in stale           # never re-exported -> genuinely dead


def test_conditional_reexport_is_an_export_root(tmp_path):
    """A re-export nested in a `try/except ImportError` (optional dep) or `if
    sys.version_info` (backport) is public API, but the __init__ export scan only walked
    TOP-LEVEL statements, so the conditionally re-exported symbol was flagged dead at conf
    0.6 (panel R26A, cardinal). The scan now looks through control-flow blocks. Underscore
    re-exports nested in control flow stay private."""
    _mk(tmp_path, {
        "pkg/__init__.py": (
            "from .impl import PlainThing\n"
            "try:\n    from .impl import OptThing\nexcept ImportError:\n    OptThing = None\n"
            "try:\n    from .impl import _secret\nexcept ImportError:\n    _secret = None\n"
        ),
        "pkg/impl.py": (
            "class PlainThing:\n    def work(self): return 1\n"
            "class OptThing:\n    def work(self): return 2\n"
            "def _secret(): return 3\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "OptThing" not in stale         # conditionally re-exported public API
        assert "work" not in stale              # its method
        assert "_secret" in stale              # underscore re-export stays private


def test_assignment_alias_reexport_is_an_export_root(tmp_path):
    """An alias re-export by assignment (`Public = impl.Thing` / `Public = _Internal`) in a
    package __init__ exposes the RHS symbol as public API, but the export scan only saw
    defs/imports, flagging the aliased class dead (panel R26B). The scan now roots the RHS
    symbol of a public alias assignment; a private-target alias stays dead."""
    _mk(tmp_path, {
        "pkg/__init__.py": (
            "from . import impl\n"
            "Public = impl.Thing\n"          # public alias -> roots Thing
            "_priv = impl.Secret\n"           # private target -> Secret stays dead
        ),
        "pkg/impl.py": (
            "class Thing:\n    def go(self): return 1\n"
            "class Secret:\n    pass\n"
            "class Unused:\n    pass\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Thing" not in stale            # public alias target -> live API
        assert "go" not in stale                # its method
        assert "Secret" in stale               # private-target alias -> stays dead
        assert "Unused" in stale               # never aliased -> genuinely dead


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
        got = [n for n, _ in ts._bash_callback_refs(tree.root_node, src)]
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
        # An ambiguous name-resolved fan-out is name_based (the resolver/`_ref_edges` set it):
        # only such edges revert to holes on a target's deletion (a PRECISE edge is kept
        # dangling and surfaced as a missing-target hole instead — panel R29A).
        return Edge(src="caller.py::call_me", relation=Relation.CALLS, dst_symbol="helper",
                    dst_id=dst, weight=0.5, provenance=Provenance.AMBIGUOUS,
                    location="caller.py:2:0", source="test", name_based=True)
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


# -- Panel R17A / haiku (envelope/crash): None string args must return a Result ----
def test_string_arg_ops_return_result_on_none_not_raise(tmp_path):
    """A None (wrong-type) path/symbol/scope passed to a library op raised TypeError instead
    of returning a Result envelope — the CLI is type-safe but the library/MCP surface can
    hit it, violating "every op returns a Result, never raises" (panel R17A). reindex
    degrades to an empty index; the rest refuse cleanly."""
    _mk(tmp_path, {"m.py": "def f():\n    return 1\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ops = [
            lambda: sg.reindex(store, None),
            lambda: sg.find_symbol(store, None),
            lambda: sg.get_callers(store, None),
            lambda: sg.get_callees(store, None),
            lambda: sg.impact_of(store, None),
            lambda: sg.trace_path(store, None, None),
            lambda: sg.find_similar(store, None),
            lambda: sg.summarize_subsystem(store, None),
            lambda: sg.get_matrix(store, None),
        ]
        for call in ops:
            res = call()                       # must not raise
            assert hasattr(res, "ok")          # a real Result envelope


# -- Panel R17B / sonnet (non-blocking envelope correctness) ----------------------
def test_find_holes_zero_holes_is_green_not_orange():
    """find_holes emitted ORANGE unconditionally; zero dangling references is a clean
    result, so urgency is GREEN when there are no holes (panel R17B). The clean result is
    also confident — needs_review must be False, not True with an empty review_reasons
    (an unexplained review flag, panel R19B)."""
    from stitchgraph.core.envelope import Urgency
    with sg.Store(":memory:") as store:
        res = sg.find_holes(store)
        assert res.result == [] and res.urgency == Urgency.GREEN
        assert res.needs_review is False and res.review_reasons == []


def test_get_callers_reflects_edge_provenance():
    """get_callers/get_callees reported confidence 1.0 / EXTRACTED / needs_review=False
    regardless of edge provenance; a caller list resting on INFERRED/AMBIGUOUS edges must
    reflect that uncertainty (panel R17B)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    with sg.Store(":memory:") as store:
        store.add_node(Node(id="a.py::f", kind=NodeKind.FUNCTION, name="f"))
        store.add_node(Node(id="b.py::g", kind=NodeKind.FUNCTION, name="g"))
        store.add_edge(Edge(src="b.py::g", relation=Relation.CALLS, dst_symbol="f",
                            dst_id="a.py::f", weight=0.3, provenance=Provenance.INFERRED))
        store.commit()
        res = sg.get_callers(store, "a.py::f")
        assert res.provenance is Provenance.INFERRED and res.needs_review
        assert res.confidence < 1.0
        # the EXTRACTED case stays certain
        store.add_node(Node(id="c.py::h", kind=NodeKind.FUNCTION, name="h"))
        store.add_edge(Edge(src="c.py::h", relation=Relation.CALLS, dst_symbol="f",
                            dst_id="a.py::f", weight=1.0, provenance=Provenance.EXTRACTED))
        store.add_node(Node(id="d.py::k", kind=NodeKind.FUNCTION, name="k"))
        store.add_edge(Edge(src="d.py::k", relation=Relation.CALLS, dst_symbol="kk",
                            dst_id="d.py::k2", weight=1.0, provenance=Provenance.EXTRACTED))
        store.add_node(Node(id="d.py::k2", kind=NodeKind.FUNCTION, name="k2"))
        store.commit()
        res2 = sg.get_callers(store, "d.py::k2")
        assert res2.provenance is Provenance.EXTRACTED and not res2.needs_review


# -- Panel R18A / opus (CARDINAL): Go init() is a runtime entry point ------------
def test_go_init_function_and_its_callees_are_live(tmp_path):
    """Go's `init()` is invoked automatically by the runtime at package initialization
    (driver/handler registration) — it and whatever it calls are live, but _roles only
    rooted main/Main, so init+callees were flagged dead (panel R18A, cardinal)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "main.go": "package main\nimport \"fmt\"\nvar cfg string\n"
                   "func main() { fmt.Println(getConfig()) }\n"
                   "func getConfig() string { return cfg }\n"
                   "func init() { cfg = loadDefault() }\n"
                   "func loadDefault() string { return \"default\" }\n"
                   "func trulyDead() string { return \"x\" }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.go::init" not in stale
        assert "main.go::loadDefault" not in stale     # reached from init
        assert "main.go::trulyDead" in stale            # genuinely unused, still flagged


# -- Panel R18A+B (envelope/crash): wrong-type args return a Result, never raise --
def test_ops_return_result_on_wrong_type_args(tmp_path):
    """Beyond None, ops must not raise on list/dict/int/bytes args (sqlite bind errors,
    tokeniser TypeErrors, Path()/relation.upper()/abspath crashes) — reachable via the
    library and MCP surfaces (panels R18A/R18B). reindex degrades to empty; rest refuse."""
    _mk(tmp_path, {"m.py": "def f():\n    return 1\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        import stitchgraph.core.similar as _sim
        _sim._EMBEDDER, _sim._M2V_TRIED = None, True  # force the stdlib token path
        calls = [
            lambda: sg.find_symbol(store, []),
            lambda: sg.find_symbol(store, {}),
            lambda: sg.find_similar(store, 7),
            lambda: sg.find_similar(store, []),
            lambda: sg.find_similar(store, "x", None),
            lambda: sg.ingest_trace(store, None),
            lambda: sg.ingest_trace(store, [1]),
            lambda: sg.get_matrix(store, "m", None),
            lambda: sg.get_matrix(store, "m", "CALLS", "5"),
            lambda: sg.reindex(store, b"abc"),
            lambda: sg.trace_path(store, [1], "x"),
        ]
        for call in calls:
            assert hasattr(call(), "ok")               # a Result, not an exception


# -- Panel R15B/R16A/R18A sonnet (CARDINAL): replace_file re-widens homonyms ------
def test_replace_file_rewidens_resolved_edge_on_new_homonym():
    """Adding a node whose name matches an already-resolved edge's symbol must re-expand
    that edge to the new node (matching a full reindex), or the new node is unreachable and
    find_stale flags it dead (panels R15B/R16A/R18A, cardinal)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.reach import fan_in

    def _n(i):
        return Node(id=i, kind=NodeKind.FUNCTION, name=i.split("::")[1])

    with sg.Store(":memory:") as store:
        store.replace_file("a.py", [_n("a.py::foo")], [])
        store.replace_file("c.py", [_n("c.py::caller")], [
            Edge(src="c.py::caller", relation=Relation.CALLS, dst_symbol="foo",
                 dst_id="a.py::foo", weight=1.0, provenance=Provenance.EXTRACTED,
                 name_based=True)])
        store.replace_file("b.py", [_n("b.py::foo")], [])   # new homonym, added later
        fi = fan_in(store)
        assert fi.get("a.py::foo", 0) >= 1 and fi.get("b.py::foo", 0) >= 1


# -- Panel R19A (cardinal): polymorphic dispatch must keep subclass overrides live --
def test_override_via_base_typed_param_is_not_stale(tmp_path):
    """`def go(b: Base): b.run()` dispatches at runtime to the override on the concrete
    subclass, but the precision path bound the CALLS edge to Base.run only, leaving the
    live Derived.run with no inbound edge -> flagged dead at conf 0.6 (panel R19A,
    cardinal). The override must stay live; only genuinely-unused code is flagged."""
    _mk(tmp_path, {
        "main.py": """
            class Base:
                def run(self): return 0
            class Derived(Base):
                def run(self): return self._extra()
                def _extra(self): return 1
            class Unused(Base):
                def run(self): return 2
            def go(b: Base): return b.run()
            def main(): return go(Derived())
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::Derived.run" not in stale     # the override that runs
        assert "main.py::Derived._extra" not in stale   # reached only via the override


def test_override_via_self_dispatch_is_not_stale(tmp_path):
    """A base method calling `self.step()` dispatches to the subclass override at runtime;
    binding the edge to the enclosing (base) class left the override dead (panel R19A,
    cardinal)."""
    _mk(tmp_path, {
        "main.py": """
            class Base:
                def run(self): return self.step()
                def step(self): return 0
            class Derived(Base):
                def step(self): return 1
            def main():
                d = Derived()
                return d.run()
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::Derived.step" not in stale


def test_override_via_abc_typed_param_is_not_stale(tmp_path):
    """Same defect via an `abc.ABC` + `@abstractmethod` declared type — DI/polymorphism
    through an abstract base is the common idiom (panel R19A, cardinal)."""
    _mk(tmp_path, {
        "main.py": """
            import abc
            class Iface(abc.ABC):
                @abc.abstractmethod
                def handle(self): ...
            class Impl(Iface):
                def handle(self): return self._do()
                def _do(self): return 1
            def run(x: Iface): return x.handle()
            def main(): return run(Impl())
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::Impl.handle" not in stale
        assert "main.py::Impl._do" not in stale


def test_duck_typed_call_keeps_unrelated_same_named_method_live(tmp_path):
    """A call bound to a declared (annotation) type is only a hint: the runtime object may
    be a subclass OR an unrelated duck-typed class with the same method. `go(b: Base)`
    calling `b.run()` must keep EVERY same-named `run` live — the precise INHERITS subtree
    is not enough, because the real object can be outside it (panel R20A, cardinal). The
    over-approximation (an unrelated never-used `run` kept live) is the documented
    cardinal-safe trade-off."""
    _mk(tmp_path, {
        "main.py": """
            class Base:
                def run(self): return 0
            class Derived(Base):
                def run(self): return 1
            class Duck:                       # unrelated, not a Base subclass
                def run(self): return self._go()
                def _go(self): return 99
            def go(b: Base): return b.run()
            def main():
                go(Derived())
                return go(Duck())             # real object is duck-typed, not a Base
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::Derived.run" not in stale      # subclass override, live
        assert "main.py::Duck.run" not in stale         # duck-typed impl, live (cardinal)
        assert "main.py::Duck._go" not in stale         # reached only via Duck.run


def test_structural_protocol_impl_is_not_stale(tmp_path):
    """A class satisfying a typing.Protocol WITHOUT inheriting it (idiomatic structural
    typing) has no INHERITS edge, so override-propagation alone can't keep it live; the
    declared-type call must widen to all same-named methods (panel R20A, cardinal)."""
    _mk(tmp_path, {
        "main.py": """
            import typing
            class Renderer(typing.Protocol):
                def render(self) -> int: ...
            class HtmlRenderer:
                def render(self): return self._impl()
                def _impl(self): return 1
            def show(r: Renderer): return r.render()
            def main(): return show(HtmlRenderer())
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::HtmlRenderer.render" not in stale
        assert "main.py::HtmlRenderer._impl" not in stale


def test_descriptor_get_helper_is_not_stale(tmp_path):
    """A descriptor's __get__ is invoked implicitly by attribute access; a helper it alone
    calls must stay live when the descriptor class is used (panel R20A, cardinal)."""
    _mk(tmp_path, {
        "main.py": """
            class Field:
                def __get__(self, obj, owner): return self._fetch()
                def _fetch(self): return 42
            class Model:
                val = Field()
            def main():
                m = Model()
                return m.val
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::Field._fetch" not in stale


def test_callable_dunder_helper_is_not_stale(tmp_path):
    """An instance call `s(...)` invokes __call__ implicitly; a helper reached only via
    __call__ must stay live when the instance is called (panel R20A, cardinal)."""
    _mk(tmp_path, {
        "main.py": """
            class Strategy:
                def __call__(self, x): return x
            class Double(Strategy):
                def __call__(self, x): return self.calc(x)
                def calc(self, x): return x * 2
            def run(s: Strategy): return s(5)
            def main(): return run(Double())
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::Double.calc" not in stale


def test_property_read_through_declared_type_is_not_stale(tmp_path):
    """A property/attribute READ through a declared type is the read-side twin of the call
    case: the runtime object may be a subclass, structural Protocol impl, or duck-typed
    class. Round-20 widened CALLS but left REFERENCES (reads) narrow, so an overriding
    property and its helpers were confidently flagged dead (panel R21A, cardinal)."""
    _mk(tmp_path, {
        "main.py": """
            import typing
            class HasName(typing.Protocol):
                @property
                def name(self) -> str: ...
            class Dog:                          # structural impl, no inheritance
                @property
                def name(self): return self._dog_name()
                def _dog_name(self): return "rex"
            class Base:
                @property
                def label(self): return "b"
            class Sub(Base):                    # subclass property override
                @property
                def label(self): return self._sub_lbl()
                def _sub_lbl(self): return "s"
            def greet(h: HasName): return h.name
            def show(b: Base): return b.label
            def main(): return greet(Dog()) + show(Sub())
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        for live in ("main.py::Dog.name", "main.py::Dog._dog_name",
                     "main.py::Sub.label", "main.py::Sub._sub_lbl"):
            assert live not in stale


def test_self_property_override_is_not_stale(tmp_path):
    """A base method reading `self.title` (a property) dispatches to a subclass override at
    runtime; _propagate_overrides must widen REFERENCES (not just CALLS) across subclass
    overrides or the override and its helper are flagged dead (panel R21A, cardinal)."""
    _mk(tmp_path, {
        "main.py": """
            class Base:
                def run(self): return self.title
                @property
                def title(self): return "b"
            class Sub(Base):
                @property
                def title(self): return self._mk()
                def _mk(self): return "s"
            def main(): return Sub().run()
            if __name__ == "__main__": print(main())
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert "main.py::Sub.title" not in stale
        assert "main.py::Sub._mk" not in stale


def test_non_utf8_config_does_not_crash(tmp_path):
    """A non-UTF-8 stitchgraph.toml must degrade to defaults, not raise UnicodeDecodeError
    out of every CLI command (panel R20A)."""
    (tmp_path / "m.py").write_text("def foo(): pass\n")
    (tmp_path / "stitchgraph.toml").write_bytes(b"[tool.stitchgraph]\n# bad: \xe9\n")
    with sg.Store(":memory:") as store:
        res = sg.reindex(store, str(tmp_path))
        assert res.ok


def test_out_of_range_int_arg_returns_result_not_overflow(tmp_path):
    """An int beyond SQLite's signed-64-bit range must return a Result, not raise
    OverflowError from the store bind (panel R20B). Reaches find_symbol -> nodes_by_name
    and trace_path -> get_node."""
    _mk(tmp_path, {"m.py": "def foo(): pass\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert sg.find_symbol(store, 2**63).ok is False
        assert sg.trace_path(store, 2**63, "x").ok is False
        assert sg.trace_path(store, "x", 2**63).ok is False


def test_needs_review_always_has_a_reason():
    """The envelope guarantees needs_review => review_reasons non-empty, centrally, so no
    op can emit an unexplained review flag (panels R19B/R20B). A specific reason added
    later supersedes the generic fallback."""
    from stitchgraph.core.envelope import Provenance, Result
    # Low confidence with no explicit reason -> generic fallback present.
    r = Result(ok=True, result=[], confidence=0.5, provenance=Provenance.INFERRED)
    assert r.needs_review and r.review_reasons
    # Ambiguous provenance likewise.
    r2 = Result(ok=True, result=[], confidence=1.0, provenance=Provenance.AMBIGUOUS)
    assert r2.needs_review and r2.review_reasons
    # A specific reason replaces the generic fallback rather than doubling it.
    r3 = Result(ok=True, result=[], confidence=0.5, provenance=Provenance.INFERRED)
    r3.add_reason("specific cause")
    assert r3.review_reasons == ["specific cause"]
    # A confident clean result is not flagged and carries no reasons.
    r4 = Result(ok=True, result=[], confidence=1.0, provenance=Provenance.EXTRACTED)
    assert r4.needs_review is False and r4.review_reasons == []


def test_incremental_replace_does_not_cross_widen_dunder_edges(tmp_path):
    """The seeded class->dunder REFERENCES edge (round 20) is precise/self-scoped, but
    many classes share a dunder name. On an incremental replace_file, _rewiden_resolved
    must not treat it as a name-ambiguous fan-out and cross-link every class's __init__ —
    that inflated fan_in/impact on UNTOUCHED files (panel R21B). Incremental must converge
    to the full-reindex graph."""
    from stitchgraph.core.extract.python import extract_project
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {
        "a.py": "class Foo:\n    def __init__(self): self.x = 1\n",
        "b.py": "class Bar:\n    def __init__(self): self.y = 2\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert fan_in(store).get("b.py::Bar.__init__") == 1
        nodes, edges = extract_project(str(tmp_path))
        an = [n for n in nodes if n.id.startswith("a.py")]
        ae = [e for e in edges if e.src.startswith("a.py")]
        store.replace_file("a.py", an, ae)            # touch a.py only
        # b.py is untouched: its dunder fan_in must stay 1, not gain a phantom from Foo.
        assert fan_in(store).get("b.py::Bar.__init__") == 1


def _incremental_store(root):
    """Build a store by replace_file'ing each file of `root` independently (incremental
    path), filtering one shared full extraction per file — the panel differential harness."""
    from stitchgraph.core.extract.python import extract_project
    nodes, edges = extract_project(str(root))
    files = sorted({n.id.split("::", 1)[0] for n in nodes if "::" in n.id})
    store = sg.Store(":memory:")
    for f in files:
        n = [x for x in nodes if x.id.split("::", 1)[0] == f]
        e = [x for x in edges if x.src.split("::", 1)[0] == f]
        store.replace_file(f, n, e)
    return store


def test_incremental_drop_redundant_holes_respects_name_based(tmp_path):
    """_drop_redundant_holes must not treat an unrelated PRECISE edge (to a different
    class's same-named method) as satisfying a name-based widening hole. Two classes named
    MyClass in different files, the precise target class indexed last: dropping the hole
    left the live target method unreferenced and flagged dead (panel R25A, cardinal)."""
    import itertools

    from stitchgraph.core.extract.python import extract_project
    _mk(tmp_path, {
        "target.py": ("class MyClass:\n    def method(self): return self._helper()\n"
                      "    def _helper(self): return 1\n"),
        "other.py": "class MyClass:\n    def method(self): return 2\n",
        "caller.py": "from target import MyClass\ndef use(m: MyClass): return m.method()\n",
        "main.py": ("from caller import use\nfrom target import MyClass\n"
                    "def main(): return use(MyClass())\n"
                    "if __name__ == '__main__': print(main())\n"),
    })
    nodes, edges = extract_project(str(tmp_path))
    nbf, ebf = {}, {}
    for n in nodes:
        nbf.setdefault(n.id.split("::", 1)[0], []).append(n)
    for e in edges:
        ebf.setdefault(e.src.split("::", 1)[0], []).append(e)
    for order in itertools.permutations(["target.py", "other.py", "caller.py", "main.py"]):
        store = sg.Store(":memory:")
        for f in order:
            store.replace_file(f, nbf.get(f, []), ebf.get(f, []))
        stale = {c["id"] for c in sg.find_stale(store).result}
        store.close()
        assert "target.py::MyClass.method" not in stale, f"order {order}"


def test_incremental_self_dispatch_override_not_stale(tmp_path):
    """A subclass added in a DIFFERENT file via replace_file must have its override of a
    `self`-dispatched base member kept live — the store's _propagate_overrides re-derives
    the cross-file widening a full reindex does, or the override is flagged dead (panel
    R22A, cardinal, incremental path)."""
    _mk(tmp_path, {
        "a.py": """
            class Base:
                def driver(self): return self.op()
                def op(self): return 0
            def run(b): return b.driver()
            def main(): return run(Sub())
            if __name__ == "__main__": print(main())
        """,
        "sub.py": """
            from a import Base
            class Sub(Base):
                def op(self): return self._special()
                def _special(self): return 1
        """,
    })
    store = _incremental_store(tmp_path)
    stale = {c["id"] for c in sg.find_stale(store).result}
    store.close()
    assert "sub.py::Sub.op" not in stale
    assert "sub.py::Sub._special" not in stale


def test_incremental_forward_ref_precise_import_not_misresolved(tmp_path):
    """A precise import (`from right import helper`) to a not-yet-indexed file must NOT be
    nullified and re-resolved by name to an unrelated same-named symbol in another module —
    that inflated the homonym's fan_in and diverged from a full reindex (panel R24A).
    _invalidate_dangling keeps a precise forward-ref edge regardless of file order; only
    genuine deletions (target in the replaced file) and name-based edges revert to holes."""
    import itertools

    from stitchgraph.core.extract.python import extract_project
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {
        "wrong.py": "def helper(): return 99\n",
        "right.py": "def helper(): return 1\n",
        "caller.py": "from right import helper as h\ndef caller(): return h()\n",
    })
    nodes, edges = extract_project(str(tmp_path))
    nbf, ebf = {}, {}
    for n in nodes:
        nbf.setdefault(n.id.split("::", 1)[0], []).append(n)
    for e in edges:
        ebf.setdefault(e.src.split("::", 1)[0], []).append(e)
    full = sg.Store(":memory:")
    sg.reindex(full, str(tmp_path))
    want = fan_in(full).get("wrong.py::helper", 0)
    full.close()
    for order in itertools.permutations(["wrong.py", "right.py", "caller.py"]):
        store = sg.Store(":memory:")
        for f in order:
            store.replace_file(f, nbf.get(f, []), ebf.get(f, []))
        got = fan_in(store).get("wrong.py::helper", 0)
        store.close()
        assert got == want, f"order {order}: fan_in(wrong.helper)={got}, full={want}"


def test_incremental_deletion_reverts_inbound_edge_to_hole():
    """Deleting a node (replacing its file with nothing) must still revert another file's
    edge to it back to a hole — the forward-ref guard must not suppress genuine deletions."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation

    def _n(i):
        return Node(id=i, kind=NodeKind.FUNCTION, name=i.split("::")[1])

    with sg.Store(":memory:") as store:
        store.replace_file("b.py", [_n("b.py::target")], [])
        store.replace_file("a.py", [_n("a.py::caller")], [
            Edge(src="a.py::caller", relation=Relation.CALLS, dst_symbol="target",
                 dst_id="b.py::target", weight=1.0, provenance=Provenance.EXTRACTED)])
        store.replace_file("b.py", [], [])     # delete target
        holes = {(e.src, e.dst_symbol) for e in store.unresolved_edges()}
        assert ("a.py::caller", "target") in holes


def test_incremental_dedup_preserves_name_based_for_rewiden(tmp_path):
    """A declared-type call emits a precise (name_based=0) AND a widening (name_based=1)
    edge to its declared target; _dedup_resolved_edges keeps the precise row and must NOT
    drop the name_based marker, or the group can never re-widen to a homonym whose file is
    added later — flagging it dead (panel R23A, cardinal, incremental path)."""
    _mk(tmp_path, {
        "a.py": "class A:\n    def do(self): return 1\n",
        "b.py": "class B:\n    def do(self): return 2\n",
        "main.py": """
            from a import A
            def run():
                x = A()
                return x.do()
            def main(): return run()
            if __name__ == "__main__": print(main())
        """,
    })
    full = sg.Store(":memory:")
    sg.reindex(full, str(tmp_path))
    full_stale = {c["id"] for c in sg.find_stale(full).result}
    full.close()
    # Incrementally apply main.py BEFORE b.py exists (the failing order).
    inc = _incremental_store_in_order(tmp_path, ["a.py", "main.py", "b.py"])
    inc_stale = {c["id"] for c in sg.find_stale(inc).result}
    inc.close()
    assert "b.py::B.do" not in inc_stale       # the homonym must stay live (cardinal)
    assert inc_stale == full_stale             # incremental converges to full reindex


def _incremental_store_in_order(root, order):
    from stitchgraph.core.extract.python import extract_project
    nodes, edges = extract_project(str(root))
    store = sg.Store(":memory:")
    for f in order:
        n = [x for x in nodes if x.id.split("::", 1)[0] == f]
        e = [x for x in edges if x.src.split("::", 1)[0] == f]
        store.replace_file(f, n, e)
    return store


def test_incremental_precise_import_not_over_widened(tmp_path):
    """A precise `from a import helper` resolution must stay bound to a's helper on an
    incremental replace of an UNRELATED file, never name-widened across a homonym in
    another module (panel R22B, metric inflation)."""
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {
        "a.py": "def helper(): return 1\n",
        "b.py": "def helper(): return 2\n",
        "main.py": "from a import helper\ndef go(): return helper()\n",
        "other.py": "def unrelated(): return 9\n",
    })
    full = sg.Store(":memory:")
    sg.reindex(full, str(tmp_path))
    inc = _incremental_store(tmp_path)
    # b.py's helper is a different module's homonym — main's precise import must not link it.
    assert fan_in(full).get("b.py::helper") == fan_in(inc).get("b.py::helper")
    full.close()
    inc.close()


def test_incremental_replace_does_not_widen_scope_precise_self_call():
    """A scope-precise self-call (`Impl.extra -> Impl.process`, EXTRACTED) must NOT be
    widened to a same-named method of an unrelated class added later via replace_file — a
    full reindex keeps it bound to Impl.process, so widening inflates fan_in/impact of the
    unrelated method (panel R21A). Distinct from the class->member dunder case."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.reach import fan_in

    def _c(i):
        return Node(id=i, kind=NodeKind.CLASS, name=i.split(".")[-1])

    def _m(i):
        return Node(id=i, kind=NodeKind.METHOD, name=i.rsplit(".", 1)[1])

    with sg.Store(":memory:") as store:
        store.replace_file(
            "impl.py",
            [_c("impl.py::Impl"), _m("impl.py::Impl.process"), _m("impl.py::Impl.extra")],
            [Edge(src="impl.py::Impl.extra", relation=Relation.CALLS, dst_symbol="process",
                  dst_id="impl.py::Impl.process", weight=1.0,
                  provenance=Provenance.EXTRACTED)])
        store.replace_file("other.py",          # unrelated class, same method name
                           [_c("other.py::Other"), _m("other.py::Other.process")], [])
        fi = fan_in(store)
        assert fi.get("impl.py::Impl.process") == 1
        assert fi.get("other.py::Other.process", 0) == 0   # no phantom inbound
        rows = store.conn.execute(
            "SELECT provenance, weight FROM edges WHERE src = 'impl.py::Impl.extra' "
            "AND dst_id IS NOT NULL").fetchall()
        assert [(r["provenance"], r["weight"]) for r in rows] == [("extracted", 1.0)]


def test_rewiden_renormalizes_weight_when_fanout_narrows():
    """Deleting one arm of an N-way ambiguous fan-out must re-normalize the survivors'
    weights to match a full reindex (1/N -> 1/(N-1), or 1.0 when one candidate remains),
    or best_path/trace_path confidence stays deflated (panel R19A, non-blocking)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.reach import best_path

    def _n(i):
        return Node(id=i, kind=NodeKind.FUNCTION, name=i.split("::")[1])

    with sg.Store(":memory:") as store:
        store.replace_file("b.py", [_n("b.py::foo")], [])
        store.replace_file("a.py", [_n("a.py::caller")], [
            Edge(src="a.py::caller", relation=Relation.CALLS, dst_symbol="foo",
                 dst_id="b.py::foo", weight=1.0, provenance=Provenance.EXTRACTED,
                 name_based=True)])
        store.replace_file("c.py", [_n("c.py::foo")], [])   # widen -> 0.5 / 0.5
        store.replace_file("c.py", [], [])                  # narrow back to one
        bp = best_path(store, "a.py::caller", "b.py::foo")
        assert bp is not None and bp[1] == 1.0          # confidence restored, not 0.5
        rows = store.conn.execute(
            "SELECT weight FROM edges WHERE dst_symbol = 'foo' AND dst_id IS NOT NULL"
        ).fetchall()
        assert [r["weight"] for r in rows] == [1.0]

    # 3-way fan-out narrowing to 2 re-normalizes 0.333 -> 0.5.
    with sg.Store(":memory:") as store:
        store.replace_file("a.py", [_n("a.py::caller")], [
            Edge(src="a.py::caller", relation=Relation.CALLS, dst_symbol="m",
                 dst_id="x.py::m", weight=1.0, provenance=Provenance.EXTRACTED,
                 name_based=True)])
        for f in ("x.py", "y.py", "z.py"):
            store.replace_file(f, [_n(f"{f}::m")], [])
        store.replace_file("z.py", [], [])              # drop one arm
        rows = store.conn.execute(
            "SELECT weight FROM edges WHERE dst_symbol = 'm' AND dst_id IS NOT NULL"
        ).fetchall()
        assert sorted(r["weight"] for r in rows) == [0.5, 0.5]


def test_old_schema_db_missing_node_columns_does_not_crash(tmp_path):
    """Opening an index built by an older stitchgraph whose `nodes` table lacks newer
    columns (arity/summary/...) must not raise IndexError at read time — _row_to_node now
    tolerates missing optional columns and _migrate backfills them, mirroring the edge-row
    guard (panel R27A)."""
    import sqlite3
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,"
        " location TEXT NOT NULL DEFAULT '', file TEXT NOT NULL DEFAULT '',"
        " is_stub INTEGER NOT NULL DEFAULT 0);"   # no arity/summary/roles/end_line
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL,"
        " relation TEXT NOT NULL, dst_symbol TEXT NOT NULL, dst_id TEXT,"
        " weight REAL NOT NULL DEFAULT 1.0, provenance TEXT NOT NULL DEFAULT 'extracted');"
    )
    conn.execute("INSERT INTO nodes(id, kind, name) VALUES ('m.py::f', 'Function', 'f')")
    conn.commit()
    conn.close()
    with sg.Store(db) as store:                 # _migrate backfills missing columns
        assert sg.orient(store).ok              # reads nodes -> _row_to_node, must not raise
        assert sg.find_stale(store).ok
        assert sg.find_symbol(store, "f").ok


# -- Panel R28A / opus (CARDINAL): __all__ built by += / concat / extend -------
def test_dunder_all_augmented_and_computed_forms_are_exported(tmp_path):
    """A regular (non-__init__) module's public API is declared ONLY via `__all__`.
    `_dunder_all` recognized just `__all__ = [literal]`, so symbols added with the equally
    idiomatic `__all__ += [...]`, `__all__ = [...] + [...]`, and `__all__.extend([...])`
    got no `exported` role and were flagged dead — live public API as dead, the cardinal
    sin (panel R28A)."""
    root = _mk(tmp_path, {
        "pyproject.toml": '[project]\nname = "p"\nversion = "0.1"\n',
        "pkg/__init__.py": "",
        "pkg/aug.py": '''
            __all__ = ["First"]
            __all__ += ["second"]

            class First: ...
            def second(): return 1
            def really_private(): return 2
        ''',
        "pkg/concat.py": '''
            __all__ = ["VisibleA"] + ["VisibleB"]

            class VisibleA: ...
            def VisibleB(): return 1
        ''',
        "pkg/ext.py": '''
            __all__ = ["KeepA"]
            __all__.extend(["KeepB"])
            __all__.append("KeepC")

            class KeepA: ...
            def KeepB(): return 1
            def KeepC(): return 2
        ''',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(root))
        stale = sg.find_stale(store)
        ids = {c["id"] for c in (stale.result or [])}
    # Every __all__-declared name (regardless of build form) must NOT be stale...
    for exported in ("pkg/aug.py::First", "pkg/aug.py::second",
                     "pkg/concat.py::VisibleA", "pkg/concat.py::VisibleB",
                     "pkg/ext.py::KeepA", "pkg/ext.py::KeepB", "pkg/ext.py::KeepC"):
        assert exported not in ids, f"public API flagged dead: {exported}"
    # ...while a genuinely-private symbol stays a true positive.
    assert "pkg/aug.py::really_private" in ids


# -- Panel R28B / opus (BLOCKING): _migrate must backfill ALL schema columns ---
def test_old_schema_db_missing_file_and_location_columns_does_not_crash(tmp_path):
    """An index whose `nodes` predates `file` crashed `_INDEXES` (idx_nodes_file) at
    construction; one whose `edges` predates `location` crashed every `_row_to_edge`.
    `_migrate` now derives its backfill set from `_SCHEMA` itself, so it can never omit a
    column again (panel R28B)."""
    import sqlite3
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        # nodes lacks file/is_stub/arity/summary/roles/end_line
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,"
        " location TEXT NOT NULL DEFAULT '');"
        # edges lacks location/source/file/name_based
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL,"
        " relation TEXT NOT NULL, dst_symbol TEXT NOT NULL, dst_id TEXT,"
        " weight REAL NOT NULL DEFAULT 1.0, provenance TEXT NOT NULL DEFAULT 'extracted');"
    )
    conn.execute("INSERT INTO nodes(id, kind, name) VALUES ('m.py::f', 'Function', 'f')")
    conn.execute("INSERT INTO nodes(id, kind, name) VALUES ('m.py::g', 'Function', 'g')")
    conn.execute("INSERT INTO edges(src, relation, dst_symbol, dst_id) "
                 "VALUES ('m.py::f', 'CALLS', 'g', 'm.py::g')")  # exercises _row_to_edge
    conn.commit()
    conn.close()
    with sg.Store(db) as store:                 # must not raise in __init__ (idx_nodes_file)
        assert sg.orient(store).ok
        assert sg.find_stale(store).ok          # reads edges -> _row_to_edge, must not raise
        assert sg.get_callers(store, "g").ok


# -- Panel R28A / haiku (BLOCKING): coverage JSON depth-bomb must not crash -----
def test_ingest_trace_json_depth_bomb_returns_result(tmp_path):
    """A deeply nested coverage JSON makes `json.loads` exceed the recursion limit;
    RecursionError is not a JSONDecodeError, so it escaped `_parse_json` and crashed
    `ingest_trace`. The 'empty on any problem' contract must hold (panel R28A-haiku)."""
    bomb = tmp_path / "cov.json"
    bomb.write_text('{"a":' * 6000 + "1" + "}" * 6000)
    with sg.Store(":memory:") as store:
        result = sg.ingest_trace(store, str(bomb))   # must return, not raise
    assert result.ok is False


# -- Panel R29A / sonnet (fan_in inflation): delete->re-add must converge -------
def test_incremental_delete_readd_precise_edge_does_not_widen(tmp_path):
    """A precise import to reach.py::func, after reach.py is deleted then re-added, must NOT
    re-widen across a same-named homonym in another file. The old code nullified the precise
    edge on deletion, re-resolved it by bare name to the homonym, marked it name_based, then
    `_rewiden_resolved` widened it to BOTH on re-add — inflating the dead homonym's fan_in and
    masking it from find_stale. `_invalidate_dangling` now keeps a precise edge bound to its
    exact id (dangling until the file returns), so the result equals a full reindex (panel R29A)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    from stitchgraph.core.reach import fan_in

    def _n(i, k=NodeKind.FUNCTION, **kw):
        return Node(id=i, kind=k, name=i.split("::")[1], location=f"{i.split('::')[0]}:1:0", **kw)
    cm = _n("caller.py::caller_module", NodeKind.MODULE)
    cf = _n("caller.py::caller_func", roles=frozenset({"main"}))
    rf = _n("reach.py::func")
    af = _n("algebra.py::func")            # dead homonym, never referenced
    ce = Edge(src="caller.py::caller_module", relation=Relation.IMPORTS, dst_symbol="func",
              dst_id="reach.py::func", weight=1.0, provenance=Provenance.EXTRACTED)

    def setup(s):
        s.replace_file("caller.py", [cm, cf], [ce])
        s.replace_file("reach.py", [rf], [])
        s.replace_file("algebra.py", [af], [])

    with sg.Store(":memory:") as full:
        setup(full)
        want_fi = fan_in(full).get("algebra.py::func", 0)
        want_stale = {c["id"] for c in (sg.find_stale(full).result or [])}
    with sg.Store(":memory:") as inc:
        setup(inc)
        inc.replace_file("reach.py", [], [])        # delete
        inc.replace_file("reach.py", [rf], [])      # re-add same content
        got_fi = fan_in(inc).get("algebra.py::func", 0)
        got_stale = {c["id"] for c in (sg.find_stale(inc).result or [])}
    assert got_fi == want_fi == 0, f"fan_in(algebra.func) inflated: {got_fi} vs {want_fi}"
    assert got_stale == want_stale, f"stale diverged: {got_stale} vs {want_stale}"
    assert "algebra.py::func" in got_stale          # dead homonym still correctly flagged


# -- Panel R29B / opus (corrupt index): bad enum/weight must not crash an op ----
def test_corrupt_index_values_do_not_crash_ops(tmp_path):
    """A corrupt private index — a `kind`/`relation` string no stitchgraph writer emits, or a
    non-finite edge weight (external tampering / on-disk bit-rot) — must not raise out of an
    op. The row mappers skip an un-parseable enum row and clamp a non-finite weight, so ops
    return a Result and JSON never carries `Infinity` (panel R29B)."""
    import json
    import sqlite3
    db = str(tmp_path / "corrupt.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,"
        " location TEXT NOT NULL DEFAULT '', file TEXT NOT NULL DEFAULT '',"
        " is_stub INTEGER NOT NULL DEFAULT 0, arity INTEGER, summary TEXT,"
        " roles TEXT NOT NULL DEFAULT '', end_line INTEGER);"
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL,"
        " relation TEXT NOT NULL, dst_symbol TEXT NOT NULL, dst_id TEXT,"
        " weight REAL NOT NULL DEFAULT 1.0, provenance TEXT NOT NULL DEFAULT 'extracted',"
        " location TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'x',"
        " file TEXT NOT NULL DEFAULT '', name_based INTEGER NOT NULL DEFAULT 0);"
    )
    conn.execute("INSERT INTO nodes(id, kind, name) VALUES ('a.py::z', 'BOGUSKIND', 'z')")
    conn.execute("INSERT INTO nodes(id, kind, name) VALUES ('a.py::y', 'Function', 'y')")
    conn.execute("INSERT INTO edges(src, relation, dst_symbol, dst_id, weight) "
                 "VALUES ('a.py::y', 'BOGUSREL', 'z', 'a.py::z', 1e500)")   # corrupt rel + inf
    conn.execute("INSERT INTO edges(src, relation, dst_symbol, dst_id, weight) "
                 "VALUES ('a.py::y', 'CALLS', 'y', 'a.py::y', 1e500)")      # inf weight
    conn.commit()
    conn.close()
    with sg.Store(db) as store:
        for op in (sg.find_stale, sg.scan, sg.orient, sg.find_holes):
            assert op(store).ok in (True, False)        # returns a Result, never raises
        assert store.get_node("a.py::z") is None         # corrupt-kind row skipped
        assert store.get_node("a.py::y") is not None
        assert "Infinity" not in json.dumps(sg.get_matrix(store, "a.py").to_dict())


# -- Panel R30A / opus (CARDINAL): attribute read on an unknown receiver --------
def test_attribute_read_unknown_receiver_not_flagged_dead(tmp_path):
    """A property/attribute read on a receiver whose type we can't resolve — a constructor
    result `Config().threshold` or, the everyday shape, an unannotated parameter
    `def f(cfg): return cfg.threshold` — got NO edge, so the live property (and its private
    helpers) were flagged dead. The attribute-read pass now has the same name-based fallback
    as the call path (`_call_edge`), so an unknown-receiver read can't flag live code dead
    (panel R30A, cardinal)."""
    root = _mk(tmp_path, {
        "pyproject.toml": '[project]\nname = "p"\nversion = "0.1"\n',
        "pkg/__init__.py": "from .main import main\n__all__ = ['main']\n",
        "pkg/lib.py": '''
            class Config:
                @property
                def threshold(self):
                    return self._compute()
                def _compute(self):
                    return 0.5
        ''',
        "pkg/main.py": '''
            from .lib import Config
            def main():
                cfg = Config()
                def use(c):              # unannotated parameter -> unknown receiver
                    return c.threshold
                return use(cfg)
        ''',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(root))
        ids = {c["id"] for c in (sg.find_stale(store).result or [])}
    assert "pkg/lib.py::Config.threshold" not in ids   # live property
    assert "pkg/lib.py::Config._compute" not in ids     # its private helper


# -- Panel R30B / opus (BLOCKING): non-str id in a corrupt index must not crash -
def test_corrupt_index_blob_node_id_does_not_crash_ops(tmp_path):
    """`all_node_ids()` does a raw projection that bypasses the row mappers, so a BLOB (bytes)
    `nodes.id` (external tampering / bit-rot) leaked to `get_matrix`/`risk`, which do string
    ops on it -> TypeError instead of a Result. A non-str id can't be a real node id; it is
    dropped, so every op returns a Result (panel R30B)."""
    import sqlite3
    db = str(tmp_path / "blob.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE nodes (id, kind TEXT NOT NULL, name TEXT NOT NULL,"
        " location TEXT NOT NULL DEFAULT '', file TEXT NOT NULL DEFAULT '',"
        " is_stub INTEGER NOT NULL DEFAULT 0, arity INTEGER, summary TEXT,"
        " roles TEXT NOT NULL DEFAULT '', end_line INTEGER);"
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL,"
        " relation TEXT NOT NULL, dst_symbol TEXT NOT NULL, dst_id TEXT,"
        " weight REAL NOT NULL DEFAULT 1.0, provenance TEXT NOT NULL DEFAULT 'extracted',"
        " location TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'x',"
        " file TEXT NOT NULL DEFAULT '', name_based INTEGER NOT NULL DEFAULT 0);"
    )
    conn.execute("INSERT INTO nodes(id, kind, name) VALUES (?, 'Function', 'z')", (b"\x00\x01blob",))
    conn.execute("INSERT INTO nodes(id, kind, name) VALUES ('a.py::y', 'Function', 'y')")
    conn.commit()
    conn.close()
    with sg.Store(db) as store:
        assert store.all_node_ids() == ["a.py::y"]       # BLOB id dropped
        assert sg.get_matrix(store, "").ok in (True, False)   # no TypeError
        assert sg.risk(store, str(tmp_path)).ok in (True, False)


# -- Panel R30A / sonnet (non-blocking): synthetic override edge isn't a hole ----
def test_dangling_synthetic_override_edge_is_not_a_hole():
    """A `_propagate_overrides` edge (provenance='ambiguous', name_based=0) left dangling by a
    subclass file's deletion is a derived liveness link, not a source reference, so it must NOT
    be reported as a hole — only genuine missing-target references are (panel R30A)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation

    def _n(i, k=NodeKind.FUNCTION):
        return Node(id=i, kind=k, name=i.split("::")[-1].split(".")[-1],
                    location=f"{i.split('::')[0]}:1:0")
    with sg.Store(":memory:") as store:
        store.replace_file("base.py", [_n("base.py::Base", NodeKind.CLASS)], [])
        # a synthetic override edge (ambiguous + name_based=0) to a symbol in sub.py
        store.replace_file("sub.py", [_n("sub.py::Sub.do", NodeKind.METHOD)], [
            Edge(src="base.py::Base", relation=Relation.REFERENCES, dst_symbol="do",
                 dst_id="sub.py::Sub.do", weight=1.0, provenance=Provenance.AMBIGUOUS,
                 name_based=False)])
        store.replace_file("sub.py", [], [])                      # delete subclass file
        holes = {(e.src, e.dst_symbol) for e in store.unresolved_edges()}
        assert ("base.py::Base", "do") not in holes              # synthetic edge != hole


# == Panel R31 fixes + bounding-matrix invariant tests ========================
# These are "matrix" tests: they enumerate the axes where the late-stage symmetry
# gaps live (scope × expression-kind; every str column; edit-sequence orderings)
# so a not-yet-written cell fails CI instead of waiting for a panel. See
# CONTRIBUTING.md "White-box symmetry closure".

@pytest.mark.parametrize("scope", ["module", "class_body", "function"])
def test_attr_read_unknown_receiver_live_in_every_scope(tmp_path, scope):
    """CARDINAL matrix (panel R30/R31A): an attribute read on an unknown receiver must keep
    the live member (and its private helper) alive in ALL three scope edge-builders —
    `_module_scope_edges`, the `_walk_scope` ClassDef body, and the FunctionDef body. The
    round-30 fix covered only the function body; this cell-per-scope test pins the column."""
    site = {
        "module":     "RESULT = _e.compute\ndef entry():\n    return RESULT\n",
        "class_body": "class Holder:\n    DEFAULT = _e.compute\ndef entry():\n    return Holder\n",
        "function":   "def entry():\n    def use(c):\n        return c.compute\n    return use(_e)\n",
    }[scope]
    root = _mk(tmp_path, {
        "pyproject.toml": '[project]\nname = "p"\nversion = "0.1"\n',
        "pkg/__init__.py": "from .api import entry\n__all__ = ['entry']\n",
        "pkg/api.py": "class Engine:\n    def compute(self):\n        return self._inner()\n"
                      "    def _inner(self):\n        return 1\n"
                      "def make():\n    return Engine()\n_e = make()\n" + site,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(root))
        ids = {c["id"] for c in (sg.find_stale(store).result or [])}
    assert "pkg/api.py::Engine.compute" not in ids, f"{scope}: live member flagged dead"
    assert "pkg/api.py::Engine._inner" not in ids, f"{scope}: live helper flagged dead"


def test_name_based_attr_read_stub_is_not_red(tmp_path):
    """INFLATION (panel R31B): a stub reached only via a name-based attribute READ on an
    unknown receiver must be ORANGE, not RED — `_ref_edges` grants INFERRED on `is_method`
    regardless of relation, so the REFERENCES read is as low-confidence as the CALLS call."""
    root = _mk(tmp_path, {
        "pyproject.toml": '[project]\nname = "c"\nversion = "0.1"\n'
                          '[project.scripts]\nrun-app = "api:entry"\n',
        "api.py": "def entry():\n    helper('x')\n"
                  "def helper(obj):\n    return obj.value\n"
                  "class Service:\n    def value(self):\n        raise NotImplementedError\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(root))
        reds = [i for i in sg.scan(store).result if i.get("urgency") == "red"]
    assert not any("Service.value" in str(i.get("node")) for i in reds)


def test_corrupt_index_blob_in_every_str_column_does_not_crash(tmp_path):
    """CRASH matrix (panel R31B): a BLOB in ANY str-typed column (node id/name/roles/location,
    edge src/dst_id/dst_symbol/location/source, meta.value) must not crash an op — the row
    mappers skip un-parseable rows / coerce optional columns, `get_meta` ignores a non-str
    value. One assertion per column would whack-a-mole; this writes a BLOB into all of them."""
    import sqlite3
    db = str(tmp_path / "blob.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value);"
        "CREATE TABLE nodes (id, kind, name, location, file TEXT NOT NULL DEFAULT '',"
        " is_stub INTEGER NOT NULL DEFAULT 0, arity INTEGER, summary TEXT, roles, end_line INTEGER);"
        "CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, src, relation, dst_symbol,"
        " dst_id, weight REAL NOT NULL DEFAULT 1.0, provenance TEXT NOT NULL DEFAULT 'extracted',"
        " location, source, file TEXT NOT NULL DEFAULT '', name_based INTEGER NOT NULL DEFAULT 0);"
    )
    B = b"\x00\x01"
    conn.execute("INSERT INTO meta VALUES ('root', ?)", (B,))
    # a fully-valid node/edge so ops have something real to chew on...
    conn.execute("INSERT INTO nodes(id,kind,name,location,roles) VALUES ('a.py::f','Function','f','a.py:1:0','')")
    # ...and rows with a BLOB in each str column (each must be skipped/coerced, never crash)
    conn.execute("INSERT INTO nodes(id,kind,name,location,roles) VALUES (?, 'Function','z','a.py:2:0','')", (B,))
    conn.execute("INSERT INTO nodes(id,kind,name,location,roles) VALUES ('a.py::z2','Function',?,'a.py:3:0','')", (B,))
    conn.execute("INSERT INTO nodes(id,kind,name,location,roles) VALUES ('a.py::z3','Function','z3',?,'')", (B,))
    conn.execute("INSERT INTO nodes(id,kind,name,location,roles) VALUES ('a.py::z4','Function','z4','a.py:4:0',?)", (B,))
    conn.execute("INSERT INTO edges(src,relation,dst_symbol,dst_id) VALUES (?, 'CALLS','f','a.py::f')", (B,))
    conn.execute("INSERT INTO edges(src,relation,dst_symbol,dst_id) VALUES ('a.py::f','CALLS',?, 'a.py::f')", (B,))
    conn.execute("INSERT INTO edges(src,relation,dst_symbol,dst_id) VALUES ('a.py::f','CALLS','f', ?)", (B,))
    conn.commit()
    conn.close()
    with sg.Store(db) as store:
        for op in (sg.find_stale, sg.scan, sg.orient, sg.find_holes):
            assert op(store).ok in (True, False)
        assert sg.get_matrix(store, "a.py").ok in (True, False)
        assert sg.summarize_subsystem(store, "a.py").ok in (True, False)
        assert sg.risk(store, str(tmp_path)).ok in (True, False)


def test_incremental_function_move_no_module_fan_in_inflation():
    """INFLATION (panel R31A): moving a function out of a same-named module file
    (`helper.py` with `def helper()`) in a single batch must not leave a CALLS edge pointing
    at the surviving MODULE node `helper.py::helper`. Verified order-independent vs full
    reindex — the differential oracle (incremental == full) is the bounding test for the whole
    incremental pipeline; see CONTRIBUTING.md 'Methods to adopt next'."""
    import itertools
    import tempfile

    from stitchgraph.core.extract.python import extract_project
    from stitchgraph.core.reach import fan_in

    def _by_file(items, key):
        out = {}
        for x in items:
            out.setdefault(key(x).split("::", 1)[0], []).append(x)
        return out

    for order in itertools.permutations(["caller.py", "helper.py", "newplace.py"]):
        root = Path(tempfile.mkdtemp())
        (root / "caller.py").write_text("from helper import helper\n__all__=['run']\ndef run(): helper()\n")
        (root / "helper.py").write_text("def helper(): pass\n")
        (root / "newplace.py").write_text("# empty\n")
        inc = sg.Store(":memory:")
        sg.reindex(inc, str(root))                 # index the pre-move state incrementally
        (root / "caller.py").write_text("from newplace import helper\n__all__=['run']\ndef run(): helper()\n")
        (root / "helper.py").write_text("# empty\n")
        (root / "newplace.py").write_text("def helper(): pass\n")
        nodes, edges = extract_project(str(root))
        nbf = _by_file(nodes, lambda n: n.id)
        ebf = _by_file(edges, lambda e: e.src)
        for f in order:                            # apply the move in the permuted order
            inc.replace_file(f, nbf.get(f, []), ebf.get(f, []))
        full = sg.Store(":memory:")
        sg.reindex(full, str(root))
        assert fan_in(inc).get("helper.py::helper", 0) == fan_in(full).get("helper.py::helper", 0), \
            f"order {order}: module fan_in inflated"
        inc.close()
        full.close()


# == Mutation-testing-found gaps (scripts/mutate.py) ==========================
# The meta-oracle: each test below kills a mutant that SURVIVED the suite — an envelope
# contract that was executed but not pinned. See docs/TESTING.md "mutation testing".
def test_red_urgency_kept_only_for_extracted_provenance():
    """Mutant: the urgency gate `RED and not EXTRACTED` -> `or` survived. A RED result with
    EXTRACTED provenance must STAY red; only non-EXTRACTED demotes to ORANGE."""
    from stitchgraph.core.envelope import Provenance, Result, Urgency
    assert Result(ok=True, confidence=1.0, provenance=Provenance.EXTRACTED,
                  urgency=Urgency.RED).urgency is Urgency.RED
    assert Result(ok=True, confidence=1.0, provenance=Provenance.INFERRED,
                  urgency=Urgency.RED).urgency is Urgency.ORANGE


def test_refuse_always_needs_review_even_high_confidence_extracted():
    """Mutant: `refuse(... needs_review=False ...)` survived. refuse() is refuse-when-unsure;
    it must flag review even if a caller passes high-confidence EXTRACTED args."""
    from stitchgraph.core.envelope import Provenance, refuse
    r = refuse("unsure", confidence=0.95, provenance=Provenance.EXTRACTED)
    assert r.needs_review is True
    assert "unsure" in r.review_reasons


def test_plain_converts_objects_and_preserves_none():
    """Mutants: the `_plain` primitive/None guard flips survived. A dataclass/object must be
    converted (not returned as-is); None must stay None (not stringified)."""
    from stitchgraph.core.envelope import _plain
    from stitchgraph.core.model import Node, NodeKind
    assert _plain(None) is None
    assert _plain(5) == 5 and _plain("x") == "x"
    out = _plain(Node("a.py::b", NodeKind.FUNCTION, "b"))
    assert isinstance(out, dict) and out.get("id") == "a.py::b"


def test_trace_path_returns_complete_node_path():
    """Mutant (scripts/mutate.py on reach.py best_path): the path-reconstruction loop
    `while path[-1] != source` -> `==` truncates the result to just [sink]. trace_path must
    return the COMPLETE source..sink node path. best_path is pure-Python always (no GraphBLAS
    branch), so this is a genuine unit-testable contract — most other reach.py lines are
    oracle-owned (see docs/TESTING.md mutation scope)."""
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    with sg.Store(":memory:") as store:
        for n in ("a", "b", "c"):
            store.add_node(Node(f"m.py::{n}", NodeKind.FUNCTION, n))
        store.add_edge(Edge("m.py::a", Relation.CALLS, "b", dst_id="m.py::b"))
        store.add_edge(Edge("m.py::b", Relation.CALLS, "c", dst_id="m.py::c"))
        store.commit()
        r = sg.trace_path(store, "a", "c")
    assert r.result == ["m.py::a", "m.py::b", "m.py::c"]


# ===========================================================================
# Round 33 (full-diversity panel: opus×2 · sonnet×2 · haiku×2). Five blockers,
# all fixed at root cause; each pinned below as its owning verification layer.
# ===========================================================================


def test_r33_ruby_php_module_level_calls_root_functions(tmp_path):
    """R33A-haiku (CARDINAL): Ruby/PHP execute a file's top-level body on require/load,
    so a module-level call roots the function it invokes. `is_script` excluded them
    (only bash + C# top-level), so top-level-only-used helpers were flagged dead — live
    code as dead. Same class as bash #22 / C# WWW (treesitter.py is_script column)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "svc.rb": "def used_helper\n  42\nend\n\nused_helper\n",
        "svc.php": "<?php\nfunction foo() {\n  return 1;\n}\nfoo();\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    assert not any("used_helper" in s for s in stale), f"Ruby top-level helper flagged dead: {stale}"
    assert not any("svc.php::foo" in s for s in stale), f"PHP top-level fn flagged dead: {stale}"


def test_r33_replace_file_preserves_runtime_role(tmp_path):
    """R33A-sonnet (CARDINAL + INFLATION): ingest_trace sets a `runtime` role (a fact
    about execution); a later replace_file delete+re-insert erased it while `has_runtime`
    meta lingered, so an executed-but-dynamically-dispatched function was flagged dead at
    confidence 0.78 (the confident path). replace_file must carry the runtime role across
    for surviving ids (store.replace_file column)."""
    import json

    from stitchgraph.core.extract import extract_project
    _mk(tmp_path, {
        "main.py": 'def main():\n    return 0\nif __name__ == "__main__":\n    main()\n',
        "lib.py": "def dynamic_callback():\n    return 42\ndef unused():\n    pass\n",
    })
    cov = {"meta": {}, "totals": {}, "files": {
        str(tmp_path / "lib.py"): {"executed_lines": [2], "missing_lines": [4], "excluded_lines": []}}}
    cov_path = tmp_path / "cov.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        sg.ingest_trace(store, str(cov_path))
        nodes, edges = extract_project(str(tmp_path))
        fn = [n for n in nodes if n.id.startswith("lib.py")]
        fe = [e for e in edges if e.src.startswith("lib.py")]
        store.replace_file("lib.py", fn, fe)
        assert {n.id for n in store.nodes_with_role("runtime")} == {"lib.py::dynamic_callback"}
        fs = sg.find_stale(store)
        stale = {x["id"] for x in (fs.result or [])}
    assert "lib.py::dynamic_callback" not in stale, "executed code flagged dead after replace_file"
    assert "lib.py::unused" in stale  # genuinely unused stays flagged


def test_r33_empty_ignore_glob_does_not_crash(tmp_path):
    """R33B-opus (raises-instead-of-Result): `ignore = [""]` reached PurePath.match(""),
    which raises ValueError('empty pattern') — reindex crashed with a raw traceback. The
    glob chokepoints (_ignored / _wanted) must skip empty patterns; the config loader
    drops them at the source too."""
    from stitchgraph.core.extract import extract_project
    _mk(tmp_path, {
        "m.py": "def f():\n    return 1\n",
        "stitchgraph.toml": '[index]\nignore = ["", "*.md", ""]\n',
    })
    nodes, _ = extract_project(str(tmp_path), ignore=[""])  # direct API must not raise
    assert any(n.id == "m.py::f" for n in nodes)
    # And end-to-end through a hand-edited config carrying an empty glob (the panel's
    # path): reindex must return a Result, not crash with a raw traceback.
    with sg.Store(":memory:") as store:
        r = sg.reindex(store, str(tmp_path))
        assert r.ok


def test_r33_config_str_list_drops_empty_entries():
    """R33B-opus (root producer): config `_str_list` must not pass empty strings into the
    ignore list — they crash the glob matcher downstream."""
    from stitchgraph.core.config import load_config
    cfg_dir = Path(__import__("tempfile").mkdtemp())
    (cfg_dir / "stitchgraph.toml").write_text('[index]\nignore = ["", "*.md", ""]\n')
    cfg = load_config(cfg_dir)  # load_config searches the DIR for stitchgraph.toml
    assert "" not in cfg.ignore and "*.md" in cfg.ignore


def test_r33_impact_of_demotes_on_name_based_blast_radius(tmp_path):
    """R33A-opus (INFLATION + envelope contract): impact_of hardcoded confidence=0.9 /
    provenance=extracted / needs_review=false regardless of the edges backing the blast
    radius, while sibling ops (get_callers, trace_path) demote name-based evidence. A
    blast radius reached only through AMBIGUOUS homonym binds must be advisory, not
    type-certain fact (operations.py provenance-demotion column)."""
    _mk(tmp_path, {
        "cli.py": 'def main():\n    return 0\nif __name__ == "__main__":\n    main()\n',
        "mcp.py": 'def main():\n    return 1\nif __name__ == "__main__":\n    main()\n',
        "report.py": 'def main():\n    return 2\nif __name__ == "__main__":\n    main()\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.impact_of(store, "mcp.py::main")
    assert r.needs_review is True
    assert r.provenance.value in ("ambiguous", "inferred")
    assert r.confidence < 0.9


def test_r33_scope_matching_respects_id_boundaries(tmp_path):
    """R33B-sonnet (INFLATION): get_matrix / summarize_subsystem used bare startswith,
    so scope `Foo` swept in sibling class `FooBar` and file scope `...::Node` pulled in
    `NodeKind` — inflating cells/density/counts with unrelated nodes. The char after the
    scope must be a real id separator (operations._under_scope column)."""
    from stitchgraph.core.operations import _under_scope
    assert _under_scope("m.py::Foo", "m.py::Foo")
    assert _under_scope("m.py::Foo.run", "m.py::Foo")
    assert not _under_scope("m.py::FooBar", "m.py::Foo")
    assert not _under_scope("m.py::NodeKind", "m.py::Node")
    assert _under_scope("pkg/a.py::X", "pkg")
    assert not _under_scope("pkgutil/a.py::X", "pkg")
    # End-to-end: a class scope must not absorb a prefix-sharing sibling class.
    _mk(tmp_path, {"model.py": (
        "class Foo:\n"
        "    def run(self):\n        self.helper()\n"
        "    def helper(self):\n        pass\n\n"
        "class FooBar:\n"
        "    def execute(self):\n        f = Foo()\n        f.run()\n"
    )})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        m = sg.get_matrix(store, "model.py::Foo", relation="CALLS")
    assert m.result["n"] == 3, f"scope bled into FooBar: labels={m.result['labels']}"
    assert all("FooBar" not in lbl for lbl in m.result["labels"])


# ===========================================================================
# Round 34 (full-diversity panel: opus×2 · sonnet×2 · haiku×2). Three product
# blockers + one consistency item; each fixed at root cause and pinned below.
# ===========================================================================


def test_r34_trace_path_demotes_name_based_path():
    """R34B-opus (INFLATION + envelope contract): trace_path derived provenance from the
    propagated confidence (conf>=0.99 => extracted), so a name-based AMBIGUOUS edge with
    weight 1.0 was reported as a type-certain extracted path with needs_review=False. It
    must demote on the real edges along the path, like impact_of/get_callers (the
    provenance-demotion column)."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    with sg.Store(":memory:") as store:
        for n in ("a", "b"):
            store.add_node(Node(f"m.py::{n}", NodeKind.FUNCTION, n))
        store.add_edge(Edge("m.py::a", Relation.CALLS, "b", dst_id="m.py::b",
                            weight=1.0, provenance=Provenance.AMBIGUOUS))
        store.commit()
        r = sg.trace_path(store, "m.py::a", "m.py::b")
    assert r.ok and r.confidence == 1.0          # confidence still reflects the weight
    assert r.provenance is Provenance.AMBIGUOUS   # but provenance reflects the edge
    assert r.needs_review is True and r.review_reasons


def test_r34_trace_path_extracted_path_stays_certain():
    """Companion to the above: an all-EXTRACTED path must NOT be demoted (no false
    needs_review), so the demotion only fires on genuine name-based evidence."""
    from stitchgraph.core.envelope import Provenance
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation
    with sg.Store(":memory:") as store:
        for n in ("a", "b"):
            store.add_node(Node(f"m.py::{n}", NodeKind.FUNCTION, n))
        store.add_edge(Edge("m.py::a", Relation.CALLS, "b", dst_id="m.py::b",
                            weight=1.0, provenance=Provenance.EXTRACTED))
        store.commit()
        r = sg.trace_path(store, "m.py::a", "m.py::b")
    assert r.provenance is Provenance.EXTRACTED


def test_r34_runtime_suffix_match_requires_separator_boundary():
    """R34A-sonnet (INFLATION): _by_suffix had a bare endswith, so coverage for `b/a.py`
    marked the unrelated top-level `a.py` runtime, inflating executed_nodes. A bare-filename
    rel must not suffix-match a deeper path; only a rel with a directory component does."""
    from stitchgraph.core.runtime import _by_suffix
    assert _by_suffix({"/proj/b/a.py": {2}}, "a.py") is None      # bare rel: no false steal
    assert _by_suffix({"/proj/a.py": {2}}, "a.py") is None        # bare rel relies on exact match
    assert _by_suffix({"/abs/pkg/m.py": {2}}, "pkg/m.py") == {2}  # dir component: legit suffix


def test_r34_runtime_suffix_inflation_end_to_end(tmp_path):
    """R34A-sonnet end-to-end: coverage covering only `b/a.py` must not mark `a.py::alive`
    runtime. executed_nodes must equal the truly-executed count."""
    import json
    _mk(tmp_path, {
        "a.py": "def alive():\n    return 1\ndef dead():\n    return 2\n",
        "b/a.py": "def other_func():\n    return 3\n",
    })
    cov = {"meta": {}, "totals": {}, "files": {
        str(tmp_path / "b" / "a.py"): {"executed_lines": [2], "missing_lines": [], "excluded_lines": []}}}
    cov_path = tmp_path / "cov.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.ingest_trace(store, str(cov_path))
        runtime_ids = {n.id for n in store.nodes_with_role("runtime")}
    assert runtime_ids == {"b/a.py::other_func"}, f"inflated runtime set: {runtime_ids}"
    assert r.meta.get("executed") == 1


def test_r34_incremental_resolution_is_per_language(tmp_path):
    """R34A-opus (INFLATION): full reindex buckets name resolution per language (by_lang),
    but the incremental store path was language-blind — a Rust `helper()` bound to a Go
    `helper`, inflating fan_in/get_callers vs a full reindex. Name resolution must stay
    within the same language family."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.extract import extract_project
    from stitchgraph.core.reach import fan_in
    _mk(tmp_path, {
        "lib.rs": "pub fn run() {\n    helper();\n}\nfn helper() {}\n",
        "other.go": "package main\nfunc helper() {}\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        nodes, edges = extract_project(str(tmp_path))
        fn = [n for n in nodes if n.id.startswith("lib.rs")]
        fe = [e for e in edges if e.src.startswith("lib.rs")]
        store.replace_file("lib.rs", fn, fe)
        fi = fan_in(store)
        callers = {c["src"] for c in (sg.get_callers(store, "other.go::helper").result or [])}
    assert fi.get("other.go::helper", 0) == 0, "Rust call bled into Go helper fan_in"
    assert not callers, f"phantom cross-language caller: {callers}"


def test_r34_same_lang_is_recall_safe_for_unknown_ids():
    """The language filter must be recall-safe: an unknown extension / pseudo node (db::,
    var::) must NOT be filtered out (returning 0 could drop a valid bind and flag live code
    dead — the cardinal sin). C and C++ share one bucket (panel UUU)."""
    from stitchgraph.core.store import _same_lang
    assert _same_lang("a.rs::f", "b.rs::g") == 1       # same language
    assert _same_lang("a.rs::f", "b.go::g") == 0       # different language
    assert _same_lang("a.c::f", "b.cpp::g") == 1       # C/C++ share a bucket
    assert _same_lang("db::table", "a.rs::f") == 1     # pseudo node: don't filter
    assert _same_lang("a.unknownext::f", "b.go::g") == 1  # unknown ext: don't filter


def test_r34_scan_inner_items_have_review_reasons(tmp_path):
    """R34B-sonnet (consistency): scan cycle/god_object inner items set needs_review but
    only carried `reason` (singular). Mirror the envelope contract on inner items — a
    needs_review item must carry a non-empty review_reasons list."""
    src = ["def hub():\n    pass\n"]
    src += [f"def caller{i}():\n    hub()\n" for i in range(6)]
    src += [f"def callee{i}():\n    pass\n" for i in range(6)]
    _mk(tmp_path, {"m.py": "\n".join(src)})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        items = sg.scan(store).result
    offenders = [it for it in items if it.get("needs_review") and not it.get("review_reasons")]
    assert not offenders, f"needs_review item without review_reasons: {offenders}"


# ===========================================================================
# Round 35 (full-diversity panel: opus×2 · sonnet×2 · haiku×2). Two cardinals +
# two inflations; each fixed at root cause and pinned below.
# ===========================================================================


def test_r35_go_package_var_initializer_is_live(tmp_path):
    """R35A-opus (CARDINAL): Go package files share scope — a package-level `var x = setup()`
    initializer runs at startup for every file once the package loads. A rootless package
    file (no main/exported) was never seeded, so its functions were flagged dead though they
    run at startup. Module-node seeding must widen to the whole package directory (panel R35A)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "main.go": 'package main\nfunc main() {\n    println("hi")\n}\n',
        "reg.go": ("package main\nvar _registered = setup()\n"
                   "func setup() int {\n    return configure()\n}\n"
                   "func configure() int {\n    return 1\n}\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    assert not any("setup" in s or "configure" in s for s in stale), \
        f"live Go package-init code flagged dead: {stale}"


def test_r35_express_method_reference_handler_is_live(tmp_path):
    """R35B-haiku (CARDINAL): an Express route handler given as a method reference
    (`ctrl.handleRequest`, a member_expression) got no ROUTES_TO edge — only bare-identifier
    handlers were extracted — so the live handler method was flagged dead (panel R35B)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.js": ("class Controller {\n  handleRequest(req, res) {}\n}\n"
                   "const ctrl = new Controller();\n"
                   "app.get('/api/test', ctrl.handleRequest);\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    assert not any("handleRequest" in s for s in stale), \
        f"live Express method handler flagged dead: {stale}"


def test_r35_coverage_bool_line_is_not_int(tmp_path):
    """R35B-sonnet (INFLATION): bool is an int subclass, so a JSON `true` in executed_lines
    coerced to line 1, spuriously marking a one-liner at line 1 runtime — inflating
    executed_nodes and find_stale confidence. bool must be excluded (panel R35B)."""
    import json
    _mk(tmp_path, {"mod.py": "def oneliner(): return 1\n"})
    cov = {"meta": {}, "totals": {}, "files": {
        str(tmp_path / "mod.py"): {"executed_lines": [True, False], "missing_lines": [], "excluded_lines": []}}}
    cov_path = tmp_path / "cov.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.ingest_trace(store, str(cov_path))
        runtime = {n.id for n in store.nodes_with_role("runtime")}
    assert not runtime, f"bool coerced to a phantom executed line: {runtime}"
    assert r.meta.get("executed") == 0


def test_r35_coverage_exact_match_blocks_cross_file_suffix(tmp_path):
    """R35A-sonnet (INFLATION): when the coverage root aligns (an exact root-relative match
    exists), a non-matching node must NOT suffix-fall-back and steal another file's coverage
    — coverage for `b/a.py` must not also mark the top-level `a.py` runtime (panel R35A)."""
    import json
    _mk(tmp_path, {
        "a.py": "def alive():\n    return 1\n",
        "b/a.py": "def other():\n    return 3\n",
    })
    cov = {"meta": {}, "totals": {}, "files": {
        str(tmp_path / "b" / "a.py"): {"executed_lines": [2], "missing_lines": [], "excluded_lines": []}}}
    cov_path = tmp_path / "cov.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        sg.ingest_trace(store, str(cov_path))
        runtime = {n.id for n in store.nodes_with_role("runtime")}
    assert runtime == {"b/a.py::other"}, f"cross-file suffix steal: {runtime}"


# ===========================================================================
# Round 36 (full-diversity panel: opus×2 · sonnet×2 · haiku×2). One C++ cardinal
# + envelope hardening; four reviewers clean, one finding invalid (reverted).
# ===========================================================================


def test_r36_cpp_static_initializer_chain_is_live(tmp_path):
    """R36A-opus (CARDINAL): a C++ translation unit's namespace-scope static initializer
    (`static int g = seed();`, or a self-registering global object) runs at startup once the
    TU is LINKED — i.e. once any of its symbols is reached. The module node wasn't promoted,
    so the initializer's call chain was flagged dead though it runs on link (panel R36A)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "main.cpp": "int count();\nint main() {\n    return count();\n}\n",
        "registry.cpp": ("int doRegister();\nstatic int g = doRegister();\n"
                         "int add() {\n    return 1;\n}\n"
                         "int doRegister() {\n    return add();\n}\n"
                         "int count() {\n    return 2;\n}\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    assert not any("add" in s or "doRegister" in s for s in stale), \
        f"live C++ static-init chain flagged dead: {stale}"


def test_r36_cpp_dead_tu_still_flagged(tmp_path):
    """Precision companion: the TU-liveness fixpoint must only promote a LINKED TU (one with
    a reachable symbol). A C++ file with no reached symbol must still surface its dead code,
    so the fix doesn't blanket-suppress (panel R36A)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "main.cpp": "int main() {\n    return 0;\n}\n",
        "dead.cpp": "int helper() {\n    return 1;\n}\nint orphan() {\n    return helper();\n}\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    assert any("orphan" in s for s in stale), "dead C++ TU should still surface dead code"


def test_r36_envelope_clamps_nonfinite_confidence():
    """R36B-sonnet (envelope contract): a non-finite / out-of-range confidence must be clamped
    to a finite [0,1] value at the envelope chokepoint so to_dict() never emits Infinity/NaN
    (invalid JSON per RFC 8259), and needs_review is set (panel R36B)."""
    import json

    from stitchgraph.core.envelope import Provenance, Result
    for bad, expect in [(float("inf"), 0.0), (float("nan"), 0.0), (-5.0, 0.0), (2.0, 1.0)]:
        r = Result(ok=True, result="x", confidence=bad, provenance=Provenance.EXTRACTED)
        assert r.confidence == expect
        assert r.needs_review is True
        dumped = json.dumps(r.to_dict())  # must be valid JSON
        assert "Infinity" not in dumped and "NaN" not in dumped


# ===========================================================================
# Round 37 (full-diversity panel: opus×2 · sonnet×2 · haiku×2). Two cardinals +
# defense-in-depth; one finding documented as a known limitation (find_holes on
# incremental delete), one non-blocking (Express middleware), one invalid earlier.
# ===========================================================================


def test_r37_module_symbol_collision_keeps_module_load_live_python(tmp_path):
    """R37A-opus (CARDINAL): when a top-level class/function shares the file stem, the MODULE
    node id `Service.py::Service` is clobbered into the symbol node, so module-load-root
    seeding (via nodes_by_kind(MODULE)) misses it and a live module-level call is flagged
    dead. The detector now seeds the module-load id computed from the file path (panel R37A)."""
    _mk(tmp_path, {
        "Service.py": ('__all__ = ["do_it"]\n'
                       "class Service:\n    def run(self):\n        return 1\n"
                       "_inst = prep()\n"
                       "def prep():\n    return Service()\n"
                       "def do_it():\n    return 42\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    # prep() runs at module load; Service is instantiated by it — both live.
    assert "Service.py::prep" not in stale
    assert "Service.py::Service" not in stale
    # the genuinely-unused method is still flagged (precision preserved)
    assert "Service.py::Service.run" in stale


def test_r37_module_symbol_collision_keeps_module_load_live_js(tmp_path):
    """R37A-opus (CARDINAL), tree-sitter sibling of the above."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Service.js": ("class Service { run() { return 1; } }\n"
                       "const _inst = prep();\n"
                       "function prep() { return new Service(); }\n"
                       "export function doIt() { return 42; }\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    assert not any(s in stale for s in ("Service.js::prep", "Service.js::Service"))


def test_r37_incremental_init_export_change_converges(tmp_path):
    """R37A-sonnet F1 (CARDINAL): incrementally editing __init__.py to re-export a symbol
    defined in another file (adding it to __all__) must not leave that symbol flagged dead.
    replace_file is a single-file update and can't see another file's export change, so the
    incremental caller passes `exported_ids` from the whole-project extract; replace_file then
    re-applies the cross-file `exported` role exactly, converging with a full reindex (panel
    R37A)."""
    from stitchgraph.core.extract import extract_project
    _mk(tmp_path, {
        "app/__init__.py": '__all__ = ["main"]\nfrom .main import main\n',
        "app/main.py": "def main():\n    return 1\n",
        "app/other.py": "def public_fn():\n    return 42\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert "app/other.py::public_fn" in {x["id"] for x in (sg.find_stale(store).result or [])}
        (tmp_path / "app" / "__init__.py").write_text(
            '__all__ = ["main", "public_fn"]\nfrom .main import main\nfrom .other import public_fn\n')
        nodes, edges = extract_project(str(tmp_path))
        exported_ids = {n.id for n in nodes if "exported" in n.roles}
        fn = [n for n in nodes if n.id.startswith("app/__init__.py")]
        fe = [e for e in edges if e.src.startswith("app/__init__.py")]
        store.replace_file("app/__init__.py", fn, fe, exported_ids=exported_ids)
        inc_stale = {x["id"] for x in (sg.find_stale(store).result or [])}
    with sg.Store(":memory:") as full:
        sg.reindex(full, str(tmp_path))
        full_stale = {x["id"] for x in (sg.find_stale(full).result or [])}
    assert "app/other.py::public_fn" not in inc_stale, "re-exported symbol flagged dead incrementally"
    assert inc_stale == full_stale, f"incremental diverged from full: {inc_stale ^ full_stale}"


def test_r37_plain_drops_nonfinite_floats():
    """R37B-sonnet (defense-in-depth): _plain is the serialization chokepoint for every
    result/meta value; a stray non-finite float must become None, never Infinity/NaN (invalid
    JSON, RFC 8259) (panel R37B)."""
    import json

    from stitchgraph.core.envelope import _plain
    assert _plain(float("inf")) is None
    assert _plain(float("nan")) is None
    assert _plain(1.5) == 1.5
    assert _plain(True) is True and _plain(3) == 3
    out = _plain({"a": float("inf"), "b": [float("nan"), 2.0], "c": "x"})
    assert json.dumps(out) and "Infinity" not in json.dumps(out) and "NaN" not in json.dumps(out)


# ===========================================================================
# Round 38 (full-diversity panel: opus×2 · sonnet×2 · haiku×2). FIRST CLEAN
# PANEL ROUND — zero valid blockers. One latent consistency item closed below;
# one finding invalid (SUBMITS_TO premise/direction), rest clean.
# ===========================================================================


def test_r38_to_dict_meta_is_sanitized():
    """R38 (latent envelope consistency): to_dict() routed `result` through _plain but passed
    `meta` raw, so a non-finite float in meta would serialize to Infinity/NaN (invalid JSON).
    No current op puts a float in meta, but the envelope must honour _plain as the chokepoint
    for ALL values, so meta is now sanitized too."""
    import json

    from stitchgraph.core.envelope import Provenance, Result
    r = Result(ok=True, result="x", confidence=1.0, provenance=Provenance.EXTRACTED,
               meta={"inf": float("inf"), "nan": float("nan"), "n": 3, "s": "ok"})
    d = r.to_dict()
    assert d["meta"]["inf"] is None and d["meta"]["nan"] is None
    assert d["meta"]["n"] == 3 and d["meta"]["s"] == "ok"
    dumped = json.dumps(d)
    assert "Infinity" not in dumped and "NaN" not in dumped


# ===========================================================================
# Post-1.0.6 multi-repo false-positive hunt (corpora: flake8 / isort / flask /
# cookiecutter / datasette). The hunt surfaced ONE genuine cardinal-class gap:
# entry points declared in setup.cfg `[options.entry_points]` (the older but still
# ubiquitous packaging format — flake8 et al.) were not read, only pyproject.toml's
# `[project.*]`. Live plugin code (functions AND classes) registered there with no
# internal caller was flagged dead. Reproduced minimally below.
# ===========================================================================


def test_src_layout_namespace_package_absolute_import_resolves(tmp_path):
    """R42A (cardinal): a PEP 420 namespace package (no `__init__.py`) under `src/` must still
    be recognized as a src-layout source root. Otherwise its absolute imports
    (`from nspkg.handlers import Handler`) stay 'external', the module-load side effect is
    dropped, and a class instantiated only in a module-level registry is flagged dead."""
    _mk(tmp_path, {
        # NOTE: deliberately NO src/nspkg/__init__.py — namespace package is the trigger
        "pyproject.toml": '[project]\nname="nspkg"\n[project.scripts]\nnscli = "nspkg.cli:main"\n',
        "src/nspkg/cli.py": "from nspkg import registry\ndef main():\n    return registry.run_all()\n",
        "src/nspkg/registry.py": (
            "from nspkg.handlers import Handler\n"
            "TABLE = {'h': Handler()}\n"
            "def run_all():\n    return TABLE\n"
        ),
        "src/nspkg/handlers.py": "class Handler:\n    def __init__(self):\n        self._x = 1\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        # Handler is instantiated at registry import, reached from the console-script entry: live
        assert not any(s.endswith("handlers.py::Handler") for s in stale)


def test_setup_cfg_console_script_target_is_live(tmp_path):
    """A `console_scripts` entry in setup.cfg roots its target exactly like a
    pyproject `[project.scripts]` one — else a CLI's `main` is false-flagged dead."""
    _mk(tmp_path, {
        "setup.cfg": """
            [options.entry_points]
            console_scripts =
                mytool = mypkg.cli:main
        """,
        "mypkg/__init__.py": "",
        "mypkg/cli.py": "def main():\n    return 0\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "main" not in stale


def test_setup_cfg_plugin_group_function_target_is_live(tmp_path):
    """A function registered only through a plugin-group entry point in setup.cfg
    (`flake8.extension = E = flake8.plugins.pycodestyle:pycodestyle_logical`) is loaded
    exclusively by the framework's entry-point machinery and has no internal caller, yet is
    definitively live. Was a cardinal-class false positive before setup.cfg was parsed."""
    _mk(tmp_path, {
        "setup.cfg": """
            [options.entry_points]
            myframework.plugins =
                foo = mypkg.plugins:do_foo
        """,
        "mypkg/__init__.py": "",
        "mypkg/plugins.py": (
            "def do_foo(x):\n    return x + 1\n\n"
            "def truly_dead():\n    return 99\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "do_foo" not in stale            # entry-point plugin: live
        assert "truly_dead" in stale            # genuinely dead: still flagged (no over-root)


def test_setup_cfg_plugin_group_class_target_and_methods_are_live(tmp_path):
    """A *class* registered as a plugin (`flake8.report = pylint = ...:Pylint`) is
    instantiated and driven by the framework, so the class AND its public protocol methods
    are live API with no internal caller. The class target must root its public methods just
    like an exported class does — underscore methods stay private."""
    _mk(tmp_path, {
        "setup.cfg": """
            [options.entry_points]
            myframework.reporters =
                bar = mypkg.plugins:DoBar
        """,
        "mypkg/__init__.py": "",
        "mypkg/plugins.py": (
            "class DoBar:\n"
            "    def run(self):\n        return self._impl()\n\n"
            "    def _impl(self):\n        return 2\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "DoBar" not in stale             # plugin class: live
        assert "DoBar.run" not in stale         # public protocol method: live API


def test_setup_cfg_entry_point_does_not_root_same_named_symbol_elsewhere(tmp_path):
    """The entry-point match is module-path-precise: a same-named function in an unrelated
    module is NOT mis-rooted (precision over recall — the fix only adds the true target)."""
    _mk(tmp_path, {
        "setup.cfg": """
            [options.entry_points]
            console_scripts =
                mytool = mypkg.cli:run
        """,
        "mypkg/__init__.py": "",
        "mypkg/cli.py": "def run():\n    return 0\n",
        "mypkg/other.py": "def run():\n    return 1\n",  # same name, different module
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in sg.find_stale(store).result}
        assert not any(s.endswith("mypkg/cli.py::run") for s in stale)   # the target: live
        assert any(s.endswith("mypkg/other.py::run") for s in stale)     # unrelated: still dead


def test_inherited_public_method_of_exported_class_is_live(tmp_path):
    """A public method an exported class *inherits* from a (non-exported) base is part of
    its public surface — callable on an instance of the exported class — so it is live API
    with no internal caller. `Flask(App)`, `App(Scaffold)`: `.shell_context_processor` /
    `.patch` live on the base yet are public API. Was a cardinal-class FP (flask corpus)."""
    _mk(tmp_path, {
        "lib/__init__.py": '__all__ = ["App"]\nfrom .app import App\n',
        "lib/base.py": (
            "class Scaffold:\n"
            "    def patch(self, rule):\n        return rule\n\n"
            "    def _private_helper(self):\n        return 1\n"
        ),
        "lib/app.py": (
            "from .base import Scaffold\n\n"
            "class App(Scaffold):\n"
            "    def run(self):\n        return 0\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "Scaffold.patch" not in stale          # inherited public API: live
        assert "Scaffold._private_helper" in stale     # private, genuinely dead: still flagged


def test_transitive_external_base_methods_are_callback_roots(tmp_path):
    """A class that subclasses a first-party class which itself has an EXTERNAL framework
    base inherits that base transitively — its method overrides are framework-invoked too.
    `FlaskGroup(AppGroup)`, `AppGroup(click.Group)`: FlaskGroup.get_command overrides a click
    method with no internal caller. The 'callback' role must propagate down INHERITS (C1)."""
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/cli.py": (
            "import click\n\n"
            "class AppGroup(click.Group):\n"
            "    def make_context(self, *a):\n        return None\n\n"
            "class FlaskGroup(AppGroup):\n"
            "    def get_command(self, ctx, name):\n        return None\n\n"
            "    def list_commands(self, ctx):\n        return []\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "FlaskGroup.get_command" not in stale    # transitive framework override: live
        assert "FlaskGroup.list_commands" not in stale
        assert "AppGroup.make_context" not in stale      # direct framework override: live


def test_self_named_external_base_is_a_callback_class(tmp_path):
    """`class EnvironBuilder(werkzeug.test.EnvironBuilder)` — a subclass whose external base
    shares its own leaf name. The INHERITS edge resolves the base to the subclass itself (a
    self-loop), so the inline name heuristic misses it and its overrides were flagged dead.
    A self-loop INHERITS means the real base is external -> a framework callback class (C2)."""
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/testing.py": (
            "import werkzeug.test\n\n"
            "class EnvironBuilder(werkzeug.test.EnvironBuilder):\n"
            "    def json_dumps(self, obj, **kw):\n        return '{}'\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "EnvironBuilder.json_dumps" not in stale  # override of external same-name base
        assert "EnvironBuilder" not in stale             # class itself: live (has callbacks)


def test_plain_stdlib_exception_subclass_is_not_over_rooted(tmp_path):
    """Precision guard for the C1/C2 widening: subclassing a stdlib *plain* base
    (`Exception`, `Enum`, ...) must NOT turn the subclass into a framework-callback class —
    its genuinely-dead methods must still be flagged. Only non-plain external bases count."""
    _mk(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/errs.py": (
            "class MyError(Exception):\n"
            "    def never_called(self):\n        return 1\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "MyError.never_called" in stale   # plain-base subclass: dead method still flagged


def test_src_layout_absolute_import_module_load_side_effect_is_live(tmp_path):
    """src-layout (`src/pkg/...`): a module reached only via an ABSOLUTE first-party import
    (`from pkg.sub import mod`) must still fire its module-load side effects, so module-level
    code is live. Before src-root detection, the path-qualname `src.pkg.*` never matched the
    import `pkg.*`, the import was dropped as external, and `_enable` was flagged dead
    (the flake8 `_windows_color` cardinal FP)."""
    _mk(tmp_path, {
        "src/app/__init__.py": '__all__ = ["Default"]\nfrom app.default import Default\n',
        "src/app/default.py": (
            "from app import base\n\n"
            "class Default(base.BaseFormatter):\n"
            "    def after_init(self):\n        return 1\n"
        ),
        "src/app/base.py": (
            "from app import _color\n\n"
            "class BaseFormatter:\n"
            "    def color(self):\n        return _color.supported\n"
        ),
        "src/app/_color.py": "def _enable():\n    return 1\n\nsupported = _enable()\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "_enable" not in stale   # module-level call in a transitively-reached module


# ===========================================================================
# Multi-language false-positive hunt (real repos: sinatra/gson/carbon/...). The
# runtime/framework invokes certain methods IMPLICITLY (never by name), so the
# name-based call graph can't see the use and flagged them dead — the cross-language
# analogue of skipping Python dunders. Rooted as `callback`.
# ===========================================================================


def test_ruby_implicit_hooks_are_live(tmp_path):
    """Ruby's class/module lifecycle hooks (`inherited`, `included`, `extended`) and
    `method_missing` are interpreter-invoked, never called by name. `Sinatra::Base.inherited`
    (sinatra's core subclass hook) was flagged dead — a cardinal FP."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib/base.rb": (
            "module App\n"
            "  class Base\n"
            "    def self.inherited(subclass)\n      setup(subclass)\n    end\n\n"
            "    def method_missing(name, *args)\n      handle(name)\n    end\n\n"
            "    def really_unused\n      1\n    end\n"
            "  end\n"
            "end\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split(".")[-1] for c in sg.find_stale(store).result}
        assert "inherited" not in stale        # interpreter subclass hook: live
        assert "method_missing" not in stale   # interpreter missing-method hook: live
        assert "really_unused" in stale        # ordinary unused method: still flagged


def test_java_serialization_hooks_are_live(tmp_path):
    """Java serialization magic methods (`writeReplace`/`readObject`/...) are invoked by
    `ObjectOutputStream`/`ObjectInputStream` via reflection, never by name. `gson`'s
    `LazilyParsedNumber.writeReplace` was flagged dead — a cardinal FP."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Num.java": (
            "class Num {\n"
            "    private Object writeReplace() { return helper(); }\n"
            "    private Object helper() { return this; }\n"
            "    private int reallyUnused() { return 7; }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split(".")[-1] for c in sg.find_stale(store).result}
        assert "writeReplace" not in stale   # serialization hook: live
        assert "reallyUnused" in stale       # ordinary unused method: still flagged


def test_php_magic_methods_are_live(tmp_path):
    """PHP magic methods (`__call`, `__get`, ...) are invoked by the engine on missing
    members, never by name — they must not be flagged dead."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Obj.php": (
            "<?php\n"
            "class Obj {\n"
            "    public function __call($name, $args) { return $this->dispatch($name); }\n"
            "    private function dispatch($name) { return $name; }\n"
            "    private function reallyUnused() { return 1; }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split(".")[-1] for c in sg.find_stale(store).result}
        assert "__call" not in stale       # engine magic method: live
        assert "reallyUnused" in stale     # ordinary unused method: still flagged


def test_c_bodyless_struct_reference_is_not_a_phantom_class(tmp_path):
    """A bodyless C struct specifier — `struct timeval tv` as a param/field, a forward decl
    `struct X;` — is a TYPE REFERENCE, not a definition. Extracting it as a CLASS minted a
    phantom node that was then flagged dead (hiredis: dozens of `struct timeval`/`event_base`
    references became dead 'classes'). Only a specifier WITH a body defines a type."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.h": (
            "struct timeval;\n"                       # forward decl (bodyless)
            "struct Real { int x; };\n"               # real definition (has a body)
            "void use(struct timeval tv);\n"          # bodyless ref in a param
        ),
        "lib.c": (
            "#include \"lib.h\"\n"
            "struct Real make(void) {\n"
            "    struct timeval local;\n"              # bodyless ref as a local
            "    struct Real r; r.x = 1; return r;\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        from stitchgraph.core.model import NodeKind
        classes = {n.name for n in store.nodes_by_kind(NodeKind.CLASS)}
        assert "timeval" not in classes   # bodyless type reference: never a node
        assert "Real" in classes          # real bodied struct: still extracted


def test_java_framework_callback_annotation_is_live(tmp_path):
    """A Java method carrying a framework-callback annotation (`@PostConstruct`,
    `@BeforeExperiment`, ...) is reflection-invoked, never called by name. gson's
    `@PostConstruct validate` and Caliper `@BeforeExperiment setUp` were flagged dead."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Svc.java": (
            "class Svc {\n"
            "    @PostConstruct void validate() { check(); }\n"
            "    private void check() { }\n"
            "    private void reallyUnused() { }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split(".")[-1] for c in sg.find_stale(store).result}
        assert "validate" not in stale     # @PostConstruct callback: live
        assert "reallyUnused" in stale     # ordinary unused method: still flagged


def test_csharp_serialization_callback_attribute_is_live(tmp_path):
    """A C# method with a serialization-callback attribute (`[OnSerializing]`,
    `[OnDeserialized]`, ...) is invoked by the serializer via reflection. Newtonsoft's
    `[OnSerializing] OnSerializingMethod` (free-form name) was flagged dead."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Obj.cs": (
            "class Obj {\n"
            "    [OnSerializing]\n"
            "    internal void OnSerializingMethod(StreamingContext c) { Prep(); }\n"
            "    void Prep() { }\n"
            "    void ReallyUnused() { }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split(".")[-1] for c in sg.find_stale(store).result}
        assert "OnSerializingMethod" not in stale   # [OnSerializing] callback: live
        assert "ReallyUnused" in stale              # ordinary unused method: still flagged


def test_ts_framework_decorator_handler_is_live(tmp_path):
    """A TS class/method carrying a framework decorator (`@Controller`, `@Get`, `@Injectable`,
    `@Entity`) is framework-instantiated/-invoked, never called by name. NestJS controller
    route handlers were flagged dead. (Method decorators precede the method as siblings; class
    decorators are children — both must be detected.)"""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    # No `export` — otherwise every public method is rooted as public API and the test
    # can't isolate the decorator's effect. Here the ONLY roots are the decorators.
    _mk(tmp_path, {
        "app.controller.ts": (
            "@Controller('users')\n"
            "class UsersController {\n"
            "  @Get(':id')\n"
            "  getUser(id: string) { return this.lookup(id); }\n\n"
            "  lookup(id: string) { return id; }\n\n"
            "  reallyUnused() { return 0; }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split(".")[-1] for c in sg.find_stale(store).result}
        assert "getUser" not in stale        # @Get route handler: live (framework-invoked)
        assert "UsersController" not in stale  # @Controller class: live (framework-instantiated)
        assert "lookup" not in stale          # reached from the live handler
        assert "reallyUnused" in stale        # undecorated, uncalled: still flagged


def test_dependency_directories_are_not_indexed(tmp_path):
    """Vendored/dependency/build dirs (`node_modules`, `vendor`, `third_party`, ...) are
    never first-party source — indexing them floods find_stale with thousands of dead
    vendored symbols and wastes time (tinycc Win32 headers, composer/Go vendor/, npm deps).
    Both extractors must skip them; only the real source is indexed."""
    _mk(tmp_path, {
        "app.py": "def real_fn():\n    return 1\n",
        "node_modules/dep/index.js": "function vendoredJs() { return 1; }\n",
        "vendor/lib.py": "def vendored_py():\n    return 2\n",
        "third_party/x.go": "package x\nfunc Vendored() {}\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        from stitchgraph.core.model import NodeKind
        names = {n.name for k in (NodeKind.FUNCTION, NodeKind.METHOD)
                 for n in store.nodes_by_kind(k)}
        assert "real_fn" in names           # first-party source: indexed
        assert "vendoredJs" not in names    # node_modules: skipped
        assert "vendored_py" not in names   # vendor/: skipped
        assert "Vendored" not in names      # third_party/: skipped


def test_js_member_assigned_function_body_is_walked(tmp_path):
    """A function assigned to an object member (`app.render = function(){...}`, the Express/
    CommonJS prototype-augmentation idiom) must become a node whose BODY is walked — else
    calls inside it are invisible and a module-private helper it alone calls (`tryRender`)
    is flagged dead. The assigned method itself is rooted (external/dynamic-invoked)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "app.js": (
            "function tryRender(v) { return v; }\n"
            "function trulyDead() { return 0; }\n\n"
            "var app = {};\n"
            "app.render = function render(name) { return tryRender(name); };\n"
            "Obj.prototype.handle = function() { return this.helper(); };\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "tryRender" not in stale   # called from the (now-walked) app.render body: live
        assert "render" not in stale      # member-assigned handler: rooted
        assert "handle" not in stale      # prototype method: rooted
        assert "trulyDead" in stale       # genuinely uncalled module function: still flagged


def test_c_export_symbol_is_public_abi(tmp_path):
    """A C function marked `EXPORT_SYMBOL(foo)` / `EXPORT_SYMBOL_GPL(foo)` is public
    kernel/module ABI — called by code outside the tree, so never dead for lack of an
    in-tree caller (the C analogue of __all__/module.exports). The Linux hunt flagged 543
    such functions. Scoped to the file the macro appears in."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lz4.c": (
            "int LZ4_compress_default(const char *s, char *d) { return 0; }\n"
            "EXPORT_SYMBOL(LZ4_compress_default);\n\n"
            "static int helper_gpl(void) { return 1; }\n"
            "EXPORT_SYMBOL_GPL(helper_gpl);\n\n"
            "static int really_internal(void) { return 2; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "LZ4_compress_default" not in stale   # EXPORT_SYMBOL: public ABI, live
        assert "helper_gpl" not in stale             # EXPORT_SYMBOL_GPL: public ABI, live
        assert "really_internal" in stale            # not exported, uncalled: still flagged


def test_classmethod_console_script_does_not_over_root_sibling_methods(tmp_path):
    """A `Class.method` console-script target (`demo = pkg.cli:App.run`) must root ONLY the
    targeted method (and keep its class live), NOT the class's whole public surface. Panel
    R40A: the plugin-class rescue keyed off the post-enclosing-rescue `script` set, so a CLI's
    command class had every public method rooted, masking genuine dead methods."""
    _mk(tmp_path, {
        "pyproject.toml": '[project]\nname="demo"\n[project.scripts]\ndemo = "pkg.cli:App.run"\n',
        "pkg/__init__.py": "",
        "pkg/cli.py": (
            "class App:\n"
            "    def run(self):\n        return self._helper()\n"
            "    def _helper(self):\n        return 1\n"
            "    def genuinely_dead(self):\n        return 99\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "App.run" not in stale              # the entry-point target: live
        assert "App.genuinely_dead" in stale       # sibling public method, uncalled: flagged


def test_comment_between_decorator_and_method_keeps_callback(tmp_path):
    """A `comment` between a JS/TS `@decorator` and the method it annotates must not flush the
    pending decorators — else the framework-callback rooting never fires and the live handler
    (and its callees) is flagged dead. Panel R40B (also protects Rust `#[test]` + comment)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "svc.ts": (
            "@Controller()\n"
            "class Svc {\n"
            "  @Get()\n"
            "  // a comment between decorator and method\n"
            "  findAll() { return this.helper(); }\n"
            "  helper() { return usedByDecorated(); }\n"
            "  reallyUnused() { return 0; }\n"
            "}\n"
            "function usedByDecorated() { return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "findAll" not in stale          # @Get handler (comment notwithstanding): live
        assert "usedByDecorated" not in stale   # reached from the live handler
        assert "reallyUnused" in stale          # undecorated, uncalled: still flagged


def test_member_assigned_function_inside_dead_function_is_not_rooted(tmp_path):
    """A member-assigned function is auto-rooted only at MODULE/class scope. One nested inside
    a function body (`function init(){ obj.x = fn }`) must stay reachability-gated: if the
    enclosing function is dead, the assignment isn't externally visible and must be flagged.
    Panel R40C — the unconditional callback role masked dead code inside dead code."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "m.js": (
            "function deadInit() {\n"
            "  utils.formatDate = function() { return 1; };\n"
            "}\n"
            "app.render = function() { return moduleHelper(); };\n"
            "function moduleHelper() { return 3; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "deadInit" in stale          # uncalled module function: dead
        assert "formatDate" in stale        # assigned inside a dead function: NOT auto-rooted
        assert "render" not in stale        # module-scope member assignment: rooted
        assert "moduleHelper" not in stale  # called by the live module-scope handler


def test_member_assigned_class_public_methods_are_live(tmp_path):
    """R46A (cardinal): a module-scope member-assigned CLASS (`exports.Parser = class {...}`,
    the CommonJS pattern) is public API, so it takes the `exported` role — its public methods
    must be rescued by _seed_exported_class_methods. Otherwise the class is live via the root
    while its methods (and their private callees) are flagged dead — the inverse-cardinal
    'class live, methods dead' shape."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "plugin.js": (
            "exports.Parser = class {\n"
            "  constructor(input) { this.input = input; }\n"
            "  parse() { return tokenize(this.input); }\n"
            "  reset() { this.input = ''; }\n"
            "  _privhelper() { return 1; }\n"
            "};\n"
            "function tokenize(s) { return s.split(' '); }\n"
            "function trulyDead() { return 0; }\n"
        ),
        "use.js": "const { Parser } = require('./plugin');\nnew Parser('a b').parse();\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "exports.Parser.parse" not in stale   # public method of member-assigned class: live
        assert "exports.Parser.reset" not in stale
        assert "tokenize" not in stale               # reached only from parse: live
        assert "trulyDead" in stale                  # genuinely uncalled module fn: flagged
        assert "exports.Parser._privhelper" in stale  # private, uncalled: flagged


def test_comment_between_rust_attribute_and_fn_keeps_test_root(tmp_path):
    """R41A: the R40B comment-skip must cover Rust comment node types (`line_comment`/
    `block_comment`, NOT `comment`) — else a `#[test]` + comment + fn drops the test marker
    and the test fn (plus helpers it alone reaches) is confidently flagged dead (cardinal)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.rs": (
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    #[test]\n"
            "    // a comment between attribute and fn\n"
            "    fn closeness_works() { helper_in_test(); }\n"
            "    fn helper_in_test() -> i32 { 1 }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "closeness_works" not in stale   # #[test] survives the comment: rooted
        assert "helper_in_test" not in stale     # reached only from the test fn: live


def test_php_array_callable_method_is_live(tmp_path):
    """R53 (Magento dogfood, cardinal): PHP invokes methods via the `[$this, 'method']`
    callable-array idiom (usort/uasort/preg_replace_callback comparators) — the method name is
    a STRING, not a syntactic call, so the call scan missed it and the live method was
    confidently flagged dead. The tree-sitter PHP extractor now emits a REFERENCES edge for
    the array-callable's method (cardinal-safe: only project symbols resolve, so a non-callable
    2-element array merely over-roots). A genuinely unused private method is still flagged.
    (`compareRows` is protected and `_convert` private — neither is rooted by public-export, so
    they would be flagged dead without the callable-array fix.)"""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Svc.php": (
            "<?php\n"
            "class Svc {\n"
            "    public function run() {\n"
            "        $rows = [];\n"
            "        usort($rows, [$this, 'compareRows']);\n"
            "        return preg_replace_callback('/x/', [$this, '_convert'], 'y');\n"
            "    }\n"
            "    protected function compareRows($a, $b) { return $a <=> $b; }\n"
            "    private function _convert($m) { return ''; }\n"
            "    private function _reallyDead() { return 1; }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    assert "compareRows" not in stale     # [$this, 'compareRows'] usort callback: live
    assert "_convert" not in stale        # [$this, '_convert'] preg_replace_callback: live
    assert "_reallyDead" in stale         # genuinely unused private method: still flagged


def test_ruby_operator_method_captured_and_rooted(tmp_path):
    """R61 (grape dogfood, cardinal): Ruby operator methods (`def []`, `def []=`, `def <=>`)
    have an `operator`-typed name node, which the extractor dropped — so the method was invisible
    AND anything it used (e.g. `ValueArray.new(value)` inside `def []=`) was false-flagged dead,
    because its only construction site lived in an uncaptured method. The extractor now captures
    operator method names and roots them (invoked via operator/index SYNTAX, never a by-name
    call — the Ruby analogue of the C++ special-member pass)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "store.rb": (
            "class Bag\n"
            "  def []=(key, value)\n"
            "    @h ||= {}\n"
            "    @h[key] = Entry.new(value)\n"   # Entry constructed only inside []=
            "  end\n"
            "  def [](key)\n    @h[key]\n  end\n"
            "end\n"
            "class Entry\n"
            "  def initialize(v)\n    @v = v\n  end\n"
            "  def evaluate\n    @v\n  end\n"
            "end\n"
            "class Unused\n"
            "  def initialize\n    @x = 1\n  end\n"   # never .new'd -> genuinely dead
            "end\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ids = {r["id"].split("::", 1)[-1] for r in store.conn.execute("SELECT id FROM nodes")}
        stale_full = {c["id"] for c in sg.find_stale(store).result}
    stale = {i.split("::", 1)[-1].split(".")[-1] for i in stale_full}
    assert "Bag.[]=" in ids and "Bag.[]" in ids       # operator methods captured as nodes
    assert "[]=" not in stale and "[]" not in stale     # rooted (syntax-invoked), not dead
    # Entry is constructed only inside `[]=`; its constructor is now reached, not false-dead:
    assert not any(i.endswith("Entry.initialize") for i in stale_full)
    assert any(i.endswith("Unused.initialize") for i in stale_full)  # genuinely unused: flagged


def test_is_ruby_operator_method_helper():
    """Pin _is_ruby_operator_method directly (R61): operator names (no leading letter/_) are
    rooted; normal/predicate/bang/setter-by-name methods are not over-rooted."""
    from stitchgraph.core.extract.treesitter import _is_ruby_operator_method
    for op in ("[]", "[]=", "<=>", "==", "===", "<<", ">>", "+", "-", "*", "/", "%", "&", "|",
               "^", "~", "!", "<", ">", "<=", ">=", "=~", "+@", "-@"):
        assert _is_ruby_operator_method(op), op
    for normal in ("evaluate", "initialize", "_private", "valid?", "save!", "to_s", "call"):
        assert not _is_ruby_operator_method(normal), normal
    assert not _is_ruby_operator_method("")


def test_csharp_attribute_class_used_via_bracket_is_live(tmp_path):
    """R64 (serilog dogfood, cardinal): C# applies an attribute with the `Attribute` suffix
    OMITTED — `[NoEnumeration]` names class `NoEnumerationAttribute`. The bare `NoEnumeration`
    reference never resolved, so an in-tree attribute class used only via `[X]` was flagged dead.
    The extractor now also emits the suffixed reference from an `attribute` usage."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.cs": (
            "using System;\n"
            "sealed class NoEnumerationAttribute : Attribute { }\n"
            "sealed class UnusedAttribute : Attribute { }\n"   # defined, never applied -> dead
            "public class Guard {\n"
            "    public static T AgainstNull<T>([NoEnumeration] T arg) { return arg; }\n"
            "}\n"
            "public class App {\n"
            "    public static void Main() { Guard.AgainstNull(\"x\"); }\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in (sg.find_stale(store).result or [])}
    assert not any(i.endswith("NoEnumerationAttribute") for i in stale)  # used via [NoEnumeration]
    assert any(i.endswith("UnusedAttribute") for i in stale)             # never applied: dead


def test_csharp_attribute_suffix_ref_helper():
    """Pin _csharp_attribute_suffix_ref (R64): `[Foo]` -> 'FooAttribute'; an already-suffixed
    `[FooAttribute]` -> None (the bare name already resolves)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    from tree_sitter_language_pack import get_parser

    from stitchgraph.core.extract.treesitter import _csharp_attribute_suffix_ref
    p = get_parser("csharp")

    def attr_ref(code_attr):
        src = (f"class C {{ void M([{code_attr}] int x) {{}} }}").encode()
        tree = p.parse(src)
        node = next(n for n in _walk(tree.root_node) if n.type == "attribute")
        return _csharp_attribute_suffix_ref(node, src)

    def _walk(n):
        yield n
        for c in n.children:
            yield from _walk(c)

    assert attr_ref("NoEnumeration") == "NoEnumerationAttribute"
    assert attr_ref("Obsolete") == "ObsoleteAttribute"
    assert attr_ref("NoEnumerationAttribute") is None    # already suffixed -> bare name resolves


def test_csharp_attribute_on_enum_and_delegate_is_live(tmp_path):
    """R66 (sonnet): the R64 C# attribute-suffix fix lived only in _direct_refs, which scans the
    bodies of `spec.defs` nodes. C# `enum`/`delegate` declarations aren't in `defs`, so their
    attributes are walked by _module_uses instead — which lacked the suffix branch, leaving the
    attribute class false-flagged dead. _module_uses now mirrors the _direct_refs branch."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.cs": (
            "using System;\n"
            "sealed class MyFlagAttribute : Attribute { }\n"
            "sealed class InterceptableAttribute : Attribute { }\n"
            "sealed class UnusedAttribute : Attribute { }\n"   # defined, never applied -> dead
            "enum Status { [MyFlag] Active = 1, Idle = 2 }\n"
            "[Interceptable] public delegate void MyDelegate(int x);\n"
            "public class App { public static void Main() { var s = Status.Active; } }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"] for c in (sg.find_stale(store).result or [])}
    assert not any(i.endswith("MyFlagAttribute") for i in stale)        # enum-member attribute
    assert not any(i.endswith("InterceptableAttribute") for i in stale)  # delegate attribute
    assert any(i.endswith("UnusedAttribute") for i in stale)            # never applied: dead


def test_rust_no_mangle_export_is_live(tmp_path):
    """R69 (doc-driven): a Rust `#[no_mangle]` / `#[export_name]` fn exports its symbol to the
    linker/FFI regardless of `pub`, so a NON-pub one is a public-ABI entry point with no in-tree
    caller — it (and what its body reaches) was false-flagged dead. Now rooted (exported), the
    Rust analogue of C EXPORT_SYMBOL. `#[inline]`-only / unused fns still flag."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.rs": (
            "#[no_mangle]\n"
            "extern \"C\" fn rust_entry() -> i32 { only_from_entry() }\n"
            "#[export_name = \"sym\"]\n"
            "fn renamed() -> i32 { 2 }\n"
            "fn only_from_entry() -> i32 { 42 }\n"   # reached only from the non-pub export
            # Rust 2024 edition REQUIRES the `unsafe(...)` wrapper — the mainstream spelling
            # going forward; it must root the same as the bare form (panel R70, cardinal).
            "#[unsafe(no_mangle)]\n"
            "extern \"C\" fn rust_entry_2024() -> i32 { only_from_2024() }\n"
            "fn only_from_2024() -> i32 { 7 }\n"     # reached only from the 2024-syntax export
            "#[inline]\n"
            "fn inline_dead() -> i32 { 1 }\n"        # #[inline] is NOT an export -> dead
            "fn really_dead() -> i32 { 0 }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    assert "rust_entry" not in stale and "renamed" not in stale     # FFI exports: live
    assert "only_from_entry" not in stale                            # reached from an export
    assert "rust_entry_2024" not in stale                            # 2024 unsafe(...) export: live
    assert "only_from_2024" not in stale                             # reached from the 2024 export
    assert "inline_dead" in stale and "really_dead" in stale         # not exports: dead


def test_is_rust_export_attr_helper():
    """Pin _is_rust_export_attr (R69/R70): no_mangle/export_name are exports incl. the Rust-2024
    `unsafe(...)` wrapper and `cfg_attr(<pred>, …)`; inline/test/cfg/doc-string are not."""
    from stitchgraph.core.extract.treesitter import _is_rust_export_attr
    for a in ("#[no_mangle]", "#[export_name = \"x\"]", "#![no_mangle]",
              "#[unsafe(no_mangle)]", "#[unsafe(export_name = \"x\")]",     # Rust 2024 (R70)
              "#[cfg_attr(unix, no_mangle)]"):                              # conditionally applied
        assert _is_rust_export_attr(a), a
    for a in ("#[inline]", "#[test]", "#[cfg(test)]", "#[derive(Debug)]", "#[doc=\"no_mangle\"]",
              "#[unsafe(test)]", "#[link_name = \"x\"]",   # link_name is on imports, not exports
              "no_mangle", "", "not an attribute"):        # non-bracket strings: not attributes
        assert not _is_rust_export_attr(a), a


def test_c_attribute_constructor_destructor_used_export_is_live(tmp_path):
    """R73 (doc-driven): a C function carrying a GCC/Clang/MSVC attribute that makes it an
    implicit entry point or exported symbol has no in-tree by-name caller — `constructor`/
    `destructor` run automatically around `main`, `used` is explicitly kept, `visibility("default")`
    is public ABI — so it (and everything its body reaches) was false-flagged dead (cardinal). Now
    rooted. A plain uncalled static fn and a `visibility("hidden")` fn still flag."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.c": (
            "#include <stdio.h>\n"
            "__attribute__((constructor)) static void init_module(void) { setup(); }\n"
            "static void setup(void) { printf(\"s\"); }\n"        # reached only from constructor
            "__attribute__((destructor)) static void fini_module(void) { teardown(); }\n"
            "static void teardown(void) { printf(\"t\"); }\n"
            "__attribute__((used)) static void keep_me(void) { printf(\"k\"); }\n"
            "__attribute__((visibility(\"default\"))) void exported_api(void) { helper_v(); }\n"
            "static void helper_v(void) { printf(\"h\"); }\n"
            "__attribute__((visibility(\"hidden\"))) void hidden_fn(void) {}\n"  # hidden: stays dead
            "static void really_dead(void) { printf(\"d\"); }\n"
            "int main(void) { return 0; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    for live in ("init_module", "setup", "fini_module", "teardown", "keep_me",
                 "exported_api", "helper_v"):
        assert live not in stale, live          # implicit entry / export (and its callees): live
    assert "really_dead" in stale               # plain uncalled static: dead
    assert "hidden_fn" in stale                 # visibility("hidden") is internal: dead-eligible


def test_cpp_attribute_forms_root(tmp_path):
    """R73: the C++ `[[gnu::constructor]]` / `[[gnu::destructor]]` form, MSVC `__declspec(dllexport)`,
    and a priority constructor `__attribute__((constructor(101)))` all root; `visibility("hidden")`
    does not."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.cpp": (
            "#include <cstdio>\n"
            "[[gnu::constructor]] static void cpp_ctor() { c_helper(); }\n"
            "static void c_helper() { printf(\"h\"); }\n"
            "[[gnu::destructor]] static void cpp_dtor() {}\n"
            "__attribute__((constructor(101))) static void prio_ctor() {}\n"
            "__declspec(dllexport) void win_api() { w_helper(); }\n"
            "static void w_helper() {}\n"
            "static void plain_dead() {}\n"
            "int main() { return 0; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    for live in ("cpp_ctor", "c_helper", "cpp_dtor", "prio_ctor", "win_api", "w_helper"):
        assert live not in stale, live
    assert "plain_dead" in stale


def test_c_attr_roots_helper():
    """Pin _c_attr_roots (R73): the parser-level mapping from a C/C++ function's attributes to
    roots. constructor/destructor/used/retain -> callback; dllexport / visibility("default") ->
    exported; visibility("hidden") and a plain fn -> no roots."""
    from tree_sitter_language_pack import get_parser

    from stitchgraph.core.extract.treesitter import _c_attr_roots

    def roots(decl: str, lang: str = "c") -> set:
        src = decl.encode()
        root = get_parser(lang).parse(src).root_node
        fn = next(n for n in root.children if n.type == "function_definition")
        return _c_attr_roots(fn, src)

    assert roots("__attribute__((constructor)) static void f(void){}") == {"callback"}
    assert roots("__attribute__((destructor)) static void f(void){}") == {"callback"}
    assert roots("__attribute__((used)) static void f(void){}") == {"callback"}
    assert roots("__attribute__((visibility(\"default\"))) void f(void){}") == {"exported"}
    assert roots("__attribute__((visibility(\"hidden\"))) void f(void){}") == set()
    assert roots("static void f(void){}") == set()
    # GNU *trailing* form: the attribute attaches to the function_declarator, not the def node.
    assert roots("static void f(void) __attribute__((used)) { }") == {"callback"}
    # GCC double-underscore synonyms (`__constructor__` etc.) — common in system headers (R74).
    assert roots("__attribute__((__constructor__)) static void f(void){}") == {"callback"}
    assert roots("__attribute__((__used__)) static void f(void){}") == {"callback"}
    assert roots("__attribute__((__visibility__(\"default\"))) void f(void){}") == {"exported"}
    assert roots("__attribute__((__visibility__(\"hidden\"))) void f(void){}") == set()
    assert roots("[[gnu::constructor]] static void f(){}", "cpp") == {"callback"}
    assert roots("__declspec(dllexport) void f(){}", "cpp") == {"exported"}
    # R74 Finding 2: section -> callback (linker-collected); weak -> exported (linker-visible).
    assert roots("__attribute__((section(\".init_array\"))) void f(void){}") == {"callback"}
    assert roots("__attribute__((weak)) void f(void){}") == {"exported"}


def test_c_alias_ifunc_target_kept_live(tmp_path):
    """R74 Finding 2: `__attribute__((alias("t")))` / `((ifunc("r")))` names another in-tree
    function the linker/loader reaches through this symbol; the target has a real definition with
    no by-name caller and was false-flagged dead (cardinal). The named target is now kept live."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.c": (
            "#include <stdio.h>\n"
            "void real_func(void) { rf_help(); }\n"
            "static void rf_help(void) { printf(\"a\"); }\n"
            "void alias_name(void) __attribute__((alias(\"real_func\")));\n"
            "__attribute__((weak)) void weak_hook(void) { wh_help(); }\n"
            "static void wh_help(void) { printf(\"w\"); }\n"
            "__attribute__((section(\".init_array\"))) void section_entry(void) {}\n"
            "static void plain_dead(void) {}\n"
            "int main(void){return 0;}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    for live in ("real_func", "rf_help", "weak_hook", "wh_help", "section_entry"):
        assert live not in stale, live
    assert "plain_dead" in stale


def test_cpp_empty_body_method_does_not_eat_next_attribute(tmp_path):
    """R75: the tree-sitter C++ grammar parses an *empty-body* inline method `void f() {}` as a
    field_declaration and absorbs the FOLLOWING method's leading attribute, so a
    `__attribute__((visibility("default")))` method right after an empty-body one lost its
    attribute and was false-flagged dead (cardinal). The attribute is now recovered from the prior
    sibling."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "t.cpp": (
            "struct T {\n"
            "  __attribute__((constructor)) void empty_ctor() {}\n"   # empty body -> field_declaration
            "  __attribute__((visibility(\"default\"))) void exported_me() { do_work(); }\n"
            "  void do_work() {}\n"
            "};\n"
            "int main(){ return 0; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    assert "exported_me" not in stale     # its visibility("default") survives the prior empty-body method
    assert "do_work" not in stale         # reached from the exported method


def test_c_dangling_attr_texts_helper():
    """Pin _c_dangling_attr_texts (R75): recover ONLY the trailing attribute (the next decl's,
    after the prior field_declaration's declarator) — not the field's own leading attribute — and
    return nothing when the prior sibling is not a mis-parsed empty-body method."""
    from tree_sitter_language_pack import get_parser

    from stitchgraph.core.extract.treesitter import _c_dangling_attr_texts

    def dangling(struct_src: str) -> list:
        src = struct_src.encode()
        root = get_parser("cpp").parse(src).root_node

        def find_fn(n):
            for c in n.children:
                if c.type == "function_definition":
                    return c
                r = find_fn(c)
                if r is not None:
                    return r
            return None

        fn = find_fn(root)
        return [t.decode() for t in _c_dangling_attr_texts(fn, src)]

    # Empty-body method before an attributed one: recover ONLY the trailing visibility attribute.
    got = dangling("struct T {\n"
                   "  __attribute__((constructor)) void a() {}\n"
                   "  __attribute__((visibility(\"default\"))) void b() { w(); }\n"
                   "  void w() {}\n};\n")
    assert any("visibility" in t for t in got)          # the next decl's attr is recovered
    assert not any("constructor" in t for t in got)     # NOT the field's own leading attr
    # A plain field (no function_declarator) before a method recovers nothing — must not steal its
    # own trailing attribute.
    got2 = dangling("struct U {\n"
                    "  int x __attribute__((aligned(8)));\n"
                    "  void m() { z(); }\n"
                    "  void z() {}\n};\n")
    assert got2 == []


def test_c_alias_target_names_helper():
    """Pin _c_alias_target_names (R74): extract alias/ifunc target names, incl. dunder spelling."""
    from stitchgraph.core.extract.treesitter import _c_alias_target_names
    assert _c_alias_target_names(b'void o(void) __attribute__((alias(\"new_impl\")));') == {"new_impl"}
    assert _c_alias_target_names(b'void f(void) __attribute__((ifunc(\"resolver\")));') == {"resolver"}
    assert _c_alias_target_names(b'void o() __attribute__((__alias__(\"t\")));') == {"t"}
    assert _c_alias_target_names(b'static void f(void){}') == set()


def test_cpp_header_declaration_export_attr_roots_definition(tmp_path):
    """R77 F2 (v2.1.5): an export attribute on a C++ *header declaration* — `__attribute__((
    visibility("default")))` on the in-class member declaration — must root the out-of-line `.cpp`
    definition, which carries no attribute. Otherwise the public-ABI method (and its callees) is
    false-flagged dead at confidence 0.6 (cardinal). A non-exported sibling method still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "w.h": (
            "struct W {\n"
            "  __attribute__((visibility(\"default\"))) int exported_m(int x);\n"
            "  int internal_m(int x);\n"
            "};\n"
        ),
        "w.cpp": (
            "#include \"w.h\"\n"
            "int W::exported_m(int x) { return em_help(x); }\n"
            "static int em_help(int x) { return x; }\n"
            "int W::internal_m(int x) { return x; }\n"
            "__attribute__((constructor)) static void boot(void){ live(); }\n"  # keep file partly live
            "static void live(void){}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    assert "exported_m" not in stale     # rooted via the header declaration's export attribute
    assert "em_help" not in stale        # reached from the exported method
    assert "internal_m" in stale         # no export attribute, no caller: correctly dead


def test_c_export_decl_names_helper():
    """Pin _c_export_decl_names (R77 F2): collect names of functions/methods declared with an export
    attribute — top-level qualified, free, and in-class member; ignore non-export attrs and bodies."""
    from tree_sitter_language_pack import get_parser

    from stitchgraph.core.extract.treesitter import _c_export_decl_names

    def names(src: str) -> set:
        b = src.encode()
        return _c_export_decl_names(get_parser("cpp").parse(b).root_node, b)

    assert names('__attribute__((visibility("default"))) int Widget::compute(int);') == {"compute"}
    assert names('__attribute__((visibility("default"))) void free_api(void);') == {"free_api"}
    assert names('struct W { __attribute__((visibility("default"))) int m(int); };') == {"m"}
    assert names('__declspec(dllexport) int Other::run();') == {"run"}
    assert names('__attribute__((visibility("hidden"))) void h(void);') == set()  # hidden: not export
    assert names('int plain(void);') == set()                                     # no attribute
    # R78: the function_declarator is nested inside a pointer/reference-return wrapper — descend to
    # it so a pointer-returning export is collected (else its def is false-flagged dead, cardinal).
    assert names('__attribute__((visibility("default"))) char* make_buf(int n);') == {"make_buf"}
    assert names('struct W { __attribute__((visibility("default"))) char* make(int); };') == {"make"}
    assert names('__attribute__((visibility("default"))) int& ref_api(int);') == {"ref_api"}
    # A non-function export (a variable/global) contributes no name.
    assert names('__attribute__((visibility("default"))) int g_counter;') == set()


def test_cpp_pointer_return_header_export_is_live(tmp_path):
    """R78 (cardinal): a pointer/reference-returning function whose export attribute is on the header
    declaration nests its function_declarator inside a pointer_declarator; the declaration-name
    collector must descend to it, or the live out-of-line definition is flagged dead at 0.6."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "api.hpp": (
            "struct W {\n"
            "  __attribute__((visibility(\"default\"))) char* make(int n);\n"
            "};\n"
        ),
        "api.cpp": (
            "#include \"api.hpp\"\n"
            "char* W::make(int n) { return mk_help(n); }\n"
            "static char* mk_help(int n) { return 0; }\n"
            "__attribute__((constructor)) static void boot(void){ live(); }\n"
            "static void live(void){}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    assert "make" not in stale       # pointer-returning header export: rooted
    assert "mk_help" not in stale     # reached from it


def test_cpp_class_level_export_attr_roots_public_methods(tmp_path):
    """R80 F1 (v2.1.6): a class carrying a CLASS-LEVEL export attribute (`class
    __attribute__((visibility("default"))) Foo`) exports its whole public interface, so each public
    method is public ABI even with no per-method attribute. Their out-of-line `.cpp` definitions
    carry no attribute and were false-flagged dead at 0.6 (cardinal). Public methods are now rooted;
    a PRIVATE method stays dead-code-eligible."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "api.hpp": (
            "class __attribute__((visibility(\"default\"))) Foo {\n"
            "public:\n"
            "  int alpha(int x);\n"
            "  int beta(int x);\n"
            "private:\n"
            "  int secret_dead();\n"
            "};\n"
        ),
        "api.cpp": (
            "#include \"api.hpp\"\n"
            "int Foo::alpha(int x) { return a_help(x); }\n"
            "static int a_help(int x){ return x; }\n"
            "int Foo::beta(int x) { return x; }\n"
            "int Foo::secret_dead() { return 0; }\n"
            "__attribute__((constructor)) static void boot(void){ live(); }\n"
            "static void live(void){}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    assert "alpha" not in stale and "beta" not in stale   # public methods of an exported class: live
    assert "a_help" not in stale                            # reached from a public method
    assert "secret_dead" in stale                           # private: not ABI, stays dead-eligible


def test_c_public_method_names_helper():
    """Pin _c_public_method_names (R80 F1): only PUBLIC methods of an export-attributed class/struct.
    `class` defaults private, `struct` defaults public; a per-section access label switches it."""
    from tree_sitter_language_pack import get_parser

    from stitchgraph.core.extract.treesitter import _c_export_decl_names

    def names(src: str) -> set:
        b = src.encode()
        return _c_export_decl_names(get_parser("cpp").parse(b).root_node, b)

    # class: default private -> alpha private (excluded), beta public (included)
    assert names('class __attribute__((visibility("default"))) C {'
                 ' int alpha(); public: int beta(); };') == {"beta"}
    # struct: default public -> both included until a private label
    assert names('struct __attribute__((visibility("default"))) S {'
                 ' int a(); private: int b(); };') == {"a"}
    # dllexport class
    assert names('class __declspec(dllexport) D { public: int m(); };') == {"m"}
    # no export attribute on the class -> nothing
    assert names('class Plain { public: int m(); };') == set()
    # a data member in the public section contributes no name (not a function)
    assert names('struct __attribute__((visibility("default"))) V { int field; int f(); };') == {"f"}
    # R81: inline-defined method (function_definition, not field_declaration) is collected
    assert names('class __attribute__((visibility("default"))) I {'
                 ' public: int m(int x){ return x; } };') == {"m"}
    # R81: inline templated method (template_declaration) is collected
    assert names('class __attribute__((visibility("default"))) T {'
                 ' public: template<class X> X tm(X a){ return a; } };') == {"tm"}
    # R81: protected is part of the extensibility ABI (out-of-tree subclasses) -> collected
    assert names('class __attribute__((visibility("default"))) P {'
                 ' protected: int hook(); private: int sec(); };') == {"hook"}


def test_cpp_class_level_export_inline_method_is_live(tmp_path):
    """R81 (cardinal): an INLINE-defined public method of a class-level-export class parses as a
    function_definition (templated: template_declaration), not a field_declaration; it must still be
    rooted or the live public-ABI method is flagged dead at 0.6. Private members still flag."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "api.hpp": (
            "class __attribute__((visibility(\"default\"))) Api {\n"
            "public:\n"
            "  int compute(int x) { return x + secret(); }\n"   # inline public method
            "  template<class X> X passthru(X a) { return a; }\n"  # inline templated public method
            "protected:\n"
            "  int hook() { return 1; }\n"                       # protected: extensibility ABI
            "private:\n"
            "  int secret() { return 7; }\n"                     # private, but reached from compute
            "  int dead_priv() { return 9; }\n"                  # private, uncalled: dead
            "};\n"
        ),
        "main.cpp": (
            "#include \"api.hpp\"\n"
            "__attribute__((constructor)) static void boot(){ live(); }\n"
            "static void live(){}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    for live in ("compute", "passthru", "hook", "secret"):
        assert live not in stale, live
    assert "dead_priv" in stale


def test_rust_third_party_test_attrs_rooted(tmp_path):
    """R84 (v2.1.7, recall): third-party Rust test-harness attributes whose path doesn't end in
    `test` — `#[rstest]`, `#[test_case(...)]`, `#[gtest]` (googletest-rust), `#[quickcheck]` — root
    the free-form-named fn they decorate (and its helpers), which the name/`::test` convention
    misses. A genuinely-dead fn still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.rs": (
            "pub fn used() -> i32 { 1 }\n"
            "#[rstest]\nfn rstest_case() { helper_r(); }\nfn helper_r() {}\n"
            "#[test_case(1)]\nfn tc_case() { helper_tc(); }\nfn helper_tc() {}\n"
            "#[gtest]\nfn gt_case() {}\n"
            "#[quickcheck]\nfn qc_case() {}\n"
            "fn really_dead() {}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    for live in ("rstest_case", "helper_r", "tc_case", "helper_tc", "gt_case", "qc_case"):
        assert live not in stale, live
    assert "really_dead" in stale


def test_is_rust_test_attr_third_party_helper():
    """Pin the R84 third-party test-attr paths; non-test attrs stay False."""
    from stitchgraph.core.extract.treesitter import _is_rust_test_attr
    for a in ("#[rstest]", "#[rstest::rstest]", "#[test_case(1)]", "#[gtest]", "#[quickcheck]",
              "#[tokio::test]", "#[test]", "#[cfg(test)]"):
        assert _is_rust_test_attr(a), a
    for a in ("#[derive(Debug)]", "#[inline]", "#[serde(rename=\"x\")]", "#[cfg(feature=\"testing\")]",
              "#[my_attr(test)]",          # non-cfg attr containing a `test` arg token: not a test
              "rstest", "", "not an attr"):  # non-bracket strings: not attributes
        assert not _is_rust_test_attr(a), a


def test_java_bytebuddy_moshi_annotations_rooted(tmp_path):
    """R84 (v2.1.7, recall): ByteBuddy `@Advice.OnMethodEnter`/`@OnMethodExit` (bytecode
    instrumentation) and Moshi `@ToJson`/`@FromJson` (reflection adapters) are framework-invoked,
    not called by name — they were flagged dead (documented gap, panel R62/R68 hunt). Now rooted
    `callback`; their callees go live. An unannotated uncalled method still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Adv.java": (
            "class Adv {\n"
            "  @Advice.OnMethodEnter static void enter() { reached_e(); }\n"
            "  static void reached_e() {}\n"
            "  @Advice.OnMethodExit static void exit() { reached_x(); }\n"
            "  static void reached_x() {}\n"
            "  @ToJson String toJson(Url u) { return tj(); }\n"
            "  String tj() { return \"\"; }\n"
            "  void deadm() {}\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    for live in ("enter", "reached_e", "exit", "reached_x", "toJson", "tj"):
        assert live not in stale, live
    assert "deadm" in stale


def test_php_bare_string_callable_is_live(tmp_path):
    """R86 (v2.1.8, recall): a project global function passed as a BARE-STRING callback to a known
    PHP callback builtin — `usort($x, 'topcmp')`, `call_user_func('handler')`, `array_map('mapper',
    …)` — is reached at runtime but the syntactic call scan misses the string, so it was
    false-flagged dead (documented gap, panel R57). Now rooted. A string passed to a non-callback
    function does NOT root it (over-match guard)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.php": (
            "<?php\n"
            "function topcmp($a, $b) { return $a - $b; }\n"
            "function handler() { echo \"h\"; }\n"
            "function mapper($x) { return $x * 2; }\n"
            "function notcb() { echo \"x\"; }\n"
            "function logmsg($s) { echo $s; }\n"
            "function run() {\n"
            "    $arr = [3,1,2];\n"
            "    usort($arr, 'topcmp');\n"
            "    call_user_func('handler');\n"
            "    $r = array_map('mapper', $arr);\n"
            "    logmsg('notcb');\n"           # 'notcb' to a non-callback fn: must NOT root
            "    return $r;\n"
            "}\n"
            "function really_dead() { echo \"d\"; }\n"
            "run();\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    for live in ("topcmp", "handler", "mapper"):
        assert live not in stale, live
    assert "notcb" in stale            # string arg to a non-callback builtin: not rooted
    assert "really_dead" in stale      # never referenced: dead


def test_php_string_callable_names_helper():
    """Pin _php_string_callable_names (R86): only string args of known callback builtins; a
    `Class::method` string and a non-callback callee yield nothing."""
    from tree_sitter_language_pack import get_parser

    from stitchgraph.core.extract.treesitter import _php_string_callable_names

    def names(call_src: str) -> set:
        b = (f"<?php function f() {{ {call_src}; }}").encode()
        root = get_parser("php").parse(b).root_node

        def find_call(n):
            for c in n.children:
                if c.type == "function_call_expression":
                    return c
                r = find_call(c)
                if r is not None:
                    return r
            return None

        call = find_call(root)
        return {nm for nm, _ in _php_string_callable_names(call, b)}

    assert names("usort($x, 'topcmp')") == {"topcmp"}
    assert names("call_user_func('handler')") == {"handler"}
    assert names("array_map('mapper', $x)") == {"mapper"}
    assert names("call_user_func('Cls::method')") == set()   # static string: already-rooted public
    assert names("logmsg('notcb')") == set()                  # not a callback builtin


def test_rust_runtime_entry_attrs_rooted(tmp_path):
    """R88 (v2.1.9, cardinal): Rust `#[panic_handler]`/`#[start]`/`#[alloc_error_handler]` fns are
    invoked by the runtime, not by an in-tree call, and need not be `pub`, so they (and their
    callees) were false-flagged dead at 0.6. Now rooted. `#[proc_macro]` is already covered (it
    requires `pub`); a plain uncalled fn still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.rs": (
            "#![no_std]\n"
            "#[panic_handler]\n"
            "fn my_panic(info: &core::panic::PanicInfo) -> ! { ph_help(); loop {} }\n"
            "fn ph_help() {}\n"
            "#[start]\n"
            "fn my_start(_a: isize, _b: *const *const u8) -> isize { st_help(); 0 }\n"
            "fn st_help() -> i32 { 1 }\n"
            "#[alloc_error_handler]\n"
            "fn on_oom(_l: core::alloc::Layout) -> ! { aeh_help(); loop {} }\n"
            "fn aeh_help() {}\n"
            "fn really_dead() {}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    for live in ("my_panic", "ph_help", "my_start", "st_help", "on_oom", "aeh_help"):
        assert live not in stale, live
    assert "really_dead" in stale


def test_is_rust_runtime_entry_attr_helper():
    """Pin _is_rust_runtime_entry_attr (R88): panic_handler/start/alloc_error_handler only."""
    from stitchgraph.core.extract.treesitter import _is_rust_runtime_entry_attr
    for a in ("#[panic_handler]", "#[start]", "#[alloc_error_handler]"):
        assert _is_rust_runtime_entry_attr(a), a
    for a in ("#[no_mangle]", "#[test]", "#[inline]", "#[derive(Debug)]", "#[doc=\"start\"]",
              "panic_handler", "", "not an attr"):
        assert not _is_rust_runtime_entry_attr(a), a


def test_go_cgo_export_directive_rooted(tmp_path):
    """R88 (v2.1.9, cardinal): a Go cgo `//export name` directly above a func makes it C-callable
    (native entry point, no in-tree caller). A capitalised one is already exported by Go's rule, but
    a LOWERCASE `//export lower_entry` was false-flagged dead. Now rooted from the directive."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "main.go": (
            "package main\n"
            "import \"C\"\n"
            "//export lower_entry\n"
            "func lower_entry() { lowHelp() }\n"
            "func lowHelp() {}\n"
            "func reallyDeadGo() {}\n"
            "func main() {}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "lower_entry" not in stale and "lowHelp" not in stale
    assert "reallyDeadGo" in stale


def test_go_has_export_directive_helper():
    """Pin _go_has_export_directive (R88): True only when the immediately-preceding `//export <name>`
    comment names THIS func; False for a name mismatch, no comment, or no preceding sibling."""
    from tree_sitter_language_pack import get_parser

    from stitchgraph.core.extract.treesitter import _go_has_export_directive

    def has(src: str, fn_name: str) -> bool:
        b = src.encode()
        root = get_parser("go").parse(b).root_node

        def find(n):
            for c in n.children:
                if c.type == "function_declaration":
                    return c
                r = find(c)
                if r is not None:
                    return r
            return None

        return _go_has_export_directive(find(root), fn_name, b)

    assert has("package m\n//export f\nfunc f(){}", "f") is True
    assert has("package m\n//export other\nfunc f(){}", "f") is False   # name mismatch
    assert has("package m\nfunc f(){}", "f") is False                   # no preceding comment
    assert has("func f(){}", "f") is False                              # no prev sibling (guard)


def test_csharp_unmanaged_callers_only_rooted(tmp_path):
    """R88 (v2.1.9, cardinal): a C# `[UnmanagedCallersOnly]` method is a native (C-ABI) entry point
    invoked from unmanaged code, not by a managed caller, and is typically non-public — so it (and
    its callees) was false-flagged dead. Now rooted `callback`. A plain uncalled method still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "Lib.cs": (
            "class Lib {\n"
            "  [UnmanagedCallersOnly(EntryPoint = \"native_entry\")]\n"
            "  static void NativeEntry() { UceHelp(); }\n"
            "  static void UceHelp() {}\n"
            "  void deadCs() {}\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
    assert "NativeEntry" not in stale and "UceHelp" not in stale
    assert "deadCs" in stale


def test_python_ipython_display_protocol_hooks_rooted(tmp_path):
    """R90 (v2.1.10, cardinal — found dogfooding rich): the IPython/Jupyter rich-display protocol
    methods (`_repr_html_`, `_repr_mimebundle_`, …) are single-underscore (so the `__x__` dunder
    pass misses them) but are invoked BY NAME by IPython on display, never from source. A live
    class's hook — and whatever it reaches — was false-flagged dead. Tied to the class like a
    dunder: a live class's hooks (+ callees) live; a dead class's hooks stay dead (cardinal-safe)."""
    _mk(tmp_path, {
        "m.py": (
            "__all__ = [\"Widget\"]\n"
            "class Widget:\n"
            "    def _repr_html_(self):\n"
            "        return fmt_html()\n"
            "    def _repr_mimebundle_(self, include, exclude):\n"
            "        return mime_payload()\n"
            "def fmt_html():\n"
            "    return \"<b>w</b>\"\n"
            "def mime_payload():\n"
            "    return {}\n"
            "class DeadWidget:\n"
            "    def _repr_html_(self):\n"
            "        return dead_only_helper()\n"
            "def dead_only_helper():\n"
            "    return \"x\"\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Widget._repr_html_" not in stale          # live class's IPython hook: rooted
    assert "Widget._repr_mimebundle_" not in stale
    assert "fmt_html" not in stale and "mime_payload" not in stale   # hooks' callees: live
    assert "DeadWidget._repr_html_" in stale          # dead class's hook stays dead (cardinal-safe)
    assert "dead_only_helper" in stale


def test_is_protocol_method_helper():
    """Pin _is_protocol_method (R90): interpreter dunders AND IPython display hooks; a plain
    single-underscore method or a public method is not a protocol method."""
    from stitchgraph.core.extract.python import _is_protocol_method
    for n in ("__call__", "__getitem__", "__enter__",                      # interpreter dunders
              "_repr_html_", "_repr_mimebundle_", "_ipython_display_",      # IPython protocol
              "_ipython_key_completions_",
              "_missing_", "_generate_next_value_"):                        # enum machinery hooks
        assert _is_protocol_method(n), n
    for n in ("_private", "_repr_", "render", "__x", "x__", "__", "_repr_custom_", "_missing"):
        assert not _is_protocol_method(n), n


# -- R93 / manual-pass (CARDINAL): enum machinery hooks tied to their class ------
def test_enum_hooks_live_when_enum_class_reachable(tmp_path):
    """`_missing_` / `_generate_next_value_` are invoked by name by the enum metaclass
    (`Color(x)` lookup miss; `auto()`), never from source. A reachable enum's hooks (and
    the helpers they alone reach) must stay live; a dead enum's hooks stay dead
    (cardinal-safe — tied to the class, not rooted unconditionally)."""
    _mk(tmp_path, {
        "pkg/__init__.py": "from .colors import Color as Color\n",
        "pkg/colors.py": """
            import enum

            class Color(enum.Enum):
                RED = 1
                @classmethod
                def _missing_(cls, value):
                    return resolve_alias(value)
                @staticmethod
                def _generate_next_value_(name, start, count, last):
                    return gen_value(name)

            def resolve_alias(v):
                return None
            def gen_value(n):
                return n

            class DeadColor(enum.Enum):
                X = 1
                @classmethod
                def _missing_(cls, value):
                    return dead_helper(value)
            def dead_helper(v):
                return None
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Color._missing_" not in stale              # live enum's hooks: rooted via class
    assert "Color._generate_next_value_" not in stale
    assert "resolve_alias" not in stale and "gen_value" not in stale   # hooks' callees: live
    assert "DeadColor._missing_" in stale              # dead enum's hook stays dead (cardinal-safe)
    assert "dead_helper" in stale


# -- R93 / manual-pass (CARDINAL): pytest conftest hooks rooted by name ----------
def test_pytest_conftest_hooks_are_not_stale(tmp_path):
    """pytest discovers and calls `pytest_*` hook functions by name from conftest.py — no
    in-tree call site exists, so without rooting they (and their helpers) are false-flagged
    dead. The `test`-prefix role only covers `test*`; `pytest_*` needs its own rooting."""
    _mk(tmp_path, {
        "app.py": "__all__ = [\"run\"]\ndef run():\n    return 1\n",
        "tests/test_thing.py": "def test_ok():\n    assert True\n",
        "tests/conftest.py": """
            def pytest_configure(config):
                register_marker(config)
            def pytest_collection_modifyitems(items):
                reorder(items)
            def register_marker(c):
                return c
            def reorder(i):
                return i
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "pytest_configure" not in stale
    assert "pytest_collection_modifyitems" not in stale
    assert "register_marker" not in stale and "reorder" not in stale   # hooks' callees: live


def test_is_pytest_hook_helper():
    """Pin _is_pytest_hook (R93): a `pytest_`-prefixed name with a suffix is a hook; a long
    non-prefixed name and the bare prefix are not (pins the `and`, not `or`, in the guard)."""
    from stitchgraph.core.extract.python import _is_pytest_hook
    assert _is_pytest_hook("pytest_configure")
    assert _is_pytest_hook("pytest_collection_modifyitems")
    assert not _is_pytest_hook("pytest_")              # bare prefix, no hook name
    assert not _is_pytest_hook("register_marker")      # long name, wrong prefix (kills or-mutant)
    assert not _is_pytest_hook("test_thing")
    assert not _is_pytest_hook("py")


# -- R92 / dogfood (CARDINAL): subscripted generic base must record INHERITS -----
def test_subscripted_generic_base_keeps_polymorphic_override_live(tmp_path):
    """`class Sub(Base[K, V])` must record an INHERITS edge so a polymorphic override
    of a base template method is reached, not flagged dead. The base-class expression is
    an ast.Subscript whose `.value` is the real name; `_name_of` returned None for it, so
    no INHERITS edge was emitted and the live override was cardinal-false-flagged
    (confirmed on sqlalchemy / werkzeug `Mixin(Base[K, V])`)."""
    _mk(tmp_path, {
        "pkg/__init__.py": "from .mixins import Base as Base, SubMixin as SubMixin\n",
        "pkg/mixins.py": """
            from typing import Generic, TypeVar
            K = TypeVar("K"); V = TypeVar("V")

            class Base(Generic[K, V]):
                def _hook(self):
                    return []
                def compute(self):
                    return list(self._hook())   # template method -> self._hook()

            class SubMixin(Base[K, V]):          # subscripted generic base
                def _hook(self):
                    return [1, 2, 3]             # live override -- must not be stale
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "SubMixin._hook" not in stale   # reached via INHERITS + polymorphic edge


def test_base_name_unwraps_subscripted_generic():
    """Pin _base_name (R92): a plain/attribute base resolves to its name; a subscripted
    generic unwraps to the underlying base name; a non-name expression is None."""
    import ast

    from stitchgraph.core.extract.python import _base_name
    assert _base_name(ast.parse("Base", mode="eval").body) == "Base"
    assert _base_name(ast.parse("mod.Base", mode="eval").body) == "Base"
    assert _base_name(ast.parse("Base[K, V]", mode="eval").body) == "Base"
    assert _base_name(ast.parse("mod.Base[K, V]", mode="eval").body) == "Base"
    assert _base_name(ast.parse("Base[K][V]", mode="eval").body) == "Base"   # nested subscript
    assert _base_name(ast.parse("(a + b)", mode="eval").body) is None


def test_subscripted_external_base_gets_framework_callback(tmp_path):
    """The real sqlalchemy/werkzeug shape: a subclass of a *subscripted external* generic base
    (`class V(GenericView[int])`) must get framework-callback rooting — the base resolves to a
    non-first-party name (signal (b) in _apply_callback_roles), so the class's framework-invoked
    override (and its callees) stay live. Before the _base_name fix the subscript yielded no base
    name at all, so neither the INHERITS edge nor the external-base signal fired."""
    _mk(tmp_path, {
        "app/__init__.py": "from .views import MyView as MyView\n",
        "app/views.py": """
            from framework import GenericView   # external (unindexed) framework base

            class MyView(GenericView[int]):
                def get(self, request):          # framework-invoked override -- live
                    return render_body()

            def render_body():
                return "ok"
        """,
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "MyView.get" not in stale          # framework override rooted via external base
    assert "render_body" not in stale         # and its callee


# -- R94 / dogfood+manual (CARDINAL): transitive external-base callback closure -----
def test_php_transitive_framework_inheritance_keeps_override_live(tmp_path):
    """A concrete override two+ hops below an external framework base (via an in-tree
    abstract intermediary) is framework-invoked and must stay live. `external_base_classes`
    only checked the DIRECT parent, so the grandchild override got no callback role and was
    flagged dead (CARDINAL, confirmed on Magento Shipment._getValidationRulesBeforeSave)."""
    _mk(tmp_path, {
        "Base.php": "<?php\nabstract class Base extends ExternalFramework {\n"
                    "    protected function handle() {}\n}\n",
        "Child.php": "<?php\nclass Child extends Base {\n"
                     "    protected function handle() { return $this->helper(); }\n"
                     "    private function helper() { return 1; }\n}\n",
        "main.php": "<?php\n$c = new Child();\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Child.handle" not in stale     # framework override, rooted transitively
    assert "Child.helper" not in stale     # reached only through the override


def test_csharp_explicit_interface_via_project_chain_live(tmp_path):
    """C# explicit interface impl (`void IDisposable.Dispose()`) reached only implicitly
    (`using`) via a PROJECT interface that extends the framework interface. The project
    interface resolves in-tree, so the implementing class was not a direct external subclass
    and its Dispose (+ callee) were flagged dead — same root cause as the PHP case."""
    _mk(tmp_path, {
        "P.cs": "using System;\n"
                "interface IHook : IDisposable { }\n"
                "class H : IHook {\n"
                "    void IDisposable.Dispose() { Clean(); }\n"
                "    void Clean() { }\n"
                "    public void Touch() { }\n}\n"
                "class Program { static void Main() { using (IHook h = new H()) { h.Touch(); } } }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "H.Dispose" not in stale
    assert "H.Clean" not in stale


def test_transitive_closure_does_not_overmask_pure_firstparty_chain(tmp_path):
    """Cardinal-safety boundary: a first-party inheritance chain with NO external base must
    NOT get framework rooting — a genuinely-dead override stays flagged."""
    _mk(tmp_path, {
        "Base.php": "<?php\nabstract class Base {\n    protected function handle() {}\n}\n",
        "Child.php": "<?php\nclass Child extends Base {\n"
                     "    protected function deadHandle() { return 1; }\n}\n",
        "main.php": "<?php\n$c = new Child();\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Child.deadHandle" in stale     # no external base anywhere -> still dead


def test_framework_classes_helper():
    """Pin _framework_classes (R94): direct external base, transitive descendant, plain-base
    exclusion, and same-name self-loop. The self-loop (`class S extends S` — base leaf binds to
    itself, the werkzeug-EnvironBuilder shape) IS framework: its real base is an external
    same-named class, so it must be rooted (python.py `_apply_callback_roles` case (b)). A plain
    base (`Object`) is not framework."""
    from stitchgraph.core.extract.treesitter import _framework_classes
    class_by_name = {
        "Base": {"f::Base"}, "Child": {"f::Child"}, "Grand": {"f::Grand"},
        "E": {"f::E"}, "S": {"f::S"},
    }
    inherits = [
        ("f::Base", "Ext", "php"),       # external direct -> Base framework
        ("f::Child", "Base", "php"),     # first-party -> transitive
        ("f::Grand", "Child", "php"),    # first-party -> deeper transitive
        ("f::E", "Object", "php"),       # plain base (in _PLAIN_BASES) -> not framework
        ("f::S", "S", "php"),            # same-name self loop -> framework (case b)
    ]
    result = _framework_classes(inherits, class_by_name)
    assert result == {"f::Base", "f::Child", "f::Grand", "f::S"}
    assert "f::E" not in result          # plain base excluded


def test_same_name_external_self_loop_keeps_override_live(tmp_path):
    """An in-tree class that extends an EXTERNAL same-named framework class (`class Foo extends
    com.framework.Foo`) parses to a self-loop INHERITS edge (base leaf == subclass name). It must
    be treated as framework, or a protected override invoked only by the framework is flagged dead
    (CARDINAL, the python.py `_apply_callback_roles` case-(b) shape, ported in R94)."""
    _mk(tmp_path, {
        "Foo.java": (
            "package app;\n"
            "import com.framework.Foo;\n"
            "public class Foo extends com.framework.Foo {\n"
            "    protected void onEvent() { helper(); }\n"
            "    private void helper() {}\n}\n"
        ),
        "Main.java": "package app;\npublic class Main { public static void main(String[] a){ new Foo(); } }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Foo.onEvent" not in stale     # framework override on same-name self-loop class
    assert "Foo.helper" not in stale      # reached only through it


# -- R96 / manual-pass (CARDINAL): narrow runtime/native entry-point attrs -------
def test_c_interrupt_isr_attribute_roots_handler(tmp_path):
    """A C function marked `__attribute__((interrupt))` / AVR `((signal))` is an ISR invoked by the
    hardware vector table — no in-tree caller — so it and its callees were flagged dead. The
    implicit-entry attr regex omitted `interrupt`/`signal`. Cardinal-safe (only adds roots): a plain
    static with no attribute still flags dead."""
    _mk(tmp_path, {
        "isr.c": (
            "static void log_event(void) {}\n"
            "__attribute__((interrupt)) static void isr(void) { log_event(); }\n"
            "__attribute__((interrupt_handler)) void isr_arm(void) { log_event(); }\n"
            "static void truly_dead(void) {}\n"
            "int main(void) { return 0; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "isr" not in stale and "log_event" not in stale   # ISR + callee rooted
    assert "isr_arm" not in stale                            # ARM/MIPS `interrupt_handler` form rooted
    assert "truly_dead" in stale                              # no attr -> still dead (cardinal-safe)


def test_rust_ctor_dtor_attribute_roots_function(tmp_path):
    """`#[ctor::ctor]` / `#[ctor]` / `#[ctor::dtor]` run a function automatically before/after main
    (the Rust analogue of C `__attribute__((constructor))`), idiomatically private, so the fn + its
    callees were flagged dead. `_RUST_RUNTIME_ENTRY_ATTRS` omitted ctor/dtor."""
    _mk(tmp_path, {
        "lib.rs": (
            "pub fn api() -> i32 { 1 }\n"
            "#[ctor::ctor]\n"
            "fn init() { setup(); }\n"
            "fn setup() {}\n"
            "fn truly_dead() {}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "init" not in stale and "setup" not in stale   # ctor + callee rooted
    assert "truly_dead" in stale                          # plain private -> still dead


def test_java_native_method_is_rooted(tmp_path):
    """A Java `native` method is a JNI entry point (implemented in C, invoked across the JNI
    boundary) with no in-tree caller — a non-public one was flagged dead. Cardinal-safe: a plain
    non-native private method with no caller still flags dead."""
    _mk(tmp_path, {
        "Jni.java": (
            "class Jni {\n"
            "    native int compute(int x);\n"
            "    private int deadHelper() { return 0; }\n"
            "    static { System.loadLibrary(\"jni\"); }\n}\n"
        ),
        "Main.java": "public class Main { public static void main(String[] a){ new Jni(); } }\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Jni.compute" not in stale     # native JNI entry rooted
    assert "Jni.deadHelper" in stale      # plain private, no caller -> still dead


def test_rust_runtime_entry_attr_helper():
    """Pin _is_rust_runtime_entry_attr (R96): ctor/dtor path tokens match; a longer identifier
    containing the token and a non-attr does not."""
    from stitchgraph.core.extract.treesitter import _is_rust_runtime_entry_attr
    assert _is_rust_runtime_entry_attr("#[ctor::ctor]")
    assert _is_rust_runtime_entry_attr("#[ctor]")
    assert _is_rust_runtime_entry_attr("#[ctor::dtor]")
    assert _is_rust_runtime_entry_attr("#[panic_handler]")
    assert not _is_rust_runtime_entry_attr("#[constructor_helper]")   # not a bare ctor token
    assert not _is_rust_runtime_entry_attr("#[doc = \"ctor\"]")       # string stripped
    assert not _is_rust_runtime_entry_attr("#[derive(Debug)]")
    assert not _is_rust_runtime_entry_attr("ctor")                    # not bracketed -> no match
    assert not _is_rust_runtime_entry_attr("")                        # empty -> no match


# -- R98 / manual-pass (CARDINAL): Ruby implicit conversion/Enumerable protocol ----
def test_ruby_implicit_protocol_methods_rooted(tmp_path):
    """Ruby invokes conversion (`to_s`/`inspect`/`to_str`/…), Enumerable (`each`), Hash-key
    (`hash`/`eql?`) and marshalling hooks BY NAME from the interpreter/stdlib — never via a
    textual call — so a live class's protocol methods (and the helpers they reach) were
    false-flagged dead. The Ruby analogue of Python dunder rooting. Cardinal-safe: a plain
    method with no caller still flags dead."""
    _mk(tmp_path, {
        "lib.rb": (
            "class Money\n"
            "  def initialize; @v = 1; end\n"
            "  def to_s; fmt; end\n"            # invoked by puts/interpolation
            "  def fmt; \"x\"; end\n"            # reached only via to_s
            "  def to_f; as_float; end\n"       # numeric coercion (Float(obj)) -> calls Float, not to_f
            "  def as_float; 1.0; end\n"        # reached only via to_f
            "  def really_dead; nuked; end\n"   # genuinely dead
            "  def nuked; 1; end\n"
            "end\n"
            "class Coll\n"
            "  include Enumerable\n"
            "  def initialize; @a = [1, 2]; end\n"
            "  def each(&b); @a.each(&b); end\n"   # driven by Enumerable#map
            "end\n"
            "m = Money.new\n"
            "puts m\n"
            "Coll.new.map { |x| x }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Money.to_s" not in stale and "Money.fmt" not in stale   # conversion hook + callee live
    assert "Money.to_f" not in stale and "Money.as_float" not in stale  # numeric coercion hook + callee
    assert "Coll.each" not in stale                                 # Enumerable driver live
    assert "Money.really_dead" in stale and "Money.nuked" in stale  # genuinely dead -> still flagged


# -- R100 / manual-pass (CARDINAL): C++ range-for begin()/end() customization points
def test_cpp_range_for_begin_end_rooted(tmp_path):
    """`for (x : r)` is desugared by the compiler to `r.begin()`/`r.end()` — the name-based call
    graph never sees those calls, and no other pass roots them, so an iterable's begin/end (and
    what they reach) were false-flagged dead. Rooted via `_IMPLICIT_HOOKS["cpp"]`. Cardinal-safe:
    a plain method with no caller still flags dead."""
    _mk(tmp_path, {
        "r.cpp": (
            "struct Range {\n"
            "    int* a; int n;\n"
            "    int* begin() { return helper(); }\n"   # desugared call target
            "    int* end() { return a + n; }\n"
            "    int* helper() { return a; }\n"          # reached only via begin()
            "    int* truly_dead() { return a; }\n"      # genuinely dead
            "};\n"
            "int main() { int d[2]={1,2}; Range r{d,2}; int s=0; for (int x : r) s+=x; return s; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Range.begin" not in stale and "Range.end" not in stale   # range-for CPOs rooted
    assert "Range.helper" not in stale                               # reached via begin()
    assert "Range.truly_dead" in stale                               # genuinely dead -> still flagged


# -- R102 / manual-pass (CARDINAL): Bash callback/invocation arg recognition --------
def test_bash_callback_arg_invocations_rooted(tmp_path):
    """Bash commands that invoke a function via an ARGUMENT (the generic command scan keys on
    the head and misses these): a trap registered INSIDE a function body, `complete -F FUNC`
    completion handlers, `export -f FUNC` (subshell-invoked), and `time FUNC`. Each must root
    FUNC; a genuinely-dead function and a plain `export VAR=…` must NOT be rooted (cardinal-
    safe — only names matching a project function are rooted)."""
    _mk(tmp_path, {
        "s.sh": (
            "#!/bin/bash\n"
            "cleanup() { echo c; }\n"                       # trap handler inside main()
            "_mycomp() { COMPREPLY=(a b); }\n"              # complete -F handler
            "worker() { echo w; }\n"                         # export -f for subshells
            "bench() { echo b; }\n"                          # time target
            "dead() { echo d; }\n"                           # genuinely dead
            "main() {\n"
            "  trap cleanup EXIT\n"
            "  time bench\n"
            "}\n"
            "complete -F _mycomp mytool\n"
            "export -f worker\n"
            "export PATH=/x\n"                               # plain export: roots nothing
            "main\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "cleanup" not in stale     # trap handler registered inside a function body
    assert "_mycomp" not in stale     # complete -F completion handler
    assert "worker" not in stale      # export -f (subshell-invoked)
    assert "bench" not in stale       # time FUNC
    assert "dead" in stale            # genuinely dead -> still flagged (cardinal-safe)


def test_bash_callback_helpers():
    """Pin the Bash callback-arg parsers (R102) directly: complete -F / export -f / time."""
    pytest.importorskip("tree_sitter_language_pack")
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language

    from stitchgraph.core.extract import treesitter as ts
    parser = Parser(get_language("bash"))
    cases = {
        "complete -F _comp mytool": ["_comp"],
        "compgen -F _gen": ["_gen"],
        'complete -F "_comp" mytool': ["_comp"],  # quoted handler unwrapped (parity w/ trap)
        "complete -F ${VAR} cmd": [],            # dynamic handler -> consume slot, don't grab cmd
        "compgen -F $(f) word": [],              # command-substitution handler -> nothing
        "complete -A dir -F _c cmd": ["_c"],     # flag not first; still found
        "complete -F f1 -F f2 cmd": ["f1", "f2"],  # bash uses last; root all (cardinal-safe)
        "complete -o default mytool": [],        # no -F, no handler
        "export -f worker": ["worker"],
        "export -f a b": ["a", "b"],
        "export PATH=/x": [],                    # plain var export, no -f
        "export -p worker": [],                  # -p (print) is not -f -> no root
        "export -f foo=bar": [],                 # non-identifier after -f -> no root
        "export -f 2bad": [],                     # variable_name but not an identifier -> no root
        "declare -f foo": [],                    # non-export declaration -> ignored entirely
        "time bench": ["bench"],
        "time -p bench": ["bench"],              # -p option skipped
        "time": [],                              # bare keyword, no target
    }
    for code, expected in cases.items():
        src = ("#!/usr/bin/env bash\n" + code + "\n").encode()
        tree = parser.parse(src)
        got = [n for n, _ in ts._bash_callback_refs(tree.root_node, src)]
        assert got == expected, f"{code!r} -> {got}, expected {expected}"


# -- R104 / dogfood (CARDINAL): Ruby &:symbol / enum_for / &method dispatch --------
def test_ruby_symbol_dispatch_rooted(tmp_path):
    """Ruby names a method via a literal symbol the call graph can't see: `xs.map(&:upcase)`
    (Symbol#to_proc), `enum_for(:m)` (lazy enumerator), `&method(:m)`. Each must root the named
    method; genuinely-dead methods still flag (cardinal-safe — only project methods rooted)."""
    _mk(tmp_path, {
        "lib.rb": (
            "class Token\n"
            "  def initialize(v); @v = v; end\n"
            "  def upcase; @v.upcase; end\n"        # via &:upcase
            "  def valid?; !@v.empty?; end\n"       # via &:valid? (note the ? suffix)
            "  def really_dead; nuked; end\n"       # genuinely dead
            "  def nuked; 1; end\n"
            "end\n"
            "class Stream\n"
            "  def filter_tokens(t); t; end\n"      # via enum_for(:filter_tokens)
            "  def run(t); enum_for(:filter_tokens, t); end\n"
            "end\n"
            "class Box\n"
            "  def value=(x); @v = x; end\n"        # setter: def keyed 'value' (no '='), reached
            "  def setter_ref; method(:value=); end\n"   # via method(:value=) -> must root 'value='
            "end\n"
            "toks = [Token.new(\"a\"), Token.new(\"\")]\n"
            "res = toks.select(&:valid?).map(&:upcase)\n"
            "Stream.new.run([1])\n"
            "Box.new.setter_ref\n"
            "puts res\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "Token.upcase" not in stale and "Token.valid?" not in stale   # &:symbol handlers
    assert "Stream.filter_tokens" not in stale                          # enum_for(:m) target
    assert "Box.value" not in stale                                    # setter (keyed w/o `=`) via method(:value=)
    assert "Token.really_dead" in stale and "Token.nuked" in stale      # genuinely dead -> flagged


def test_ruby_symbol_refs_helper():
    """Pin _ruby_symbol_refs (R104): &:sym, enum_for/to_enum/method/instance_method literal-symbol
    args; method names with ?/! suffix; NOT send/public_send; dynamic/operator symbols skipped."""
    pytest.importorskip("tree_sitter_language_pack")
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language

    from stitchgraph.core.extract import treesitter as ts
    parser = Parser(get_language("ruby"))
    cases = {
        "xs.map(&:upcase)": ["upcase"],
        "xs.select(&:valid?)": ["valid?"],
        "xs.each(&:save!)": ["save!"],
        "enum_for(:filter_tokens, a)": ["filter_tokens"],
        "to_enum(:each_line)": ["each_line"],
        "xs.map(&method(:transform))": ["transform"],
        "instance_method(:foo)": ["foo"],
        "obj.method(:name=)": ["name"],        # setter symbol -> def is keyed without the `=`
        "xs.map(&:name=)": ["name"],           # setter via &: too
        "obj.send(:step)": [],                 # send: documented dynamic dispatch, not covered
        "obj.public_send(:go)": [],            # same
        "xs.map(&blk)": [],                    # &var (not a symbol) -> nothing
        "define_method(:gen) { 1 }": [],       # define_method body walked elsewhere; not a target
    }
    for code, expected in cases.items():
        src = (code + "\n").encode()
        tree = parser.parse(src)
        got = [n for n, _ in ts._ruby_symbol_refs(tree.root_node, src)]
        assert got == expected, f"{code!r} -> {got}, expected {expected}"


def test_object_literal_method_bodies_are_walked(tmp_path):
    """#48 (cardinal): a top-level function called ONLY inside an object-literal member —
    `const obj = { run() { reach() } }` (method shorthand), `{ a: () => inner() }`
    (function-valued property), or a nested object — was flagged dead, because the object
    value was never traversed and the call was never seen. Object-literal function members
    at module scope are dynamically invoked (passed as callbacks, spread into config), so
    they're rooted `callback` and their bodies walked. A genuinely-uncalled top-level fn
    must STILL be flagged (no over-rooting of the world)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "m.ts": (
            "export const obj = {\n"
            "  run() { return reach(); },\n"
            "  arrow: () => inner(),\n"
            "  nested: { onClick() { deep(); } }\n"
            "};\n"
            "obj.run();\n"
            "function reach(){ return 1; }\n"
            "function inner(){ return 2; }\n"
            "function deep(){ return 3; }\n"
            "function trulyDead(){ return 99; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "reach" not in stale       # called from a method-shorthand body: live
        assert "inner" not in stale       # called from a function-valued property: live
        assert "deep" not in stale        # called from a nested object's method: live
        assert "trulyDead" in stale       # genuinely uncalled module fn: still flagged


def test_commonjs_module_exports_object_methods_are_walked(tmp_path):
    """#48: the CommonJS `module.exports = { handler() {...} }` object form must also have
    its member bodies walked, so a helper called only there stays live; a sibling never
    reached stays flagged."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "c.js": (
            "function used(){ return 1; }\n"
            "function alsoDead(){ return 2; }\n"
            "module.exports = {\n"
            "  handler() { return used(); }\n"
            "};\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "used" not in stale        # reached from the exported object's method: live
        assert "alsoDead" in stale        # never reached: flagged


def test_object_literal_underscore_member_dynamically_dispatched_is_live(tmp_path):
    """#48 (cardinal, panel R-opus): object literals are the canonical DISPATCH-TABLE idiom —
    `handlers["_" + action]()` reaches an underscore member with no by-name call site. Minting
    an underscore member as an UNROOTED node (an earlier gate) confidently flagged the live
    member — and its callees — dead. Module-scope members are therefore rooted UNCONDITIONALLY
    (underscore included); over-rooting a genuinely-dead member is cardinal-safe."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "p.ts": (
            "function logEvent(name) { return name; }\n"
            "const handlers = {\n"
            "  _open()  { return logEvent('open'); },\n"
            "  _close() { return logEvent('close'); }\n"
            "};\n"
            "export function dispatch(action) {\n"
            "  const key = '_' + action;\n"
            "  return handlers[key]();\n"
            "}\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        assert "handlers._open" not in stale    # dynamically dispatched: rooted, not flagged
        assert "handlers._close" not in stale
        names = {i.split(".")[-1] for i in stale}
        assert "logEvent" not in names          # sole callee of the live members: live


def test_object_literal_computed_key_member_body_is_walked(tmp_path):
    """#48 (cardinal): a computed-key function member (`{ [k]: () => h() }`) has no static
    name, so it can't be rooted by name — but its body must still be walked, or a helper called
    only there is flagged dead when the table is dispatched dynamically. The member is extracted
    under a synthesized id and rooted (computed keys are accessed dynamically by definition)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "c.ts": (
            "const k = 'm';\n"
            "const obj = {\n"
            "  [k]: () => h(),\n"
            "  [k + '2']: { deep() { g(); } }\n"   # computed key -> nested object -> method
            "};\n"
            "Object.values(obj).forEach(f => f());\n"
            "function h(){ return 1; }\n"
            "function g(){ return 3; }\n"
            "function deadFn(){ return 2; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        names = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "h" not in names         # called from the computed-key member body: live
        assert "g" not in names         # called from a method nested under a computed key: live
        assert "deadFn" in names        # genuinely uncalled: still flagged
        # The un-nameable member is ided from the key TEXT (not a constant placeholder), so two
        # distinct computed keys don't collide into one node.
        node_ids = set(store.all_node_ids())
        assert "c.ts::obj.[k]" in node_ids


def test_object_literal_function_property_non_exported_helper_is_live(tmp_path):
    """#48: a function-VALUED property (`{ a: () => helper() }`) on a NON-exported object —
    where _module_uses can't rescue the call because the module isn't a load root — must
    still have its body walked via the extracted member node. Pins the pair / arrow path
    independently of module-scope rooting."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "a.ts": (
            "const obj = { a: () => helper() };\n"
            "function helper(){ return 1; }\n"
            "function deadHelper(){ return 2; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "helper" not in stale       # called from the arrow-valued property: live
        assert "deadHelper" in stale       # never reached: flagged


def test_object_literal_string_keyed_method_body_is_walked(tmp_path):
    """#48: a string-KEYED method (`{ "do-it"() { reach() } }`) — `_name_of` returns None for
    a string key, which would silently drop the member and leave its body unwalked (cardinal).
    `_obj_key_name` reads the unquoted fragment so the member is extracted (under a clean,
    unquoted id), rooted at module scope, and its body walked. A genuinely-uncalled top-level
    function still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "s.ts": (
            "const handlers = {\n"
            "  \"do-it\"() { return reach(); }\n"
            "};\n"
            "function reach(){ return 1; }\n"
            "function deadFn(){ return 2; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ids = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        names = {i.split(".")[-1] for i in ids}
        assert "reach" not in names              # string-keyed method body walked: live
        assert "handlers.do-it" not in ids       # string-keyed member: rooted, clean id
        assert "deadFn" in names                 # genuinely uncalled top-level fn: flagged
        # The id carries the UNQUOTED key (`_obj_key_name` strips the string quotes); a wrong
        # key reader would mint `handlers."do-it"` instead. Pins the string-key path.
        assert "s.ts::handlers.do-it" in set(store.all_node_ids())


def test_object_literal_ts_wrapper_does_not_hide_members(tmp_path):
    """#48 (cardinal, panel R-sonnet): a TS value wrapper — `{…} as const`, `{…} satisfies T`,
    `({…})` — sits between the `variable_declarator` value and the object, so the bare
    `val.type == "object"` check missed it and the members' bodies were never walked. These
    wrappers are pervasive in modern TS. `_unwrap_ts_value` peels them so a helper called only
    from a member of an `as const` object stays live."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    for suffix in ("as const", "satisfies T"):
        _mk(tmp_path, {
            "lib.ts": (
                "function helper(){ return 42; }\n"
                "interface T { run(): void; }\n"
                f"export const obj = {{ run() {{ helper(); }} }} {suffix};\n"
            ),
            "lib.test.ts": "import { obj } from './lib';\ntest('o', () => { obj.run(); });\n",
        })
        with sg.Store(":memory:") as store:
            sg.reindex(store, str(tmp_path))
            stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
            assert "helper" not in stale, f"{suffix}: helper flagged dead"


def test_object_literal_method_body_nested_function_is_extracted(tmp_path):
    """#48 (cardinal, panel R-sonnet): a function DEFINED inside an object method body
    (`run() { function inner(){ helper(); } inner(); }`) must be extracted as a node with a
    CONTAINS edge to the member — pass 2's `_direct_calls` skips nested defs, so without
    walking the member body the inner fn's body is never scanned and a helper it alone calls is
    flagged dead. `_object_members` now recurses into member bodies via `_collect`, like every
    other function-extraction path."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "lib.ts": (
            "function helper(){ return 42; }\n"
            "export const obj = {\n"
            "  run() { function inner() { helper(); } inner(); }\n"
            "};\n"
            "obj.run();\n"
            "function trulyDead(){ return 0; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "helper" not in stale       # called by inner(), nested in the live member: live
        assert "trulyDead" in stale        # genuinely uncalled: flagged


def test_object_literal_member_nested_in_dead_function_stays_gated(tmp_path):
    """#48: an object built inside a DEAD function must stay reachability-gated — the member
    body is walked with `exported=False`, so a def nested in the member is NOT auto-rooted; if
    the enclosing function is never called, the member, its nested def, and a helper that def
    alone calls are all flagged. (Pins the member-body `_collect` `exported` arg: rooting them
    would mask dead code inside dead code.)"""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "m.ts": (
            "function deadSetup() {\n"
            "  const obj = { run() { function inner(){ secret(); } inner(); } };\n"
            "  obj.run();\n"
            "}\n"
            "function secret(){ return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "deadSetup" in stale         # never called: dead
        assert "secret" in stale            # reached only from a def inside a dead member: dead


def test_object_literal_wrapped_and_class_member_values(tmp_path):
    """#48 (cardinal, panel R-opus round 2): a member VALUE that is itself wrapped — a
    parenthesized arrow `run: (() => h())`, a `satisfies`/`as`-wrapped function — or a
    `class` (`{ Parser: class {…} }`) was dropped: `_unwrap_ts_value` was applied to the whole
    object but not to individual member values, and class-valued members weren't handled. Each
    left a live member's body unwalked, flagging a helper called only there dead."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    # paren-wrapped + satisfies-wrapped function-valued members
    _mk(tmp_path, {
        "mod.ts": (
            "function parenHelper(){ return 1; }\n"
            "function satHelper(){ return 2; }\n"
            "type Fn = () => number;\n"
            "export const handlers = {\n"
            "  run: (() => parenHelper()),\n"
            "  go: ((() => satHelper()) satisfies Fn)\n"
            "};\n"
        ),
        "app.ts": "import { handlers } from './mod';\nhandlers.run(); handlers.go();\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "parenHelper" not in stale     # paren-wrapped arrow member body walked: live
        assert "satHelper" not in stale       # satisfies-wrapped arrow member body walked: live


def test_object_literal_class_valued_member(tmp_path):
    """#48 (cardinal, panel R-opus round 2): a class-valued member (`{ Parser: class {…} }`) is
    public API — modeled as a CLASS with the `exported` role so its public methods (and their
    private callees) are rescued. A genuinely-unused private method of that class still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "reg.ts": (
            "function tok(){ return 1; }\n"
            "function deadHelper(){ return 2; }\n"
            "export const registry = {\n"
            "  Parser: class {\n"
            "    parse(){ return tok(); }\n"
            "    _priv(){ return deadHelper(); }\n"
            "  }\n"
            "};\n"
        ),
        "main.ts": "import { registry } from './reg';\nnew registry.Parser().parse();\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "tok" not in stale         # reached from the public class method: live
        assert "deadHelper" in stale      # reached only from an unused private method: flagged


def test_object_literal_class_member_nested_in_function_is_gated(tmp_path):
    """#48 (cardinal, panel R-sonnet round 3): a class-valued member inside a FUNCTION body must
    have its methods reachability-gated to the class (chain: enclosing fn -> class -> methods).
    The class is not `exported`-rooted there (it's gated to the enclosing fn), so walking its
    body with enclosing_func=None orphaned the methods — confidently flagged dead while live.
    Now gated to the class. A class member in a DEAD function stays dead (gating preserved)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    # live enclosing function -> the class member's method (and its callee) live
    _mk(tmp_path, {
        "live.ts": (
            "export function setup() {\n"
            "  const handlers = { Parser: class { parse() { _helper(); } } };\n"
            "  return handlers;\n"
            "}\n"
            "function _helper() { return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "_helper" not in stale     # reachable: setup -> Parser.parse -> _helper: live
    # dead enclosing function -> the class, its method, and the callee all stay flagged
    _mk(tmp_path, {
        "dead.ts": (
            "function deadSetup() {\n"
            "  const handlers = { Parser: class { parse() { secret(); } } };\n"
            "  return handlers;\n"
            "}\n"
            "function secret() { return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "deadSetup" in stale        # never called: dead
        assert "secret" in stale           # reached only via a method of a dead-gated class: dead


def test_member_assigned_class_nested_in_function_is_gated(tmp_path):
    """#48 (cardinal, panel R-sonnet round 4): the `assignment_expression` branch had the same
    function-scoped class-member orphaning bug — `function f(){ obj.X = class { run(){ _h() } } }`
    walked the class body with enclosing_func=None, orphaning its methods (no role, no
    containment) and confidently flagging them — and their callees — dead while live. Now gated
    to the class (chain: enclosing fn -> class -> methods), mirroring the object-literal path. A
    member-assigned class in a DEAD function stays flagged (gating preserved)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "live.ts": (
            "export function f() {\n"
            "  const obj: any = {};\n"
            "  obj.X = class { run() { _h(); } };\n"
            "  return obj;\n"
            "}\n"
            "function _h() { return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "_h" not in stale            # reachable: f -> obj.X.run -> _h: live
    _mk(tmp_path, {
        "dead.ts": (
            "function deadF() {\n"
            "  const obj: any = {};\n"
            "  obj.X = class { run() { secret(); } };\n"
            "  return obj;\n"
            "}\n"
            "function secret() { return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "deadF" in stale             # never called: dead
        assert "secret" in stale            # reached only via a method of a dead-gated class: dead


def test_ts_wrapped_value_in_assignment_and_const(tmp_path):
    """#48 (cardinal, panel R6): TS value wrappers (`as T` / `satisfies T` / `(…)`) must be
    peeled on the assignment-RHS fn/class value (`obj.X = (class {…}) satisfies T`) and on the
    arrow/function variable_declarator value (`export const f = (() => h()) as any`) — not only
    on object values. Without the unwrap the wrapped def is dropped, its body unwalked, and a
    helper it alone calls is flagged dead."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "m.ts": (
            "export function setup() {}\n"
            "function classHelper() {}\n"
            "function fnHelper() {}\n"
            "function arrowHelper() { return 1; }\n"
            "obj.Parser = (class { run() { classHelper(); } }) satisfies any;\n"
            "obj.h = (function(){ fnHelper(); }) as SomeType;\n"
            "export const f = (() => arrowHelper()) as any;\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "classHelper" not in stale    # wrapped class assignment body walked: live
        assert "fnHelper" not in stale        # wrapped function assignment body walked: live
        assert "arrowHelper" not in stale     # wrapped arrow const body walked: live


def test_generator_function_values_are_walked(tmp_path):
    """#48 (cardinal, panel R8): a generator (`function*(){}` / `async function*(){}`) used as a
    function VALUE — an object-literal pair value, an assignment RHS, or a const initializer —
    parses as `generator_function`, which the function-value tuples (`_OBJ_FN_VALUES`, the
    variable_declarator and assignment_expression checks) omitted, so the generator was dropped,
    its body unwalked, and a helper it alone calls flagged dead. (Method-shorthand `{ *gen(){} }`
    is a method_definition and was already handled.)"""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "m.ts": (
            "function objHelper(){ return 1; }\n"
            "function asyncHelper(){ return 2; }\n"
            "function assignHelper(){ return 3; }\n"
            "function constHelper(){ return 4; }\n"
            "function deadHelper(){ return 9; }\n"
            "export const obj = {\n"
            "  gen: function*(){ yield objHelper(); },\n"
            "  agen: async function*(){ yield asyncHelper(); }\n"
            "};\n"
            "exports.h = function*(){ yield assignHelper(); };\n"
            "export const g = function*(){ constHelper(); };\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "objHelper" not in stale      # object-literal generator pair value body walked
        assert "asyncHelper" not in stale    # async-generator pair value body walked
        assert "assignHelper" not in stale   # assignment-RHS generator body walked
        assert "constHelper" not in stale    # const generator initializer body walked
        assert "deadHelper" in stale         # genuinely uncalled: still flagged


def test_module_uses_skips_generator_const_def(tmp_path):
    """#48 (precision, panel R9): `_module_uses` (test/script files) must skip descending into a
    `const g = function*(){…}` generator value — it is itself a def, scanned per-def — else its
    body's calls are double-counted and over-rooted from the module even when `g` is uncalled
    (the generator twin of the Panel-GG arrow/function skip). Cardinal-safe either way, but the
    skip restores precision: an uncalled module-scope generator's sole callee stays flagged."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "foo.test.ts": (
            "const g = function*(){ reallyDead(); };\n"
            "function reallyDead(){ return 1; }\n"
            "test('x', () => { expect(1).toBe(1); });\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "reallyDead" in stale     # uncalled generator's callee NOT over-rooted: flagged


def test_expression_shaped_value_does_not_mint_unrooted_object_methods(tmp_path):
    """#48 (panel round 11): a variable_declarator whose value is an EXPRESSION SHAPE wrapping an
    object (`const handlers = (init(), { mmm(){ h() } })`, chained/parenthesized assignment,
    ternary, IIFE) is a DEFERRED #75 case — the object's helper stays flagged (a pre-existing
    recall gap). The cardinal guard pinned here: the branch must NOT mint the object's member
    `mmm` as an unrooted, mis-qualed module-scope node, which would escalate the recall gap into
    a live-method-flagged-dead cardinal. So `mmm` must not appear as a node id at all."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "svc.ts": (
            "function h(){ return 1; }\n"
            "const handlers = (init(), { mmm(){ h(); } });\n"
            "export function dispatch(name){ return handlers[name](); }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        ids = set(store.all_node_ids())
        # No spurious unrooted method node for the expression-shaped object member.
        assert not any(i.split("::")[-1].split(".")[-1] == "mmm" for i in ids), \
            "object member of an expression-shaped value must not be minted as a bare node"


def test_const_class_expression_is_modeled(tmp_path):
    """#80 (cardinal): `export const Widget = class extends Base { render(){ helper() } }` — a
    class expression bound to a const. The variable_declarator branch handled arrow/fn/object
    values but not `class`, so the class was never a node and its methods' callees were flagged
    dead. Now modeled as a CLASS (mirroring the assignment_expression class branch): INHERITS
    edges, body walked, `exported` role rescuing public methods (private stay dead-eligible).
    Parity with a regular `class W {}` — exported+consumed → clean; a genuinely-dead private
    method still flags."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "w.ts": (
            "export class Base {}\n"
            "function tok(){ return 1; }\n"
            "function deadHelper(){ return 2; }\n"
            "export const Widget = class extends Base {\n"
            "  render(){ tok(); }\n"
            "  _priv(){ deadHelper(); }\n"
            "};\n"
        ),
        "u.ts": "import { Widget } from './w'; new Widget().render();\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "tok" not in stale         # reached from the public class method: live
        assert "Base" not in stale        # extends base referenced via INHERITS: live
        assert "deadHelper" in stale      # reached only from an unused private method: flagged


def test_const_class_expression_nested_in_function_is_gated(tmp_path):
    """#80: a `const X = class {…}` inside a function body gates its methods to the class
    (chain enclosing-fn → class → methods); live when the fn is reachable, all dead when it
    isn't (no orphaned-but-flagged methods, no dead-initializer live roots)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    _mk(tmp_path, {
        "live.ts": (
            "export function f(){ const W = class { render(){ h(); } }; return new W(); }\n"
            "function h(){ return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "h" not in stale            # reachable: f -> W.render -> h: live
    # Distinct symbol names + a separate subdir so the live file above can't by-name-collide
    # (`new W()` resolving across files is the cardinal-SAFE #71 over-rooting family, unrelated).
    dead_dir = tmp_path / "deadpkg"
    _mk(dead_dir, {
        "dead.ts": (
            "function deadFactory(){ const Renderer = class { paint(){ secretPaint(); } };"
            " return new Renderer(); }\n"
            "function secretPaint(){ return 1; }\n"
        ),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(dead_dir))
        stale = {c["id"].split("::")[-1].split(".")[-1] for c in sg.find_stale(store).result}
        assert "deadFactory" in stale       # never called: dead
        assert "secretPaint" in stale       # reached only via a method of a dead-gated class: dead
