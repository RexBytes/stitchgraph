"""Contracts introduced by the 2026-07-09 field review (Claude Opus 4.8 on
ant-node, stitchgraph 3.50.0): actionable LSP diagnostics, heuristic-cycle
suppression, the tiered/ranked/capped impact_of, stable review codes, bounded
MCP output, IDF mode labels, audit_graph id tolerance, and the find_hotspots
cross-lens convergence command. Each test pins a promise that review asked
for; the LSP diagnostics themselves are pinned in test_lsp.py."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

import stitchgraph as sg
from stitchgraph.core.envelope import Provenance, ReviewCode, ok, refuse


def _mk(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return root


# -- request 11: stable machine-readable review codes --------------------------
def test_low_confidence_result_carries_stable_code():
    r = ok([], confidence=0.3, provenance=Provenance.INFERRED)
    assert r.needs_review
    assert ReviewCode.LOW_CONFIDENCE.value in r.review_codes
    assert r.to_dict()["review_codes"] == r.review_codes


def test_ambiguous_provenance_carries_stable_code():
    r = ok([], confidence=1.0, provenance=Provenance.AMBIGUOUS)
    assert ReviewCode.AMBIGUOUS_PROVENANCE.value in r.review_codes


def test_refusal_carries_refused_code():
    r = refuse("nope")
    assert ReviewCode.REFUSED.value in r.review_codes


def test_needs_review_never_serializes_with_empty_codes():
    """The codes mirror of the reasons contract: a flagged result must always be
    machine-filterable, even when an op set needs_review post-construction
    without naming a code."""
    r = ok([], confidence=1.0)
    r.needs_review = True
    r.review_reasons.append("hand-flagged")
    d = r.to_dict()
    assert d["needs_review"] is True
    assert d["review_codes"] == [ReviewCode.UNSPECIFIED.value]


def test_add_reason_code_deduplicates():
    r = ok([], confidence=1.0)
    r.add_reason("a", code=ReviewCode.NAME_BASED_EDGE)
    r.add_reason("b", code=ReviewCode.NAME_BASED_EDGE)
    assert r.review_codes == [ReviewCode.NAME_BASED_EDGE.value]


# -- request 4/5: tiered, distance-ranked, capped impact_of --------------------
def test_impact_of_tiers_confident_vs_ambiguous(tmp_path):
    """A dependent reached only through a homonym name-bind lands in the
    `ambiguous` tier; a dependent on an unambiguous chain lands in `confident`.
    The single blob answer ("73% of the crate at 0.47") splits into an
    actionable tier and a verify-first tier."""
    _mk(tmp_path, {
        # unique chain: leaf <- mid <- top  (EXTRACTED edges)
        "chain.py": ("def leaf():\n    return 1\n"
                     "def mid():\n    return leaf()\n"
                     "def top():\n    return mid()\n"),
        # homonym: two `main`s make the callers' edges ambiguous
        "cli.py": 'def main():\n    return 0\nif __name__ == "__main__":\n    main()\n',
        "mcp.py": 'def main():\n    return 1\nif __name__ == "__main__":\n    main()\n',
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.impact_of(store, "chain.py::leaf")
        assert r.ok
        conf_ids = {e["id"] for e in r.result["confident"]}
        assert "chain.py::mid" in conf_ids and "chain.py::top" in conf_ids
        assert r.result["confident_count"] == len(conf_ids)
        # nearest-first: the direct caller ranks before the transitive one
        assert [e["id"] for e in r.result["confident"]][:2] == \
            ["chain.py::mid", "chain.py::top"]
        assert [e["distance"] for e in r.result["confident"]][:2] == [1, 2]

        amb = sg.impact_of(store, "mcp.py::main")
        amb_ids = {e["id"] for e in amb.result["ambiguous"]}
        assert amb.result["ambiguous_count"] == len(amb_ids) > 0
        assert ReviewCode.NAME_BASED_EDGE.value in amb.review_codes


def test_impact_of_caps_tier_lists_and_reports_truncation(tmp_path):
    files = {"hub.py": "def hub():\n    return 1\n"}
    for i in range(8):
        files[f"c{i}.py"] = f"from hub import hub\ndef caller{i}():\n    return hub()\n"
    _mk(tmp_path, files)
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.impact_of(store, "hub.py::hub", limit=3)
        assert r.ok
        assert len(r.result["confident"]) + len(r.result["ambiguous"]) <= 6
        total = r.result["confident_count"] + r.result["ambiguous_count"]
        assert total == r.result["count"] > 3
        assert "tiers_truncated" in r.meta
        # the flat list stays complete for compatibility
        assert len(r.result["blast_radius"]) == r.result["count"]


# -- request 9: bounded MCP output ---------------------------------------------
def test_mcp_bound_cuts_lists_and_reports_it(monkeypatch):
    from stitchgraph.adapters.mcp import _bound
    monkeypatch.delenv("STITCHGRAPH_MCP_MAX_ITEMS", raising=False)
    env = {"result": {"blast_radius": [f"n{i}" for i in range(250)], "count": 250},
           "alternatives": [], "meta": {}}
    out = _bound(env)
    assert len(out["result"]["blast_radius"]) == 100
    assert out["result"]["count"] == 250                    # scalars untouched
    assert out["meta"]["truncated"]["result.blast_radius"] == \
        {"shown": 100, "total": 250}
    assert "truncation_hint" in out["meta"]


def test_mcp_bound_handles_list_result_and_env_override(monkeypatch):
    from stitchgraph.adapters.mcp import _bound
    monkeypatch.setenv("STITCHGRAPH_MCP_MAX_ITEMS", "5")
    out = _bound({"result": list(range(9)), "alternatives": [], "meta": {}})
    assert out["result"] == [0, 1, 2, 3, 4]
    assert out["meta"]["truncated"]["result"] == {"shown": 5, "total": 9}
    monkeypatch.setenv("STITCHGRAPH_MCP_MAX_ITEMS", "0")   # 0 disables the bound
    out = _bound({"result": list(range(9)), "alternatives": [], "meta": {}})
    assert out["result"] == list(range(9))
    assert "truncated" not in out["meta"]


def test_mcp_bound_leaves_small_payloads_untouched(monkeypatch):
    from stitchgraph.adapters.mcp import _bound
    monkeypatch.delenv("STITCHGRAPH_MCP_MAX_ITEMS", raising=False)
    env = {"result": {"items": [1, 2, 3]}, "alternatives": [], "meta": {}}
    assert _bound(env)["meta"] == {}


# -- request 12: find_hotspots (cross-lens convergence) ------------------------
def test_find_hotspots_refuses_without_any_second_lens(tmp_path):
    _mk(tmp_path, {"a.py": "def f():\n    return 1\n"})
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.find_hotspots(store)
        assert r.ok is False
        assert r.needs_review


def test_find_hotspots_converges_lenses(tmp_path):
    """A file that ranks high on static centrality AND behavioural centrality
    outranks a file that is central on one lens only — the fused list the
    review had to assemble by hand across four command outputs."""
    _mk(tmp_path, {
        "core.py": ("def api():\n    return util()\n"
                    "def util():\n    return 1\n"),
        "edge.py": "def rare():\n    return 2\n",
        "app.py": ("from core import api\nfrom edge import rare\n"
                   "def run():\n    return api() + rare()\n"),
    })
    cov = {
        "format": "stitchgraph-coverage-v1",
        "tests": {
            "tests/test_a.py::test_1": ["core.py::api", "core.py::util"],
            "tests/test_a.py::test_2": ["core.py::api", "core.py::util"],
            "tests/test_a.py::test_3": ["core.py::api"],
            "tests/test_a.py::test_4": ["edge.py::rare"],
        },
    }
    cov_path = tmp_path / "coverage_modes.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.find_hotspots(store, coverage=str(cov_path))
        assert r.ok
        spots = r.result["hotspots"]
        assert spots, "expected at least one converged hotspot"
        files = [h["file"] for h in spots]
        assert files[0] == "core.py"
        assert set(r.result["lenses"]) >= {"static_centrality", "behavioural_centrality"}
        for h in spots:
            assert set(h["lenses"]) <= set(r.result["lenses"])
            assert 0.0 <= h["score"] <= 1.0


# -- request 13: mode labels prefer distinctive tokens over boilerplate --------
def test_mode_labels_downweight_ubiquitous_tokens():
    """Plain TF made labels keyword salad: a token in EVERY function name
    (`handle`) out-counted the tokens that distinguish the mode. IDF weighting
    must rank the distinctive tokens first."""
    from stitchgraph.core.modes import _idf_label, _token_df
    funcs = [f"m.py::handle_{w}"
             for w in ("upload", "download", "login", "logout", "core")]
    gdf = _token_df(funcs)
    label = _idf_label(["m.py::handle_upload", "m.py::handle_download"],
                       gdf, len(funcs)).split()
    assert set(label[:2]) == {"upload", "download"}
    if "handle" in label:
        assert label.index("handle") > 1


# -- request 14: audit_graph tolerates path-prefix id drift --------------------
def test_audit_graph_suffix_matches_prefixed_coverage_ids(tmp_path):
    """The artifact was captured with a `crate/`-style path prefix the index
    doesn't have. find_modes consumes it happily; audit_graph must not refuse —
    it suffix-matches (basename + qualname) and reports the remap."""
    pytest.importorskip("numpy")
    _mk(tmp_path, {
        "app.py": "def work():\n    return 1\n",
        "tests/test_app.py": ("from app import work\n"
                              "def test_work():\n    assert work() == 1\n"),
    })
    cov = {
        "format": "stitchgraph-coverage-v1",
        "tests": {  # note the sandbox/ prefix on BOTH test and function ids
            "sandbox/tests/test_app.py::test_work": ["sandbox/app.py::work"],
        },
    }
    cov_path = tmp_path / "coverage_modes.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.audit_graph(store, coverage=str(cov_path))
        assert r.ok, r.review_reasons
        assert r.result["tests_audited"] == 1
        assert r.meta.get("ids_remapped", 0) >= 1
        assert ReviewCode.COVERAGE_MISMATCH.value in r.review_codes
