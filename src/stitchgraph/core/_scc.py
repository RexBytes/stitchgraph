"""Tarjan strongly-connected-components — shared core.

Extracted in v2.3.0: the SCC algorithm was duplicated verbatim in `reach.py` (call / import
cycles) and `dataloop.py` (data-feedback loops). The two call sites differ only in how they
build the adjacency and what they do with the result — the iterative-vs-recursive *core* is
identical — so it lives here once.

`tarjan_scc` is behaviour-preserving: same component output, same iteration order (driven by the
caller-supplied `seeds`), same recursion-limit handling (raised for deep graphs, restored in a
`finally` so a raised limit never leaks to the host — panel QQQ LOW).
"""
from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence


def tarjan_scc(
    adj: Mapping[str, Sequence[str]], seeds: Iterable[str], node_count: int,
) -> list[list[str]]:
    """Tarjan's SCC over `adj`, visiting `seeds` in order. `node_count` sizes the temporary
    recursion-limit raise for deep graphs. Returns components in reverse-topological order."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = [0]
    out: list[list[str]] = []

    _old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(10000, node_count * 4 + 1000))

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
        for v in seeds:
            if v not in index:
                strongconnect(v)
    finally:
        sys.setrecursionlimit(_old_limit)
    return out
