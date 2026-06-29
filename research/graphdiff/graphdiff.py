"""graph-diff oracle — research prototype (shared primitive for questions #2 and #3).

A structural-equivalence diff between two stitchgraph indexes. Two modes:

  * id mode    — compare node *identities* (kind + qualified name) and edges keyed by
                 (src-name, relation, dst-name). For same-codebase / same-language
                 comparisons: "did this refactor change the graph?", "does the plan match
                 the actual?". Exact, high-precision.

  * leaf mode  — compare *shapes*: node kinds + per-kind leaf-name multisets, and edges
                 keyed by (relation, src-leaf, dst-leaf). For cross-language comparisons
                 (#2, translate rust->js), where qualified names and module paths differ
                 but the call/def shape should survive. Lower precision by design — the §2
                 finding warns that raw topology tracks the *extractor*, so leaf-mode diff
                 is an ORACLE (does B preserve A's shape?), never a generator.

The diff is symmetric and located: every delta carries the node/edge it concerns so a
human/LLM can act on it. This is the "matrix as oracle, not generator" thesis made concrete:
the LLM writes the translation/refactor; this primitive *verifies* the structure survived.

Usage:
    from graphdiff import index, diff, summarize
    a = index("path/to/repo_a"); b = index("path/to/repo_b")
    print(summarize(diff(a, b, mode="id")))
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

import stitchgraph as sg


def _leaf(name: str | None) -> str:
    return name.rsplit(".", 1)[-1].rsplit("::", 1)[-1] if name else ""


def index(path: str) -> dict:
    """Index a path in memory and pull node/edge rows into a plain dict snapshot."""
    store = sg.Store(":memory:")
    sg.reindex(store, path)
    nodes = {
        r[0]: {"kind": r[1], "name": r[2], "file": r[3]}
        for r in store.conn.execute("select id,kind,name,file from nodes")
    }
    edges = []
    for src, rel, dst_id, dst_sym in store.conn.execute(
        "select src,relation,dst_id,dst_symbol from edges"
    ):
        edges.append((src, rel, dst_id, dst_sym))
    store.close()
    return {"path": path, "nodes": nodes, "edges": edges}


# --- key functions: what counts as "the same" in each mode ------------------------

def _node_keys(snap: dict, mode: str) -> collections.Counter:
    out: collections.Counter = collections.Counter()
    for n in snap["nodes"].values():
        if mode == "id":
            out[(n["kind"], n["name"])] += 1
        else:  # leaf
            out[(n["kind"], _leaf(n["name"]))] += 1
    return out


def _edge_keys(snap: dict, mode: str) -> collections.Counter:
    nodes = snap["nodes"]
    out: collections.Counter = collections.Counter()
    for src, rel, dst_id, dst_sym in snap["edges"]:
        s = nodes.get(src, {}).get("name", src)
        d = nodes[dst_id]["name"] if dst_id in nodes else (dst_sym or "")
        if mode == "id":
            out[(s, rel, d)] += 1
        else:  # leaf
            out[(_leaf(s), rel, _leaf(d))] += 1
    return out


@dataclass
class Diff:
    mode: str
    nodes_only_a: list = field(default_factory=list)
    nodes_only_b: list = field(default_factory=list)
    edges_only_a: list = field(default_factory=list)
    edges_only_b: list = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.nodes_only_a or self.nodes_only_b
                    or self.edges_only_a or self.edges_only_b)


def _counter_diff(ca: collections.Counter, cb: collections.Counter):
    """Return (only-in-a, only-in-b) as flat lists, honouring multiplicity."""
    only_a = list((ca - cb).elements())
    only_b = list((cb - ca).elements())
    return sorted(map(str, only_a)), sorted(map(str, only_b))


def diff(a: dict, b: dict, mode: str = "id") -> Diff:
    na, nb = _node_keys(a, mode), _node_keys(b, mode)
    ea, eb = _edge_keys(a, mode), _edge_keys(b, mode)
    d = Diff(mode=mode)
    d.nodes_only_a, d.nodes_only_b = _counter_diff(na, nb)
    d.edges_only_a, d.edges_only_b = _counter_diff(ea, eb)
    return d


def summarize(d: Diff, limit: int = 12) -> str:
    lines = [f"graph-diff (mode={d.mode}): "
             + ("STRUCTURALLY EQUIVALENT ✓" if d.is_empty else "DELTAS FOUND")]

    def block(title, items):
        lines.append(f"  {title}: {len(items)}")
        for it in items[:limit]:
            lines.append(f"      - {it}")
        if len(items) > limit:
            lines.append(f"      … +{len(items) - limit} more")

    block("nodes only in A", d.nodes_only_a)
    block("nodes only in B", d.nodes_only_b)
    block("edges only in A", d.edges_only_a)
    block("edges only in B", d.edges_only_b)
    return "\n".join(lines)
