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
