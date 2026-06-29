"""Data-loop detection (design §6.F, 🟡).

A *data* loop is a feedback cycle through mutable state — distinct from a *call*
cycle (recursion / circular deps, which `scan` already finds via CALLS SCCs).
The classic case: a function reads a global, computes from it, and writes it back
(an accumulator), so the value depends on itself across calls.

Built over a graph that mixes control and data flow:
  - CALLS  : func -> func   (as-is)
  - WRITES : func -> var     (writer reaches the state)
  - READS  : var  -> func    (state reaches the reader — READS reversed)
A strongly-connected component spanning a Variable node is a data loop.
"""

from __future__ import annotations

from collections import defaultdict

from ._scc import tarjan_scc
from .model import NodeKind, Relation
from .store import Store


def find_data_loops(store: Store) -> list[list[str]]:
    """Return data-feedback cycles, each as a list of node ids (incl. the var)."""
    var_ids = {n.id for n in store.nodes_by_kind(NodeKind.VARIABLE)}
    if not var_ids:
        return []

    adj: dict[str, list[str]] = defaultdict(list)
    for e in store.resolved_edges():
        if e.dst_id is None:
            continue
        if e.relation == Relation.CALLS:
            adj[e.src].append(e.dst_id)
        elif e.relation == Relation.WRITES and e.dst_id in var_ids:
            adj[e.src].append(e.dst_id)          # writer -> state
        elif e.relation == Relation.READS and e.dst_id in var_ids:
            adj[e.dst_id].append(e.src)          # state -> reader (reversed)

    return [comp for comp in _tarjan(adj)
            if len(comp) > 1 and any(n in var_ids for n in comp)]


def _tarjan(adj: dict[str, list[str]]) -> list[list[str]]:
    # Seed the SCC walk from the adjacency keys (every other node is reached by recursion);
    # size the recursion-limit raise by the full node population (keys + destinations).
    nodes = list(adj.keys()) + [d for ds in adj.values() for d in ds]
    return tarjan_scc(adj, list(adj.keys()), len(nodes))
