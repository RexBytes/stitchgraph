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
from collections.abc import Callable, Iterable, Iterator

from ._scc import tarjan_scc
from .model import Edge, Provenance, Relation
from .store import Store

_EXTRACTED = Provenance.EXTRACTED

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
        # full Edge — but streamed one at a time, never the whole list: scan's EXTRACTED-only
        # sweep through resolved_edges() was a second O(edges) Edge-object peak that
        # MemoryError'd at 6 GB on Home Assistant (field analysis 2026-07-03).
        rels = set(relations)
        for edge in store.iter_resolved_full():
            if edge.relation in rels and edge.dst_id in nodes and edge_filter(edge):
                adj[edge.src].append(edge.dst_id)
    return adj


def _graphblas():
    """Return the GraphBLAS algebra module if available, else None."""
    from .purity import pure_mode
    if pure_mode():
        return None
    try:
        from . import algebra
        return algebra if algebra.HAS_GRAPHBLAS else None
    except Exception:  # noqa: BLE001
        return None


def reachable_from(store: Store, seeds: Iterable[str],
                   relations: Iterable[Relation] = LIVENESS_RELATIONS,
                   edge_filter: Callable[[Edge], bool] | None = None,
                   confident_only: bool = False) -> set[str]:
    """Forward closure: every node reachable from any seed.

    Dispatch, fastest first — all three produce identical results (the BFS is the
    reference implementation, the others are pinned to it by tests):
    1. the mmapped adjacency sidecar (adjcache.py) when fresh/buildable — it also
       handles `confident_only` natively via its packed provenance bitmask;
    2. the GraphBLAS sweep (design §2b) when installed;
    3. this pure-Python frontier BFS.

    `confident_only` restricts propagation to EXTRACTED edges (scan's certainty
    pass). `edge_filter` is the general hook for anything else (arbitrary
    per-Edge predicates); it forces the pure-Python sweep since neither the
    sidecar nor GraphBLAS can evaluate an opaque callable per edge.
    """
    if edge_filter is None:
        from .adjcache import load_cache
        cache = load_cache(store)
        if cache is not None:
            return cache.reachable(seeds, relations, confident_only)
        if confident_only:
            edge_filter = lambda e: e.provenance is _EXTRACTED  # noqa: E731
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


def reachable_from_many(store: Store, seed_groups: list[set[str]],
                        relations: Iterable[Relation] = LIVENESS_RELATIONS,
                        confident_only: bool = False) -> list[set[str]]:
    """Batch forward closures — one result set per seed group, identical to
    calling `reachable_from` per group (pinned by the differential test).
    With a fresh adjacency sidecar this is the bit-parallel BFS: 64 groups per
    fixed-point sweep (v3.39.0 — turns audit_graph's per-test closure loop
    from 31.6 min into roughly a minute on the HA field index). Without the
    sidecar it degrades to the sequential reference, correct at reference
    speed."""
    from .adjcache import load_cache
    cache = load_cache(store)
    if cache is not None:
        return cache.reachable_many(seed_groups, relations, confident_only)
    return [reachable_from(store, g, relations, confident_only=confident_only)
            for g in seed_groups]


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
    from .adjcache import load_cache
    cache = load_cache(store)
    if cache is not None:
        return cache.reverse_reachable(targets, relations)
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
    from .adjcache import load_cache
    nodes = store.all_node_ids()
    cache = load_cache(store)
    # An overlay-patched cache serves the BFS family only: the SCC/articulation
    # traversals read the raw base CSR (_filtered_csr) and would miss patched
    # rows — fall through to the reference path instead (v3.40.0).
    if cache is not None and not cache.has_overlay:
        out = cache.scc(nodes, relations)
        self_loops = cache.self_loops(relations)
    else:
        adj = _adjacency(store, relations)
        out = tarjan_scc(adj, nodes, len(nodes))
        _rels = {r.value for r in relations}
        self_loops = {src for src, rel, dst, _w in store.iter_resolved()
                      if dst == src and rel in _rels}
    # Keep only genuine cycles: multi-node components or self-loops.
    return [c for c in out if len(c) > 1 or (c and c[0] in self_loops)]


