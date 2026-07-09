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


def test_hedged_code_requires_a_non_empty_payload():
    """An EMPTY payload is not 'an answer worth keeping': refuse(result=[])
    (find_stale with zero candidates and no entry points) must code REFUSED,
    or a consumer following 'HEDGED_RESULT = don't discard' keeps an empty
    advisory instead of retrying with explicit roots (self-review round 2)."""
    empty = refuse("no entry points, zero candidates", result=[], confidence=0.1)
    assert ReviewCode.REFUSED.value in empty.review_codes
    assert ReviewCode.HEDGED_RESULT.value not in empty.review_codes


def test_to_dict_self_heals_cleared_codes():
    """Belt AND suspenders: the __setattr__ hook only watches the
    `needs_review` name, so clearing review_codes on an already-flagged
    Result must be healed at the serialization chokepoint — the envelope
    self-protects exactly as it does for non-finite confidence (self-review
    round 2, reproduced live)."""
    r = ok([], confidence=1.0)
    r.needs_review = True
    r.review_codes.clear()
    r.review_reasons.clear()
    d = r.to_dict()
    assert d["review_codes"] == [ReviewCode.UNSPECIFIED.value]
    assert d["review_reasons"] != []


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


@pytest.fixture()
def _hub_store(tmp_path):
    """hub + two direct callers: radius = {c0, c1}, |radius incl. target| = 3,
    induced liveness rows = 4 (2 CALLS + 2 module-level IMPORTS)."""
    _mk(tmp_path, {
        "hub.py": "def hub():\n    return 1\n",
        "c0.py": "from hub import hub\ndef caller0():\n    return hub()\n",
        "c1.py": "from hub import hub\ndef caller1():\n    return hub()\n",
    })
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    yield store
    store.close()


def test_impact_of_node_cap_boundary(_hub_store, monkeypatch):
    """CONTRIBUTING rule 4 at the NODE cap: |radius| (dependents + target,
    derived from the baseline) exactly AT the cap keeps distances; one below
    loses them, degrades to id order, and says so in meta — degradation is
    never silent."""
    import stitchgraph.core.operations as ops
    baseline = sg.impact_of(_hub_store, "hub.py::hub")
    n_radius = baseline.result["count"] + 1                # dependents + target
    monkeypatch.setattr(ops, "_IMPACT_DETAIL_CAP", n_radius)   # exactly AT
    at_cap = sg.impact_of(_hub_store, "hub.py::hub")
    assert all("distance" in e for e in at_cap.result["confident"])
    assert "distances_skipped" not in at_cap.meta
    monkeypatch.setattr(ops, "_IMPACT_DETAIL_CAP", n_radius - 1)  # one below
    over = sg.impact_of(_hub_store, "hub.py::hub")
    assert over.result["confident"] and \
        all("distance" not in e for e in over.result["confident"])
    assert over.meta["distances_skipped"] == "node_cap"


def test_impact_of_edge_budget_boundary(_hub_store, monkeypatch):
    """CONTRIBUTING rule 4 at the EDGE budget: exactly N induced rows pass;
    N-1 abandons the detail pass mid-stream with the reason in meta."""
    import stitchgraph.core.operations as ops
    # measure the fixture's true induced-row count with an ample budget
    baseline = sg.impact_of(_hub_store, "hub.py::hub")
    assert all("distance" in e for e in baseline.result["confident"])
    n_rows = sum(len(v) for v in _radj_of(_hub_store, baseline).values())
    assert n_rows >= 2
    monkeypatch.setattr(ops, "_IMPACT_DETAIL_EDGE_CAP", n_rows)   # exactly N
    at_cap = sg.impact_of(_hub_store, "hub.py::hub")
    assert all("distance" in e for e in at_cap.result["confident"])
    assert "distances_skipped" not in at_cap.meta
    monkeypatch.setattr(ops, "_IMPACT_DETAIL_EDGE_CAP", n_rows - 1)  # one below
    over = sg.impact_of(_hub_store, "hub.py::hub")
    assert over.result["confident"] and \
        all("distance" not in e for e in over.result["confident"])
    assert over.meta["distances_skipped"] == "edge_budget"


def _radj_of(store, res) -> dict[str, list[str]]:
    """Recompute the radius-induced reverse adjacency exactly as impact_of does,
    so the edge-budget boundary test derives N instead of hardcoding it."""
    import stitchgraph.core.operations as ops
    deps = set(res.result["blast_radius"])
    radius = deps | {res.result["symbol"]}
    liveness = {r.value for r in ops.LIVENESS_RELATIONS}
    radj: dict[str, list[str]] = {}
    for src, rel, dst, _p in store.conn.execute(
            "SELECT src, relation, dst_id, provenance FROM edges_all"):
        if rel in liveness and src in deps and dst in radius:
            radj.setdefault(dst, []).append(src)
    return radj


