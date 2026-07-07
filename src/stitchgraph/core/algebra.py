"""Derived sparse-matrix layer over the adjacency store (design §2b / §13.2).

Adjacency lists are the source of truth; this module derives relation matrices on
demand and runs the whole-graph sweeps — reachability and centrality — as
GraphBLAS semiring operations (frontier SpMV, never dense powers; design §7
traps). This is the regime where the matrix design genuinely beats per-node
traversal: dead-code (all − reachable), blast radius (reverse reachable), and
global hub ranking over the entire graph at once.

Optional dependency (`pip install 'stitchgraph[algebra]'`). When python-graphblas
isn't present, `HAS_GRAPHBLAS` is False and callers fall back to the pure-Python
frontier BFS in `reach.py` — identical results, just not accelerated.
"""

from __future__ import annotations

from collections.abc import Iterable

from .model import Relation
from .store import Store

try:
    import graphblas as gb
    from graphblas import Matrix, Vector, semiring
    HAS_GRAPHBLAS = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_GRAPHBLAS = False

from .reach import LIVENESS_RELATIONS


class _Adjacency:
    """A derived boolean adjacency matrix plus the node<->index mapping.

    `confident_only` builds from EXTRACTED edges only — the hub-ranking variant
    (v3.32.0): a homonym's AMBIGUOUS widening arms are resolution artifacts, not
    dependency mass, and at field scale they drown the centrality metrics exactly
    as they drowned direct fan-in before `confident_fan_in` (v3.29.0). Liveness
    sweeps stay RAW on purpose — an ambiguous edge must keep its target alive
    (precision-over-recall: never flag possibly-live code dead)."""

    def __init__(self, store: Store, relations: Iterable[Relation],
                 confident_only: bool = False) -> None:
        rels = {r.value for r in relations}  # compare against raw stored strings (see iter_resolved)
        self.ids = store.all_node_ids()
        self.index = {nid: i for i, nid in enumerate(self.ids)}
        index = self.index
        rows: list[int] = []
        cols: list[int] = []
        weights: list[float] = []
        # Stream lean (src, relation, dst, weight) tuples instead of materialising every Edge
        # object: a 16M-edge graph builds these three int/float columns directly, never a 16M-
        # element Edge list (which OOM'd find_stale on Home Assistant before v2.1).
        for src, rel, dst, weight in store.iter_resolved(confident_only=confident_only):
            if rel in rels and dst in index and src in index:
                rows.append(index[src])
                cols.append(index[dst])
                weights.append(min(max(weight, 0.0), 1.0))
        self.n = len(self.ids)
        self.rows, self.cols, self.weights = rows, cols, weights

    def boolean(self):
        if not self.rows:
            return Matrix(bool, max(self.n, 1), max(self.n, 1))
        return Matrix.from_coo(self.rows, self.cols, [True] * len(self.rows),
                               nrows=self.n, ncols=self.n, dtype=bool,
                               dup_op=gb.binary.lor)


def reachable_from(store: Store, seeds: Iterable[str],
                   relations: Iterable[Relation] = LIVENESS_RELATIONS) -> set[str]:
    """Forward reachability via GraphBLAS frontier BFS (the dead-code sweep)."""
    adj = _Adjacency(store, relations)
    return _bfs(adj, seeds, transpose=False)


def reverse_reachable_from(store: Store, targets: Iterable[str],
                           relations: Iterable[Relation] = LIVENESS_RELATIONS) -> set[str]:
    """Backward reachability (blast radius) — same sweep on the transpose."""
    adj = _Adjacency(store, relations)
    seen = _bfs(adj, targets, transpose=True)
    seen.difference_update(targets)
    return seen


def _bfs(adj: _Adjacency, seeds: Iterable[str], transpose: bool) -> set[str]:
    seed_ix = [adj.index[s] for s in seeds if s in adj.index]
    if not seed_ix or adj.n == 0:
        return set()
    A = adj.boolean()
    if transpose:
        A = A.T.new()
    visited = Vector(bool, adj.n)
    frontier = Vector(bool, adj.n)
    for i in seed_ix:
        frontier[i] = True
    visited(frontier.S) << True
    while frontier.nvals:
        nxt = frontier.vxm(A, semiring.any_pair[bool]).new(mask=~visited.S)
        frontier = nxt
        visited(frontier.S) << True
    idx = visited.to_coo()[0].tolist()
    return {adj.ids[i] for i in idx}


