"""Reachability over the graph (design §6.B, §13.2).

This is the frontier-BFS pattern that GraphBLAS implements as masked SpMV. For
M0 it's a pure-Python adjacency BFS so the core stays stdlib-only; the contract
(seed set -> reachable set) is identical, so swapping in python-graphblas/LAGraph
later (M2/M3, the `algebra` extra) is a drop-in replacement, not a rewrite.

Cycle convergence (design §13.2): boolean reachability reaches a fixed point;
under (max, x) with weights <= 1 products only shrink, so SCCs do not blow up.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from .model import Relation
from .store import Store

# Relations that propagate "liveness" — used by reachability / dead-code.
LIVENESS_RELATIONS = (Relation.CALLS, Relation.IMPORTS, Relation.INHERITS,
                      Relation.ROUTES_TO, Relation.REFERENCES, Relation.RENDERS)


def _adjacency(store: Store, relations: Iterable[Relation]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    rels = set(relations)
    for edge in store.resolved_edges():
        if edge.relation in rels and edge.dst_id is not None:
            adj[edge.src].append(edge.dst_id)
    return adj


def reachable_from(store: Store, seeds: Iterable[str],
                   relations: Iterable[Relation] = LIVENESS_RELATIONS) -> set[str]:
    """Forward closure: every node reachable from any seed."""
    adj = _adjacency(store, relations)
    seen: set[str] = set()
    frontier = deque(s for s in seeds if store.get_node(s) is not None)
    seen.update(frontier)
    while frontier:
        node = frontier.popleft()
        for nxt in adj.get(node, ()):  # next frontier (the SpMV step)
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def fan_in(store: Store, relations: Iterable[Relation] = LIVENESS_RELATIONS) -> dict[str, int]:
    """Direct in-degree per node — the cheap proxy for 'read these first' hubs.

    (Transitive fan-in / PageRank is the GraphBLAS upgrade, design §6.A.)
    """
    counts: dict[str, int] = defaultdict(int)
    rels = set(relations)
    for edge in store.resolved_edges():
        if edge.relation in rels and edge.dst_id is not None:
            counts[edge.dst_id] += 1
    return counts