def test_impact_of_tests_to_run_is_ranked_nearest_first():
    """The tests_to_run promise (AGENTS.md: 'a truncated list keeps the most
    relevant entries'): a direct-caller test outranks a transitive one even
    when alphabetical order says otherwise."""
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation

    with sg.Store(":memory:") as store:
        store.add_node(Node(id="m.py::target", kind=NodeKind.FUNCTION,
                            name="target", location="m.py:1:0"))
        store.add_node(Node(id="m.py::mid", kind=NodeKind.FUNCTION,
                            name="mid", location="m.py:5:0"))
        # alphabetically test_aaa < test_zzz, but test_zzz is NEARER (direct)
        store.add_node(Node(id="tests/t.py::test_aaa", kind=NodeKind.FUNCTION,
                            name="test_aaa", location="tests/t.py:1:0",
                            roles=frozenset({"test"})))
        store.add_node(Node(id="tests/t.py::test_zzz", kind=NodeKind.FUNCTION,
                            name="test_zzz", location="tests/t.py:5:0",
                            roles=frozenset({"test"})))
        for src, dst in (("m.py::mid", "m.py::target"),
                         ("tests/t.py::test_aaa", "m.py::mid"),
                         ("tests/t.py::test_zzz", "m.py::target")):
            store.add_edge(Edge(src=src, dst_id=dst, dst_symbol=dst.split("::")[-1],
                                relation=Relation.CALLS,
                                provenance=Provenance.EXTRACTED))
        r = sg.impact_of(store, "m.py::target")
        assert r.result["tests_to_run"] == \
            ["tests/t.py::test_zzz", "tests/t.py::test_aaa"]


def test_impact_of_redundant_ambiguous_edge_keeps_inferred_provenance():
    """Provenance reflects the edges backing the AMBIGUOUS TIER: a redundant
    AMBIGUOUS edge onto a confident-tier dependent must not flip the envelope
    to AMBIGUOUS when every ambiguous-tier route is INFERRED-only."""
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation

    with sg.Store(":memory:") as store:
        for n in ("target", "a", "b"):
            store.add_node(Node(id=f"m.py::{n}", kind=NodeKind.FUNCTION,
                                name=n, location="m.py:1:0"))
        store.add_edge(Edge(src="m.py::a", dst_id="m.py::target",
                            dst_symbol="target", relation=Relation.CALLS,
                            provenance=Provenance.EXTRACTED))
        # redundant name-based edge onto the CONFIDENT dependent `a`
        store.add_edge(Edge(src="m.py::a", dst_id="m.py::target",
                            dst_symbol="target", relation=Relation.REFERENCES,
                            provenance=Provenance.AMBIGUOUS))
        # `b` reaches target only through an INFERRED edge -> ambiguous tier
        store.add_edge(Edge(src="m.py::b", dst_id="m.py::a",
                            dst_symbol="a", relation=Relation.REFERENCES,
                            provenance=Provenance.INFERRED))
        r = sg.impact_of(store, "m.py::target")
        assert {e["id"] for e in r.result["ambiguous"]} == {"m.py::b"}
        assert r.needs_review is True
        assert r.provenance is Provenance.INFERRED   # NOT ambiguous


def test_find_chokepoints_coerces_bool_limit():
    """limit=True must fall back to the default like every other op — bool is
    an int subtype, and the unconverted copy returned exactly 1 chokepoint."""
    from stitchgraph.core.model import Edge, Node, NodeKind, Relation

    with sg.Store(":memory:") as store:
        for n in "abcdefgh":
            store.add_node(Node(id=f"m.py::{n}", kind=NodeKind.FUNCTION,
                                name=n, location="m.py:1:0"))
        for u, v in (("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"),
                     ("e", "f"), ("e", "g"), ("e", "h")):
            store.add_edge(Edge(src=f"m.py::{u}", dst_id=f"m.py::{v}",
                                dst_symbol=v, relation=Relation.CALLS))
        default = sg.find_chokepoints(store)
        boolean = sg.find_chokepoints(store, limit=True)
        assert len(boolean.result) == len(default.result) > 1


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


def test_mcp_bound_cuts_matrix_payloads_consistently(monkeypatch):
    """get_matrix's labels/cells are index-correlated: cutting them
    independently silently corrupts the matrix, and a blanket exemption
    reopened the unbounded-blob hole (the op's own bound is the
    caller-supplied `limit`, which has no ceiling). The correlated cut trims
    labels to the budget and keeps only cells whose endpoints survive —
    every remaining index still names the right label."""
    from stitchgraph.adapters.mcp import _bound
    monkeypatch.setenv("STITCHGRAPH_MCP_MAX_ITEMS", "5")
    labels = [f"fn{i}" for i in range(20)]
    cells = [{"src": i, "dst": (i + 1) % 20, "w": 1.0} for i in range(20)]
    env = {"result": {"labels": list(labels), "cells": list(cells), "n": 20},
           "alternatives": [], "meta": {}}
    out = _bound(env, "get_matrix")
    kept_labels = out["result"]["labels"]
    kept_cells = out["result"]["cells"]
    assert kept_labels == labels[:5]
    assert kept_cells and all(c["src"] < 5 and c["dst"] < 5 for c in kept_cells)
    # alignment: every surviving cell still points at the label it named before
    for c in kept_cells:
        assert kept_labels[c["src"]] == labels[c["src"]]
        assert kept_labels[c["dst"]] == labels[c["dst"]]
    assert out["meta"]["truncated"]["result.labels"] == {"shown": 5, "total": 20}
    assert out["meta"]["truncated"]["result.cells"]["total"] == 20
    # a matrix already within budget passes through untouched
    small = {"result": {"labels": labels[:3], "cells": cells[:3], "n": 3},
             "alternatives": [], "meta": {}}
    assert "truncated" not in _bound(small, "get_matrix")["meta"]


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


