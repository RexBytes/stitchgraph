"""Fixes from the LLM field review (docs/LLM_REVIEW.md, 2026-07-07): the
confident-empty guard on get_callers/get_callees, and the AUTO LSP default
(best available analysis runs when a server is installed)."""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

import stitchgraph as sg
from stitchgraph.core.operations import get_callees, get_callers


def _index(tmp_path, files):
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    for rel, content in files.items():
        (root / rel).write_text(textwrap.dedent(content))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    return store


# -- the confident-empty guard -------------------------------------------------
def test_no_callers_but_referenced_is_not_confident(tmp_path):
    """The review's verify_path case: nine macro-wrapped uses arrived as
    REFERENCES and `get-callers` answered a CONFIDENT empty — the one place
    the envelope claimed certainty it did not have. An empty CALLS answer
    with other incoming edges must be needs_review with the counts shown."""
    store = _index(tmp_path, {"m.py": """
        def handler(event):
            return event

        HANDLERS = {"x": handler}

        def dispatch(name, event):
            return HANDLERS[name](event)
    """})
    res = get_callers(store, "handler")
    assert res.ok
    assert res.result == []                      # still honestly no CALLS
    assert res.needs_review, "empty-but-referenced must not be confident"
    assert res.confidence <= 0.6
    assert res.meta["non_call_uses"].get("REFERENCES", 0) >= 1
    assert any("do NOT treat it as unused" in r for r in res.review_reasons)
    store.close()


def test_genuinely_unused_keeps_confident_empty(tmp_path):
    """The guard must not water down the honest case: nothing touches the
    symbol -> the confident empty stands."""
    store = _index(tmp_path, {"m.py": """
        def totally_unused(x):
            return x

        def main():
            return 1
    """})
    res = get_callers(store, "totally_unused")
    assert res.ok and res.result == []
    assert not res.needs_review
    assert res.confidence == 1.0
    assert "non_call_uses" not in res.meta
    store.close()


def test_no_callees_but_outgoing_refs_annotated(tmp_path):
    store = _index(tmp_path, {"m.py": """
        class Config:
            pass

        def annotated(x: "Config"):
            return x
    """})
    res = get_callees(store, "annotated")
    assert res.ok and res.result == []
    assert res.needs_review
    assert res.meta["non_call_uses"].get("REFERENCES", 0) >= 1
    store.close()


# -- the AUTO LSP default --------------------------------------------------------
FAKE = Path(__file__).parent / "fake_lsp_server.py"


def _ts_root(tmp_path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "util.ts").write_text(
        'export function greet(name: string): string { return "hello " + name; }\n')
    (root / "src" / "other.ts").write_text(
        'export function greet(name: string): string { return "howdy " + name; }\n')
    (root / "src" / "main.ts").write_text(textwrap.dedent("""\
        import { greet } from "./util";

        export function run(): string {
            return greet("world");
        }
    """))
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({
        "shape": "location_list",
        "definitions": {"src/main.ts:3:11":
                        {"rel": "src/util.ts", "line": 0, "char": 16}}}))
    # [lsp.servers] override only — no `enabled` key, so the mode is AUTO
    (root / "stitchgraph.toml").write_text(textwrap.dedent(f"""\
        [lsp.servers]
        ".ts" = '''{sys.executable} {FAKE} {answers}'''
    """))
    return root


def test_auto_runs_lsp_when_server_available(tmp_path, monkeypatch):
    """v3.48.0 default: no flag, no `enabled` key — the pass runs because the
    server binary exists (best available analysis by default)."""
    pytest.importorskip("tree_sitter_language_pack")
    monkeypatch.delenv("STITCHGRAPH_NO_LSP", raising=False)
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(_ts_root(tmp_path)))
    assert res.ok
    assert sum(s.get("resolved", 0) for s in res.result.get("lsp", {}).values()) == 1
    assert any(e.source == "lsp" for e in store.resolved_edges())
    # AUTO must not turn expected fallbacks into review reasons
    assert not any("declined" in r for r in res.review_reasons)
    store.close()


def test_env_kill_switch_disables_auto(tmp_path, monkeypatch):
    pytest.importorskip("tree_sitter_language_pack")
    monkeypatch.setenv("STITCHGRAPH_NO_LSP", "1")
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(_ts_root(tmp_path)))
    assert res.ok
    assert "lsp" not in (res.result or {})
    assert not any(e.source == "lsp" for e in store.resolved_edges())
    store.close()


def test_explicit_param_overrides_kill_switch(tmp_path, monkeypatch):
    pytest.importorskip("tree_sitter_language_pack")
    monkeypatch.setenv("STITCHGRAPH_NO_LSP", "1")
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(_ts_root(tmp_path)), lsp=True)
    assert res.ok
    assert any(e.source == "lsp" for e in store.resolved_edges())
    store.close()


def test_incremental_keeps_lsp_edges(tmp_path, monkeypatch):
    """Adversarial self-audit find (docs/BUG_HUNT_PROMPT.md class 5): under
    the AUTO default a fresh index carries source="lsp" edges, so a watch
    edit going through reindex_incremental must NOT silently strip them from
    the edited file — incremental == fresh, including the LSP pass."""
    pytest.importorskip("tree_sitter_language_pack")
    from stitchgraph.core.operations import reindex_incremental
    monkeypatch.delenv("STITCHGRAPH_NO_LSP", raising=False)
    root = _ts_root(tmp_path)
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    assert any(e.source == "lsp" for e in store.resolved_edges())
    # edit the calling file (content change that keeps the call site line)
    main = root / "src" / "main.ts"
    main.write_text(main.read_text().replace(
        'return greet("world");', 'return greet("world!");'))
    assert reindex_incremental(store, str(root), {"src/main.ts"}).ok
    kept = [e for e in store.resolved_edges()
            if e.source == "lsp" and e.src == "src/main.ts::run"]
    assert kept, "the edited file lost its LSP edges on the incremental path"
    twin = sg.Store(str(tmp_path / "twin.db"))
    assert sg.reindex(twin, str(root)).ok

    def rows(s):
        return sorted((e.src, e.relation.value, e.dst_id, e.provenance.value,
                       e.source) for e in s.resolved_edges())
    assert rows(store) == rows(twin), "incremental drifted from fresh under AUTO"
    twin.close()
    store.close()


def test_config_false_disables_auto(tmp_path, monkeypatch):
    pytest.importorskip("tree_sitter_language_pack")
    monkeypatch.delenv("STITCHGRAPH_NO_LSP", raising=False)
    root = _ts_root(tmp_path)
    servers_table = (root / "stitchgraph.toml").read_text()
    (root / "stitchgraph.toml").write_text(
        "[lsp]\nenabled = false\n" + servers_table)
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(root))
    assert res.ok
    assert not any(e.source == "lsp" for e in store.resolved_edges())
    store.close()
