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
