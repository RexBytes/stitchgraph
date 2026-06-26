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
from collections.abc import Callable, Iterable

from .model import Edge, Relation
from .store import Store

# Relations that propagate "liveness" — used by reachability / dead-code.
# EMITS/HANDLES carry liveness across a pub/sub boundary: if an emit is reachable,
# the event's handlers can run, so they must not be flagged dead (precision over
# recall). Omitting them flagged live event handlers stale — a symmetry gap with
# the other decoupled edges (ROUTES_TO/RENDERS) that already propagate liveness.
LIVENESS_RELATIONS = (Relation.CALLS, Relation.IMPORTS, Relation.INHERITS,
                      Relation.ROUTES_TO, Relation.REFERENCES, Relation.RENDERS,
                      Relation.EMITS, Relation.HANDLES)


def _adjacency(store: Store, relations: Iterable[Relation],
               edge_filter: Callable[[Edge], bool] | None = None) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    nodes = set(store.all_node_ids())  # ignore edges to a non-existent target (a precise
    # edge dangling after its file's deletion) — matches GraphBLAS (algebra.py), panel R29A.
    if edge_filter is None:
        # Hot path: stream lean tuples, never materialising the full Edge list (the 16M-edge
        # find_stale OOM on Home Assistant, v2.1).
        rels = {r.value for r in relations}
        for src, rel, dst, _w in store.iter_resolved():
            if rel in rels and dst in nodes:
                adj[src].append(dst)
    else:
        # edge_filter inspects fields beyond (src,rel,dst) (e.g. provenance), so it needs the
        # full Edge; this is the rarer EXTRACTED-only path.
        rels = set(relations)
        for edge in store.resolved_edges():
            if edge.relation in rels and edge.dst_id in nodes and edge_filter(edge):
                adj[edge.src].append(edge.dst_id)
    return adj


def _graphblas():
    """Return the GraphBLAS algebra module if available, else None."""
    try:
        from . import algebra
        return algebra if algebra.HAS_GRAPHBLAS else None
    except Exception:  # noqa: BLE001
        return None


def reachable_from(store: Store, seeds: Iterable[str],
                   relations: Iterable[Relation] = LIVENESS_RELATIONS,
                   edge_filter: Callable[[Edge], bool] | None = None) -> set[str]:
    """Forward closure: every node reachable from any seed.

    Uses the GraphBLAS sweep when available (design §2b), else this pure-Python
    frontier BFS — identical results, the BFS is the reference implementation.

    `edge_filter` restricts which edges propagate liveness (e.g. EXTRACTED-only,
    to tell a certain path from an inferred one); it forces the pure-Python sweep
    since GraphBLAS works on the raw relation matrices.
    """
    gb = _graphblas()
    if gb is not None and edge_filter is None:
        return gb.reachable_from(store, seeds, relations)
    adj = _adjacency(store, relations, edge_filter)
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
    rels = {r.value for r in relations}
    nodes = set(store.all_node_ids())  # ignore edges to a non-existent target (panel R29A)
    for src, rel, dst, _w in store.iter_resolved():
        if rel in rels and dst in nodes:
            radj[dst].append(src)
    return radj


def reverse_reachable_from(store: Store, targets: Iterable[str],
                           relations: Iterable[Relation] = LIVENESS_RELATIONS) -> set[str]:
    """Backward closure: every node that can transitively reach a target.

    This is the blast radius for impact_of (design §6.B): who depends on X.
    """
    gb = _graphblas()
    if gb is not None:
        return gb.reverse_reachable_from(store, targets, relations)
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
    _old_limit = sys.getrecursionlimit()  # restore in finally (panel QQQ LOW: don't leak
    sys.setrecursionlimit(max(10000, len(nodes) * 4 + 1000))  # a raised limit to the host)

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

    try:
        for v in nodes:
            if v not in index:
                strongconnect(v)
    finally:
        sys.setrecursionlimit(_old_limit)
    # Keep only genuine cycles: multi-node components or self-loops.
    _rels = {r.value for r in relations}
    self_loops = {src for src, rel, dst, _w in store.iter_resolved()
                  if dst == src and rel in _rels}
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

    rels = {r.value for r in relations} if relations is not None else None
    nodes = set(store.all_node_ids())  # ignore edges to a non-existent target (panel R29A)
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for src, rel, dst, weight in store.iter_resolved():
        if dst not in nodes:
            continue
        if rels is not None and rel not in rels:
            continue
        w = min(max(weight, 1e-9), 1.0)
        adj[src].append((dst, w))

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
    rels = {r.value for r in relations}
    nodes = set(store.all_node_ids())  # ignore edges to a non-existent target (panel R29A)
    for _src, rel, dst, _w in store.iter_resolved():
        if rel in rels and dst in nodes:
            counts[dst] += 1
    return counts


def fan_out(store: Store, relations: Iterable[Relation] = (Relation.CALLS,)) -> dict[str, int]:
    """Direct out-degree per node (callees) — half of the god-object signal."""
    counts: dict[str, int] = defaultdict(int)
    rels = {r.value for r in relations}
    nodes = set(store.all_node_ids())  # ignore edges to a non-existent target (panel R29A)
    for src, rel, dst, _w in store.iter_resolved():
        if rel in rels and dst in nodes:
            counts[src] += 1
    return counts
