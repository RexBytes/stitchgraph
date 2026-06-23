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
    bad.write_text('{"files": {"pkg/m.py": {"executed_lines": ["oops", 1, null]}}}')
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        res = sg.ingest_trace(store, str(bad))  # must not raise
        assert res.ok


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