def articulation_points(store: Store,
                        relations: Iterable[Relation] = LIVENESS_RELATIONS,
                        ) -> dict[str, int]:
    """Cut vertices of the UNDIRECTED projection of the graph over `relations` — advisory
    structural criticality (design §6). A node is a *chokepoint* if removing it disconnects the
    graph; its value is the **blast radius**: how many nodes get cut off from the main body when it
    is removed. One Tarjan DFS pass (subtree sizes computed inline), O(V+E), deterministic
    (sorted adjacency), recursion-limit raised for deep graphs and restored in a `finally` (mirrors
    `_scc.tarjan_scc`). Advisory only — like SCC / PageRank it never feeds liveness.

    Blast radius is defined uniformly for the root and non-root case: when u is removed its component
    (of `comp_total` nodes) splits into pieces — each separating child subtree (a child v with
    `low[v] >= disc[u]`) plus, for a non-root u, the parent-side blob (everything else, size
    `comp_total - 1 - sum(child subtrees)`). The largest surviving piece is the 'main body'; the
    blast radius is what is cut off from it, `(comp_total - 1) - max(piece sizes)`. (For the root the
    parent side is empty, so this reduces to sum-of-children minus the largest child.)"""
    from .adjcache import load_cache
    cache = load_cache(store)
    if cache is not None and not cache.has_overlay:  # see SCC note (v3.40.0)
        return cache.articulation(relations)
    directed = _adjacency(store, relations)
    undirected: dict[str, set[str]] = defaultdict(set)
    for u, vs in directed.items():
        for v in vs:
            if u != v:
                undirected[u].add(v)
                undirected[v].add(u)
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    timer = [0]
    guarded: dict[str, int] = {}
    # (no recursion-limit raise: the DFS below is iterative — the old raise/restore pair was
    #  dead code from a recursive predecessor; review 2026-07-03, F11e)

    def dfs(root: str) -> None:
        # Iterative post-order DFS carrying (node, parent, child-iterator); subtree sizes and the
        # articulation test are resolved when a node's children are exhausted. Iterative (not
        # recursive) so a deep call graph can't overflow the stack even with the raised limit.
        disc[root] = low[root] = timer[0]
        timer[0] += 1
        size = {root: 1}
        sep: dict[str, list[int]] = defaultdict(list)  # node -> separated child-subtree sizes
        stack: list[tuple[str, str | None, Iterator[str]]] = [
            (root, None, iter(sorted(undirected[root])))]
        while stack:
            u, parent, it = stack[-1]
            advanced = False
            for v in it:
                if v not in disc:
                    disc[v] = low[v] = timer[0]
                    timer[0] += 1
                    size[v] = 1
                    stack.append((v, u, iter(sorted(undirected[v]))))
                    advanced = True
                    break
                elif v != parent:
                    low[u] = min(low[u], disc[v])
            if advanced:
                continue
            stack.pop()
            if parent is not None:
                low[parent] = min(low[parent], low[u])
                size[parent] += size[u]
                # u's parent-side is cut off from u's subtree iff u can't climb above parent. At the
                # DFS root disc[parent] is the global minimum, so EVERY root child qualifies — hence
                # `sep[root]` collects all the root's child-subtree sizes (handled specially below).
                if low[u] >= disc[parent]:
                    sep[parent].append(size[u])
        comp_total = size[root]  # nodes in this connected component (the whole DFS tree)
        for u, sizes in sep.items():
            if u == root:
                # the root is an articulation point iff it has >1 DFS child; on removal the pieces
                # are exactly its child subtrees, and the largest stays as the 'main' body.
                if len(sizes) > 1:
                    guarded[root] = (comp_total - 1) - max(sizes)
            else:
                # non-root: pieces on removal are the separating child subtrees + the parent-side
                # blob (everything else). Blast = total-remaining minus the largest surviving piece
                # — the same 'cut off from the main body' definition as the root case (R263 fix: the
                # old `sum(sizes)` wrongly assumed the parent side is always the main body, which
                # inverted the ranking whenever a child subtree was larger than the parent side).
                parent_side = comp_total - 1 - sum(sizes)
                guarded[u] = (comp_total - 1) - max([*sizes, parent_side])

    for start in sorted(undirected):
        if start not in disc:
            dfs(start)
    return {u: g for u, g in guarded.items() if g > 0}


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
    from .adjcache import load_cache
    cache = load_cache(store)
    if cache is not None:
        return cache.fan_in(relations)
    counts: dict[str, int] = defaultdict(int)
    rels = {r.value for r in relations}
    nodes = set(store.all_node_ids())  # ignore edges to a non-existent target (panel R29A)
    for _src, rel, dst, _w in store.iter_resolved():
        if rel in rels and dst in nodes:
            counts[dst] += 1
    return counts


def fan_out(store: Store, relations: Iterable[Relation] = (Relation.CALLS,)) -> dict[str, int]:
    """Direct out-degree per node (callees) — half of the god-object signal."""
    from .adjcache import load_cache
    cache = load_cache(store)
    if cache is not None:
        return cache.fan_out(relations)
    counts: dict[str, int] = defaultdict(int)
    rels = {r.value for r in relations}
    nodes = set(store.all_node_ids())  # ignore edges to a non-existent target (panel R29A)
    for src, rel, dst, _w in store.iter_resolved():
        if rel in rels and dst in nodes:
            counts[src] += 1
    return counts