# -- dogfood finding (round 2): test-owned stubs are doubles, not debt ---------
def test_scan_test_owned_stub_is_green_advisory(tmp_path):
    """A deliberately-empty fake in a test file (a stub server's run(), a mock
    body) must never be the repo's loudest finding: scan applies the same
    test-ownership principle to stubs that it applies to god objects. Found by
    dogfooding stitchgraph on itself — the sole RED was a test double."""
    _mk(tmp_path, {
        "app.py": ("from svc import serve\n"
                   "def main():\n    serve()\n"
                   'if __name__ == "__main__":\n    main()\n'),
        "svc.py": "def serve():\n    raise NotImplementedError\n",
        "tests/test_app.py": ("class FakeServer:\n"
                              "    def run(self):\n"
                              "        raise NotImplementedError\n"
                              "def test_main():\n"
                              "    FakeServer().run()\n"),
    })
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        issues = {i["node"]: i for i in sg.scan(store).result
                  if i["kind"] in ("stub", "live_stub")}
        prod = issues["svc.py::serve"]
        assert prod["kind"] == "live_stub"            # real product debt stays loud
        assert prod["urgency"] in ("red", "orange")
        fake = issues["tests/test_app.py::FakeServer.run"]
        assert fake["urgency"] == "green"             # a double, not debt
        assert "test-owned" in fake["reason"]


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


def test_drift_reconciliation_reaches_every_coverage_join(tmp_path):
    """The drift is a property of the ARTIFACT, not of one op: with a
    prefix-drifted artifact, select_tests/co_change/find_gaps used to emit
    confident WRONG diagnoses (STATIC_ONLY 'coverage may predate them',
    COVERAGE_ABSENT 'never executed', everything 'untested') while audit_graph
    — one function away — proved the rows exist. Reconciliation now happens at
    the shared load boundary, so all of them agree and all annotate the remap."""
    _mk(tmp_path, {
        "app.py": "def work():\n    return 1\n",
        "tests/test_app.py": ("from app import work\n"
                              "def test_work():\n    assert work() == 1\n"),
    })
    cov = {
        "format": "stitchgraph-coverage-v1",
        "tests": {  # sandbox/ prefix on every id — the capture-kit drift case
            "sandbox/tests/test_app.py::test_work": ["sandbox/app.py::work"],
            "sandbox/tests/test_app.py::test_more": ["sandbox/app.py::work"],
        },
    }
    cov_path = tmp_path / "coverage_modes.json"
    cov_path.write_text(json.dumps(cov))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))

        st = sg.select_tests(store, "app.py::work", coverage=str(cov_path))
        assert st.ok
        assert "tests/test_app.py::test_work" in st.result["ran_it"]
        assert ReviewCode.STATIC_ONLY.value not in st.review_codes
        assert st.meta.get("ids_remapped", 0) >= 1
        assert ReviewCode.COVERAGE_MISMATCH.value in st.review_codes

        gaps = sg.find_gaps(store, coverage=str(cov_path))
        assert gaps.ok
        assert "app.py::work" not in gaps.result["untested_live"]
        assert gaps.meta.get("ids_remapped", 0) >= 1

        cc = sg.co_change(store, "tests/test_app.py::test_work",
                          coverage=str(cov_path))
        assert cc.ok
        assert cc.result["covers"] == ["app.py::work"]
        assert ReviewCode.COVERAGE_ABSENT.value not in cc.review_codes
        assert cc.meta.get("ids_remapped", 0) >= 1


def test_reconcile_preserves_param_and_phase_suffixes():
    """Test-id remap rewrites only the PATH component: pytest [param] and
    coverage.py |phase tails survive, so per-row granularity is intact."""
    from stitchgraph.core import coverage_query

    cov = {"sandbox/tests/t.py::test_a[x|y]|run": ["sandbox/app.py::work"],
           "sandbox/tests/t.py::test_a[z]|setup": ["sandbox/app.py::work"]}
    nodes = {"tests/t.py::test_a", "app.py::work"}
    out, n = coverage_query.reconcile(cov, nodes)
    assert n == 2   # one test base + one function id
    assert set(out) == {"tests/t.py::test_a[x|y]|run",
                        "tests/t.py::test_a[z]|setup"}
    assert all(fs == ["app.py::work"] for fs in out.values())
    # nothing to remap -> the SAME mapping comes back, zero count
    clean = {"tests/t.py::test_a": ["app.py::work"]}
    same, zero = coverage_query.reconcile(clean, nodes)
    assert same is clean and zero == 0


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
