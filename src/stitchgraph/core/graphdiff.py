"""Structural diff between two indexes — the graph-diff oracle (design: research/graphdiff).

Given two built indexes, locate where their graphs differ. Two layers:

  * call-level — node identities (kind + name) and edges keyed by (src, relation, dst). In
    ``mode="id"`` this is exact (same-codebase: did a refactor change the graph? does the actual
    match the plan?); in ``mode="leaf"`` names are reduced to their last component so the shapes
    of two *different* codebases (e.g. a translation) can be compared — advisory only, since the
    prior research (research/README §2) shows raw topology tracks the extractor across languages.

  * body-level (Python only, ``body=True``) — for functions present in BOTH sides, compare their
    structural fingerprint (`structure.py`). Catches a function whose *implementation* changed even
    when its name and call edges did not — the plan-vs-actual / translation-fidelity signal.

Advisory and read-only: it reports located deltas for a human/LLM to act on; it never edits source
and never feeds `find_stale`.
"""
from __future__ import annotations

import collections

from . import similar, structure
from .store import Store

_CTOR_ALIASES = {"__init__", "constructor", "__construct", "initialize"}


def _leaf(name: str) -> str:
    if not name:
        return ""
    leaf = name.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
    return "<init>" if leaf in _CTOR_ALIASES else leaf


def _node_keys(store: Store, mode: str) -> collections.Counter[tuple[str, str]]:
    out: collections.Counter[tuple[str, str]] = collections.Counter()
    for n in store.all_nodes_full():
        nm = n.name if mode == "id" else _leaf(n.name)
        out[(n.kind.value, nm)] += 1
    return out


def _edge_keys(store: Store, mode: str) -> collections.Counter[tuple[str, str, str]]:
    names = {n.id: n.name for n in store.all_nodes_full()}
    out: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for e in store.resolved_edges():
        rel = getattr(e.relation, "value", str(e.relation))
        src = names.get(e.src) or e.src
        resolved = names.get(e.dst_id) if e.dst_id else None
        dst = resolved or e.dst_symbol or ""
        if mode == "id":
            out[(src, rel, dst)] += 1
        else:
            out[(_leaf(src), rel, _leaf(dst))] += 1
    return out


def _counter_delta(ca: collections.Counter, cb: collections.Counter) -> tuple[list[str], list[str]]:
    only_a = sorted(str(k) for k in (ca - cb).elements())
    only_b = sorted(str(k) for k in (cb - ca).elements())
    return only_a, only_b


def _qual_fingerprints(store: Store) -> dict[str, collections.Counter[str]]:
    """{qualname -> structural fingerprint} for stored Python functions/methods. Matching by
    qualname (not full id) lets a renamed-path build still line up for body comparison."""
    out: dict[str, collections.Counter[str]] = {}
    for node_id, fp in similar._python_fn_fingerprints(store):
        qual = node_id.partition("::")[2].split("#", 1)[0]
        out[qual] = fp
    return out


def graph_diff(store_a: Store, store_b: Store, mode: str = "id",
               body: bool = True, body_threshold: float = 0.95) -> dict:
    """Structural diff of two indexes. Returns a dict with located node/edge deltas and (when
    ``body`` and there are Python functions in common) the functions whose body shape diverged."""
    na, nb = _node_keys(store_a, mode), _node_keys(store_b, mode)
    ea, eb = _edge_keys(store_a, mode), _edge_keys(store_b, mode)
    nodes_only_a, nodes_only_b = _counter_delta(na, nb)
    edges_only_a, edges_only_b = _counter_delta(ea, eb)

    body_changed: list[dict] = []
    if body:
        fa, fb = _qual_fingerprints(store_a), _qual_fingerprints(store_b)
        for qual in sorted(set(fa) & set(fb)):
            sim = structure.similarity(fa[qual], fb[qual])
            if sim < body_threshold:
                body_changed.append({"name": qual, "similarity": round(sim, 3)})

    equivalent = not (nodes_only_a or nodes_only_b or edges_only_a
                      or edges_only_b or body_changed)
    return {
        "mode": mode,
        "equivalent": equivalent,
        "nodes_only_a": nodes_only_a,
        "nodes_only_b": nodes_only_b,
        "edges_only_a": edges_only_a,
        "edges_only_b": edges_only_b,
        "body_changed": body_changed,
    }
