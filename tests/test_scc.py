"""Direct unit tests for the shared Tarjan SCC primitive (core/_scc.py).

Extracted in v2.3.0 from reach.py / dataloop.py. Those call sites cover it end-to-end, but a
shared primitive deserves first-class tests that pin its contract directly (panel R149, sonnet NIT):
component identity, seed-driven coverage, and — the load-bearing safety property — that the
temporary recursion-limit raise is always restored, including on an exception mid-walk.
"""
from __future__ import annotations

import collections
import sys

import pytest

from stitchgraph.core._scc import tarjan_scc


def _components(adj, seeds=None, node_count=None):
    seeds = list(adj) if seeds is None else seeds
    node_count = len(adj) if node_count is None else node_count
    return {frozenset(c) for c in tarjan_scc(adj, seeds, node_count)}


def test_empty():
    assert tarjan_scc({}, [], 0) == []


def test_single_node_no_edges():
    assert _components({"a": []}) == {frozenset({"a"})}


def test_self_loop_is_a_component():
    assert _components({"a": ["a"]}) == {frozenset({"a"})}


def test_acyclic_chain_is_all_singletons():
    assert _components({"a": ["b"], "b": ["c"], "c": []}) == {
        frozenset({"a"}), frozenset({"b"}), frozenset({"c"})}


def test_three_cycle_is_one_component():
    assert _components({"a": ["b"], "b": ["c"], "c": ["a"]}) == {frozenset({"a", "b", "c"})}


def test_two_independent_cycles():
    adj = {"a": ["b"], "b": ["a"], "x": ["y"], "y": ["x"]}
    assert _components(adj) == {frozenset({"a", "b"}), frozenset({"x", "y"})}


def test_destination_only_node_visited_via_recursion():
    # 'b' is never a key / never a seed; it must still surface as its own component.
    assert _components({"a": ["b"]}, seeds=["a"]) == {frozenset({"a"}), frozenset({"b"})}


def test_seed_not_in_adj_handled():
    # adj.get(...) fallback — a seed with no outgoing edges is a lone component, no crash.
    assert _components({"a": ["a"]}, seeds=["a", "z"], node_count=2) == {
        frozenset({"a"}), frozenset({"z"})}


def test_reverse_topological_order():
    # Tarjan emits components in reverse-topological order: a sink before its predecessor.
    comps = tarjan_scc({"a": ["b"], "b": ["c"], "c": []}, ["a", "b", "c"], 3)
    assert comps == [["c"], ["b"], ["a"]]


def test_cross_edge_to_finished_scc_does_not_merge():
    # 'd' is seeded AFTER the {a,b} SCC has finished and popped; its cross-edge d->a points
    # back into a node that is visited but NO LONGER on the stack. The `elif w in on_stack`
    # guard must ignore it so d stays its own component. A mutant dropping that guard would
    # falsely lower d's low-link and silently drop d from the output (panel R150, sonnet F1).
    adj = {"a": ["b"], "b": ["a"], "d": ["a"]}
    comps = {frozenset(c) for c in tarjan_scc(adj, ["a", "b", "d"], 3)}
    assert comps == {frozenset({"a", "b"}), frozenset({"d"})}


def test_dag_cross_edge_keeps_all_singletons():
    # Simpler variant: a cross-edge into an already-finished singleton must not merge them.
    adj = {"c": ["a"], "a": ["b"], "b": []}
    comps = {frozenset(c) for c in tarjan_scc(adj, ["a", "b", "c"], 3)}
    assert comps == {frozenset({"a"}), frozenset({"b"}), frozenset({"c"})}


def test_defaultdict_adj_not_mutated():
    # dataloop passes a defaultdict; the helper must read it only via adj.get(...) and never
    # trigger __missing__ key insertion (panel R150, opus minor gap). Keys stay exactly as built.
    adj: dict[str, list[str]] = collections.defaultdict(list, {"a": ["b"], "b": ["a"]})
    before = set(adj)
    tarjan_scc(adj, ["a", "b"], 2)
    assert set(adj) == before  # 'b' lookups via .get did not insert phantom keys


def test_recursion_limit_restored_after_normal_call():
    orig = sys.getrecursionlimit()
    tarjan_scc({"a": ["b"], "b": []}, ["a", "b"], 100_000)  # would raise the limit mid-call
    assert sys.getrecursionlimit() == orig


def test_recursion_limit_restored_on_exception():
    orig = sys.getrecursionlimit()

    def exploding_seeds():
        yield "a"
        raise ValueError("boom")

    with pytest.raises(ValueError):
        tarjan_scc({"a": ["a"]}, exploding_seeds(), 100_000)
    assert sys.getrecursionlimit() == orig  # finally restored it despite the exception


def test_deep_chain_no_recursionerror():
    n = 5000
    adj = {str(i): [str(i + 1)] for i in range(n)}
    adj[str(n)] = []
    comps = tarjan_scc(adj, [str(i) for i in range(n + 1)], n + 1)
    assert len(comps) == n + 1  # acyclic -> all singletons, no RecursionError
