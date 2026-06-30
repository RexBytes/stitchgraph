"""Structural diff between two indexes — the graph-diff oracle (design: research/graphdiff).

Given two built indexes, locate where their graphs differ. Two layers:

  * call-level — node identities (kind + name) and edges keyed by (src, relation, dst). In
    ``mode="id"`` this is exact (same-codebase: did a refactor change the graph? does the actual
    match the plan?); in ``mode="leaf"`` names are reduced to their last component so the shapes
    of two *different* codebases (e.g. a translation) can be compared — advisory only, since the
    prior research (research/README §2) shows raw topology tracks the extractor across languages.

  * body-level (Python + JS/TS/TSX + Go, ``body=True``) — for functions present in BOTH sides,
    compare their structural fingerprint (`structure.py` for Python, `structure_js.py` for the JS
    family, `structure_go.py` for Go). Catches a function whose *implementation* changed even when
    its name and call edges did not — the plan-vs-actual / translation-fidelity signal. (JS/Go
    bodies need the tree-sitter extra; without it that layer simply contributes nothing.)

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


def _id_fingerprints(store: Store) -> dict[str, collections.Counter[str]]:
    """{node-id -> structural fingerprint} for stored Python AND JS/TS/TSX AND Go functions/methods.
    Keyed by the FULL node id (`path::qualname#disamb`, path relative to the indexed root) — NOT the
    bare qualname — so two files defining the same name (`helper`, `__init__`, …) don't collide and
    silently drop a real body change. Two separately-indexed trees with the same internal layout
    (plan vs actual) still line up, because node paths are root-relative and therefore identical.
    A node id maps to exactly one file (one language), so the body comparison in `graph_diff` only
    ever compares same-language fingerprints — never the not-comparable cross-language case."""
    out = dict(similar._python_fn_fingerprints(store))
    out.update(similar._js_fn_fingerprints(store))
    out.update(similar._go_fn_fingerprints(store))
    return out


def graph_diff(store_a: Store, store_b: Store, mode: str = "id",
               body: bool = True, body_threshold: float = 0.95) -> dict:
    """Structural diff of two indexes. Returns a dict with located node/edge deltas and (when
    ``body`` and there are Python, JS/TS/TSX, or Go functions in common) the functions whose body
    shape diverged."""
    na, nb = _node_keys(store_a, mode), _node_keys(store_b, mode)
    ea, eb = _edge_keys(store_a, mode), _edge_keys(store_b, mode)
    nodes_only_a, nodes_only_b = _counter_delta(na, nb)
    edges_only_a, edges_only_b = _counter_delta(ea, eb)

    body_changed: list[dict] = []
    if body:
        fa, fb = _id_fingerprints(store_a), _id_fingerprints(store_b)
        for nid in sorted(set(fa) & set(fb)):
            # Identical fingerprints are unchanged — skip BEFORE the similarity guard. This avoids
            # the stub trap: a `pass`-only body has an EMPTY fingerprint and similarity(empty,
            # empty)==0.0 (zero-norm), which would otherwise flag an unchanged stub as "changed"
            # against itself. (`...`/docstring-only bodies have a tiny CONST fingerprint, not empty,
            # but this equality pre-check handles every unchanged function the same way regardless.)
            if fa[nid] == fb[nid]:
                continue
            sim = structure.similarity(fa[nid], fb[nid])
            if sim < body_threshold:
                name = nid.partition("::")[2].split("#", 1)[0]
                body_changed.append({"name": name, "similarity": round(sim, 3)})

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