def transitive_fan_in(store: Store,
                      relations: Iterable[Relation] = LIVENESS_RELATIONS,
                      max_nodes: int = 4000,
                      confident_only: bool = True,
                      exclude_sources: set[str] | None = None) -> dict[str, int]:
    """For each node, how many *distinct* nodes can transitively reach it — the
    'most-depended-on, read these first' ranking (design §6.A).

    Computed as the boolean transitive closure (iterated one-step extension —
    `reach·A` per round, linear in graph diameter rather than the log-rounds of
    true repeated squaring; any_pair semiring, frontier-free but still sparse)
    then a column count. Bounded by `max_nodes`; above it, callers should fall
    back to direct fan-in (the closure densifies on big graphs).

    `confident_only` (default True since v3.32.0): rank over EXTRACTED edges only,
    matching `confident_fan_in` — see _Adjacency's rationale.

    `exclude_sources` drops those node ids as DEPENDERS (closure rows) while
    keeping them as graph structure: `orient` passes the test set so a suite
    closing 1,117 stores doesn't crown `Store.close` the #1 hub (research/25).
    Excluded nodes still ROUTE reachability — a src helper reached only through
    tests keeps the sources that flow through it — they just aren't mass."""
    adj = _Adjacency(store, relations, confident_only=confident_only)
    if adj.n == 0 or adj.n > max_nodes or not adj.rows:
        return {}
    A = adj.boolean()
    reach = A.dup()
    while True:
        squared = reach.mxm(A, semiring.any_pair[bool]).new()
        combined = reach.ewise_add(squared, gb.monoid.lor).new()
        if combined.nvals == reach.nvals:
            break
        reach = combined
    # Drop the diagonal first: a node on a cycle (or a self-loop) reaches itself in the
    # closure, which would count the node as its own depender — inconsistent with
    # reverse_reachable_from/impact_of, which exclude self (panel R16B). We want distinct
    # *other* sources. Then cast bool->int (else `plus` on BOOL is OR, always 1) and count.
    offdiag = gb.select.offdiag(reach).new(dtype="INT64")
    if exclude_sources:
        kept = [i for i, nid in enumerate(adj.ids) if nid not in exclude_sources]
        if not kept:
            return {}
        offdiag = offdiag[kept, :].new()  # every column kept; only kept rows count
    counts = offdiag.reduce_columnwise(gb.monoid.plus).new()
    coo = counts.to_coo()
    return {adj.ids[i]: int(v) for i, v in zip(coo[0].tolist(), coo[1].tolist(), strict=False)}


def pagerank(store: Store, relations: Iterable[Relation] = LIVENESS_RELATIONS,
             damping: float = 0.85, iters: int = 40,
             confident_only: bool = True) -> dict[str, float]:
    """Transitive importance via PageRank — the 'read these first' hub ranking
    over the whole graph (design §6.A). GraphBLAS power iteration on the
    out-degree-normalised matrix. `confident_only` (default True since v3.32.0):
    rank over EXTRACTED edges only — see _Adjacency's rationale."""
    adj = _Adjacency(store, relations, confident_only=confident_only)
    if adj.n == 0 or not adj.rows:
        return {}
    n = adj.n
    A = Matrix.from_coo(adj.rows, adj.cols, [1.0] * len(adj.rows),
                        nrows=n, ncols=n, dtype="FP64", dup_op=gb.binary.plus)
    outdeg = A.reduce_rowwise(gb.monoid.plus).new()  # out-degree per source
    # Row-normalise: T[i,j] = A[i,j] / outdeg[i].
    inv = outdeg.apply(lambda x: 0.0 if x == 0 else 1.0 / x).new()
    T = _row_scale(A, inv, n)

    rank = Vector(float, n)
    rank[:] = 1.0 / n
    teleport = (1.0 - damping) / n
    for _ in range(iters):
        spread = rank.vxm(T, semiring.plus_times[float]).new()
        # Dense teleport base for every node, then accumulate the damped spread —
        # so nodes with no in-edges still receive the teleport mass (missing != 0).
        new = Vector(float, n)
        new[:] = teleport
        new(gb.binary.plus) << spread.apply(gb.binary.times, damping)
        rank = new
    scores = rank.to_coo()
    return {adj.ids[i]: float(v) for i, v in zip(scores[0].tolist(), scores[1].tolist(), strict=False)}


def _row_scale(A, inv_vec, n):
    """Return A with each row i scaled by inv_vec[i] (diag(inv) @ A)."""
    D = Matrix(float, n, n)
    coo = inv_vec.to_coo()
    for i, v in zip(coo[0].tolist(), coo[1].tolist(), strict=False):
        D[i, i] = v
    return D.mxm(A, semiring.plus_times[float]).new()
