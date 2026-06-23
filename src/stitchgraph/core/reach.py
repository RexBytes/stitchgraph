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


def _reverse_adjacency(store: Store, relations: Iterable[Relation]) -> dict[str, list[str]]:
    radj: dict[str, list[str]] = defaultdict(list)
    rels = set(relations)
    for edge in store.resolved_edges():
        if edge.relation in rels and edge.dst_id is not None:
            radj[edge.dst_id].append(edge.src)
    return radj


def reverse_reachable_from(store: Store, targets: Iterable[str],
                           relations: Iterable[Relation] = LIVENESS_RELATIONS) -> set[str]:
    """Backward closure: every node that can transitively reach a target.

    This is the blast radius for impact_of (design §6.B): who depends on X.
    """
    radj = _reverse_adjacency(store, relations)
    seen: set[str] = set()
    frontier = deque(t for t in targets if store.get_node(t) is not None)
    seen.update(frontier)
    while frontier:
        node = frontier.popleft()
        for prev in radj.get(node, ()):
            if prev not in seen:
                seen.add(prev)
                frontier.append(prev)
    seen.difference_update(targets)  # blast radius excludes the target itself
    return seen


def strongly_connected_components(
    store: Store, relations: Iterable[Relation] = (Relation.CALLS, Relation.IMPORTS),
) -> list[list[str]]:
    """Tarjan SCC over the given relations. Components of size > 1 (or a self-loop)
    are cycles — circular dependencies / recursion (design §6.C/F)."""
    adj = _adjacency(store, relations)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = [0]
    out: list[list[str]] = []
    nodes = store.all_node_ids()

    import sys
    sys.setrecursionlimit(max(10000, len(nodes) * 4 + 1000))

    def strongconnect(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(comp)

    for v in nodes:
        if v not in index:
            strongconnect(v)
    # Keep only genuine cycles: multi-node components or self-loops.
    self_loops = {e.src for e in store.resolved_edges()
                  if e.dst_id == e.src and e.relation in set(relations)}
    return [c for c in out if len(c) > 1 or (c and c[0] in self_loops)]


def best_path(store: Store, source: str, sink: str,
              relations: Iterable[Relation] | None = None) -> tuple[list[str], float] | None:
    """Highest-confidence path source -> sink under the (max, x) semiring
    (design §13.2): path confidence is the product of edge weights; best wins.

    Implemented as Dijkstra on -log(weight) (weights in (0,1] -> non-negative
    costs). Returns (node path, propagated confidence) or None if unreachable.
    """
    import heapq
    import math

    rels = set(relations) if relations is not None else None
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for edge in store.resolved_edges():
        if edge.dst_id is None:
            continue
        if rels is not None and edge.relation not in rels:
            continue
        w = min(max(edge.weight, 1e-9), 1.0)
        adj[edge.src].append((edge.dst_id, w))

    if store.get_node(source) is None or store.get_node(sink) is None:
        return None

    best_cost: dict[str, float] = {source: 0.0}
    prev: dict[str, str] = {}
    pq: list[tuple[float, str]] = [(0.0, source)]
    while pq:
        cost, node = heapq.heappop(pq)
        if node == sink:
            path = [sink]
            while path[-1] != source:
                path.append(prev[path[-1]])
            path.reverse()
            return path, math.exp(-cost)
        if cost > best_cost.get(node, math.inf):
            continue
        for nxt, w in adj.get(node, ()):
            ncost = cost - math.log(w)
            if ncost < best_cost.get(nxt, math.inf):
                best_cost[nxt] = ncost
                prev[nxt] = node
                heapq.heappush(pq, (ncost, nxt))
    return None


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


def fan_out(store: Store, relations: Iterable[Relation] = (Relation.CALLS,)) -> dict[str, int]:
    """Direct out-degree per node (callees) — half of the god-object signal."""
    counts: dict[str, int] = defaultdict(int)
    rels = set(relations)
    for edge in store.resolved_edges():
        if edge.relation in rels and edge.dst_id is not None:
            counts[edge.src] += 1
    return counts
