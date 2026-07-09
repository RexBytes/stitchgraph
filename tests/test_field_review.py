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
    machine-filterable — on the LIBRARY surface too, not only after to_dict —
    even when an op set needs_review post-construction without naming a code."""
    r = ok([], confidence=1.0)
    r.needs_review = True
    # the in-process object and the serialized envelope must agree
    assert r.review_codes == [ReviewCode.UNSPECIFIED.value]
    assert r.review_reasons != []
    d = r.to_dict()
    assert d["needs_review"] is True
    assert d["review_codes"] == [ReviewCode.UNSPECIFIED.value]


def test_specific_code_supersedes_unspecified_backfill():
    r = ok([], confidence=1.0)
    r.needs_review = True                                # backfills UNSPECIFIED
    r.add_reason("specific", code=ReviewCode.NAME_BASED_EDGE)
    assert r.review_codes == [ReviewCode.NAME_BASED_EDGE.value]


def test_add_reason_code_deduplicates():
    r = ok([], confidence=1.0)
    r.add_reason("a", code=ReviewCode.NAME_BASED_EDGE)
    r.add_reason("b", code=ReviewCode.NAME_BASED_EDGE)
    assert r.review_codes == [ReviewCode.NAME_BASED_EDGE.value]


def test_hedged_partial_result_is_not_coded_refused():
    """refuse(result=...) is the ok=True advisory-partial constructor
    (find_stale's no-entry-points candidates): a machine consumer treating
    REFUSED as 'no answer, retry differently' must not discard it."""
    hedged = refuse("hedged, but real candidates", result=[1, 2], confidence=0.5)
    assert hedged.ok is True
    assert ReviewCode.REFUSED.value not in hedged.review_codes
    assert ReviewCode.HEDGED_RESULT.value in hedged.review_codes
    hard = refuse("nothing to give")
    assert hard.ok is False
    assert ReviewCode.REFUSED.value in hard.review_codes


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


def test_impact_of_parallel_inferred_edge_does_not_hedge(tmp_path):
    """The demotion gate is the NODE-tier split, not the raw edge tally: a
    dependent with a confident route stays certain even when a redundant
    name-based edge also points at it — no more '0 of N dependents are
    ambiguous … verify the ambiguous tier' beside an empty ambiguous list."""
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation

    with sg.Store(":memory:") as store:
        for n in ("target", "caller"):
            store.add_node(Node(id=f"m.py::{n}", kind=NodeKind.FUNCTION,
                                name=n, location="m.py:1:0"))
        store.add_edge(Edge(src="m.py::caller", dst_id="m.py::target",
                            dst_symbol="target", relation=Relation.CALLS,
                            provenance=Provenance.EXTRACTED))
        # the redundant name-based edge that used to trip the edge-tally gate
        store.add_edge(Edge(src="m.py::caller", dst_id="m.py::target",
                            dst_symbol="target", relation=Relation.REFERENCES,
                            provenance=Provenance.INFERRED))
        r = sg.impact_of(store, "m.py::target")
        assert r.result["ambiguous"] == [] and r.result["ambiguous_count"] == 0
        assert r.needs_review is False
        assert r.provenance is Provenance.EXTRACTED


def test_impact_of_detail_caps_degrade_to_id_order(tmp_path, monkeypatch):
    """Just-above/below the detail caps (CONTRIBUTING rule 4): under the caps
    entries carry distances; past either the NODE cap or the induced-EDGE
    budget the tiers degrade to id order with no distance keys — the memory
    bound must actually bound."""
    import stitchgraph.core.operations as ops
    _mk(tmp_path, {
        "hub.py": "def hub():\n    return 1\n",
        "c0.py": "from hub import hub\ndef caller0():\n    return hub()\n",
        "c1.py": "from hub import hub\ndef caller1():\n    return hub()\n",
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        below = sg.impact_of(store, "hub.py::hub")
        assert all("distance" in e for e in below.result["confident"])
        monkeypatch.setattr(ops, "_IMPACT_DETAIL_CAP", 2)   # radius+target > 2
        above = sg.impact_of(store, "hub.py::hub")
        assert above.result["confident"] and \
            all("distance" not in e for e in above.result["confident"])
        monkeypatch.setattr(ops, "_IMPACT_DETAIL_CAP", 5_000)
        monkeypatch.setattr(ops, "_IMPACT_DETAIL_EDGE_CAP", 1)  # >1 induced edge
        edge_capped = sg.impact_of(store, "hub.py::hub")
        assert edge_capped.result["confident"] and \
            all("distance" not in e for e in edge_capped.result["confident"])


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


def test_mcp_bound_descends_into_nested_lists(monkeypatch):
    """A list nested inside a result item (a scan cycle's `members`) must not
    smuggle the blob the bound exists to prevent."""
    from stitchgraph.adapters.mcp import _bound
    monkeypatch.setenv("STITCHGRAPH_MCP_MAX_ITEMS", "5")
    env = {"result": [{"kind": "cycle", "members": [f"n{i}" for i in range(30)]}],
           "alternatives": [], "meta": {}}
    out = _bound(env)
    assert len(out["result"][0]["members"]) == 5
    assert out["meta"]["truncated"]["result[].members"] == {"shown": 5, "total": 30}


def test_mcp_bound_boundary_is_exact(monkeypatch):
    """N items pass uncut; N+1 are cut (CONTRIBUTING rule 4: a limit of N means
    nothing unless N passes and N+1 refuses)."""
    from stitchgraph.adapters.mcp import _bound
    monkeypatch.setenv("STITCHGRAPH_MCP_MAX_ITEMS", "5")
    exact = _bound({"result": list(range(5)), "alternatives": [], "meta": {}})
    assert exact["result"] == list(range(5)) and "truncated" not in exact["meta"]
    over = _bound({"result": list(range(6)), "alternatives": [], "meta": {}})
    assert over["result"] == list(range(5))
    assert over["meta"]["truncated"]["result"] == {"shown": 5, "total": 6}


def test_mcp_bound_exempts_index_correlated_payloads(monkeypatch):
    """get_matrix's labels/cells are index-correlated: cutting them
    independently silently corrupts the matrix, so the op is exempt (it is
    already self-bounded — it refuses broad scopes at the operation layer)."""
    from stitchgraph.adapters.mcp import _bound
    monkeypatch.setenv("STITCHGRAPH_MCP_MAX_ITEMS", "5")
    env = {"result": {"labels": list(range(20)), "cells": list(range(20))},
           "alternatives": [], "meta": {}}
    out = _bound(env, "get_matrix")
    assert len(out["result"]["labels"]) == 20 and len(out["result"]["cells"]) == 20
    assert "truncated" not in out["meta"]


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


def test_find_hotspots_excludes_test_mass(tmp_path):
    """Test files are hot on every lens by construction (top churn, credited by
    their own coverage rows, fan-in from every test) — that convergence is an
    artifact, not centrality, so they must not appear in the hotspot list
    (the research/25 exclusion orient applies, carried over)."""
    _mk(tmp_path, {
        "core.py": ("def api():\n    return util()\n"
                    "def util():\n    return 1\n"),
        "app.py": ("from core import api\n"
                   "def run():\n    return api()\n"),
        "tests/test_core.py": ("from core import api\n"
                               "def helper():\n    return api()\n"
                               "def test_one():\n    return helper()\n"
                               "def test_two():\n    return helper()\n"),
    })
    cov = {
        "format": "stitchgraph-coverage-v1",
        "tests": {  # every test credits its OWN file's functions too
            "tests/test_core.py::test_one":
                ["core.py::api", "core.py::util",
                 "tests/test_core.py::test_one", "tests/test_core.py::helper"],
            "tests/test_core.py::test_two":
                ["core.py::api",
                 "tests/test_core.py::test_two", "tests/test_core.py::helper"],
            "tests/test_core.py::test_three": ["app.py::run", "core.py::api"],
            "tests/test_core.py::test_four": ["core.py::util"],
        },
    }
    cov_path = tmp_path / "coverage_modes.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.find_hotspots(store, coverage=str(cov_path))
        assert r.ok
        files = [h["file"] for h in r.result["hotspots"]]
        assert files, "expected source hotspots"
        assert not any(f.startswith("tests/") for f in files), files


def test_find_hotspots_limit_boundary(tmp_path):
    """limit=N returns N; the full count stays in meta (CONTRIBUTING rule 4)."""
    files = {"m0.py": "def f0():\n    return 0\n"}
    cov_tests = {"tests/t.py::test_0": ["m0.py::f0"]}
    for i in range(1, 4):
        # a call chain so every file carries static mass, not just m0
        files[f"m{i}.py"] = (f"from m{i - 1} import f{i - 1}\n"
                             f"def f{i}():\n    return f{i - 1}() + {i}\n")
        cov_tests[f"tests/t.py::test_{i}"] = [f"m{i}.py::f{i}", "m0.py::f0"]
    files["main.py"] = "from m3 import f3\ndef main():\n    return f3()\n"
    _mk(tmp_path, files)
    cov_path = tmp_path / "coverage_modes.json"
    cov_path.write_text(json.dumps(
        {"format": "stitchgraph-coverage-v1", "tests": cov_tests}))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        full = sg.find_hotspots(store, coverage=str(cov_path))
        assert full.ok
        n = len(full.result["hotspots"])
        assert n >= 2
        capped = sg.find_hotspots(store, coverage=str(cov_path), limit=n - 1)
        assert len(capped.result["hotspots"]) == n - 1
        assert capped.meta["hotspots"] == n           # full count never hidden


def test_tied_percentiles_share_average_rank():
    """Ordinal ranks gave tied values arbitrary alphabetical percentiles —
    100 files at churn 1 spanned 0.01..0.99. Ties share their average rank."""
    from stitchgraph.core.operations import _tied_percentiles
    pct = _tied_percentiles({"a": 1.0, "b": 1.0, "c": 1.0, "d": 50.0})
    assert pct["a"] == pct["b"] == pct["c"] == 0.5   # mean of ranks 1..3 / 4
    assert pct["d"] == 1.0


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


def test_audit_graph_never_grafts_unaligned_paths(tmp_path):
    """A basename+symbol match alone must NOT remap: a stale/vendored id whose
    directory disagrees with the index's (neither path a whole-segment suffix
    of the other) stays unmatched — an honest refusal beats recall numbers
    computed against the wrong function."""
    pytest.importorskip("numpy")
    _mk(tmp_path, {
        "tools/utils.py": "def parse():\n    return 1\n",
        "tests/test_u.py": ("import sys\n"
                            "def test_parse():\n    assert True\n"),
    })
    cov = {
        "format": "stitchgraph-coverage-v1",
        "tests": {  # 'src/utils.py' does not align with the index's 'tools/utils.py'
            "src/tests/test_u.py::test_parse": ["src/utils.py::parse"],
        },
    }
    cov_path = tmp_path / "coverage_modes.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        r = sg.audit_graph(store, coverage=str(cov_path))
        # the test id aligns (suffix) but its only function must NOT be grafted
        # onto tools/utils.py::parse — so the row has no matching function and
        # the audit refuses rather than fabricating recall for the wrong node
        assert r.ok is False
        assert "no coverage row matched" in r.review_reasons[0]
