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
    calls, writes, reads = Relation.CALLS.value, Relation.WRITES.value, Relation.READS.value
    # streamed tuples, not materialized Edge objects (review 2026-07-03, F11a)
    for src, rel, dst_id, _w in store.iter_resolved():
        if rel == calls:
            adj[src].append(dst_id)
        elif rel == writes and dst_id in var_ids:
            adj[src].append(dst_id)              # writer -> state
        elif rel == reads and dst_id in var_ids:
            adj[dst_id].append(src)              # state -> reader (reversed)

    return [comp for comp in _tarjan(adj)
            if len(comp) > 1 and any(n in var_ids for n in comp)]


def _tarjan(adj: dict[str, list[str]]) -> list[list[str]]:
    # Seed the SCC walk from the adjacency keys (every other node is reached by recursion);
    # size the recursion-limit raise by the full node population (keys + destinations).
    nodes = list(adj.keys()) + [d for ds in adj.values() for d in ds]
    return tarjan_scc(adj, list(adj.keys()), len(nodes))
