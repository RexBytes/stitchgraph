"""The public library API — the task-level operations (design §9).

These functions ARE the product surface. The CLI and MCP adapters are generated
from them (same name, same params, same docstring -> design §3 'obvious mapping');
the report composes them. Each returns a `Result` envelope.

Every operation takes the `Store` explicitly as its first argument so the library
is honest about what it touches. Operations are READ-ONLY on analyzed code — they
only read the index, never mutate source (design §4 read-only invariant).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass

from .entrypoints import EntryPointDetector, PythonLibraryDetector
from .envelope import Provenance, Result, Urgency, ok, refuse
from .model import NodeKind, Relation
from .reach import (
    best_path, fan_in, fan_out, reachable_from, reverse_reachable_from,
    strongly_connected_components,
)
from .store import Store

# --------------------------------------------------------------------------
# Operation registry — adapters iterate this to build their surfaces.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    name: str  # snake_case; CLI kebab-cases it, MCP uses it verbatim
    func: Callable[..., Result]
    summary: str

    def params(self) -> list[inspect.Parameter]:
        """Caller-facing params (everything after `store`)."""
        return list(inspect.signature(self.func).parameters.values())[1:]


_REGISTRY: dict[str, Operation] = {}


def operation(summary: str) -> Callable[[Callable[..., Result]], Callable[..., Result]]:
    def deco(func: Callable[..., Result]) -> Callable[..., Result]:
        _REGISTRY[func.__name__] = Operation(func.__name__, func, summary)
        return func
    return deco


def registry() -> list[Operation]:
    return list(_REGISTRY.values())


# --------------------------------------------------------------------------
# Structural primitives (design §6, layer 0/1) — backed directly by the store.
# --------------------------------------------------------------------------


@operation("Find definition(s) of a symbol by name.")
def find_symbol(store: Store, name: str) -> Result:
    """Find definition(s) of a symbol by name."""
    nodes = store.nodes_by_name(name)
    if not nodes:
        return refuse(f"no symbol named '{name}' in the index", confidence=0.0)
    primary, *rest = nodes
    prov = Provenance.EXTRACTED if len(nodes) == 1 else Provenance.AMBIGUOUS
    conf = 1.0 if len(nodes) == 1 else 0.5
    res = ok(primary.to_dict(), confidence=conf, provenance=prov)
    res.alternatives = [n.to_dict() for n in rest]
    if rest:
        res.add_reason(f"{len(nodes)} symbols share the name '{name}'")
    return res


@operation("Direct callers of a symbol.")
def get_callers(store: Store, name: str) -> Result:
    """Direct callers of a symbol."""
    target = _resolve_one(store, name)
    if target is None:
        return refuse(f"'{name}' is not a unique symbol in the index", confidence=0.0)
    edges = store.callers_of(target.id)
    callers = [{"src": e.src, "weight": round(e.weight, 3)} for e in edges]
    return ok(callers, symbol=target.id, count=len(callers))


@operation("Direct callees of a symbol.")
def get_callees(store: Store, name: str) -> Result:
    """Direct callees of a symbol."""
    target = _resolve_one(store, name)
    if target is None:
        return refuse(f"'{name}' is not a unique symbol in the index", confidence=0.0)
    edges = store.callees_of(target.id)
    callees = [{"dst": e.dst_id, "weight": round(e.weight, 3)} for e in edges]
    return ok(callees, symbol=target.id, count=len(callees))


# --------------------------------------------------------------------------
# Headline operations (design §6/§7).
# --------------------------------------------------------------------------


@operation("References that point at something missing or stubbed (implementation holes).")
def find_holes(store: Store) -> Result:
    """Dangling references — the dual of dead code (design §6.D).

    Each is a reference whose target didn't resolve. Liveness can't be ranked
    confidently until the entry-point detector lands, so holes are flagged
    orange + needs_review rather than red.
    """
    holes = [
        {"src": e.src, "relation": e.relation.value, "missing": e.dst_symbol,
         "location": e.location}
        for e in store.unresolved_edges()
    ]
    res = ok(holes, confidence=0.7, provenance=Provenance.INFERRED,
             count=len(holes))
    res.urgency = Urgency.ORANGE
    res.add_reason("liveness of holes not yet ranked (entry-point detector pending)")
    return res


@operation("Code reachable from no entry point (dead/stale candidates).")
def find_stale(store: Store, detector: EntryPointDetector | None = None) -> Result:
    """Unreachable-from-entry-points nodes (design §6.B).

    NEVER asserts 'dead' as fact. With only the config-stub detector the entry
    set is unreliable, so results are low-confidence + needs_review by contract
    (a false 'dead' is destructive — design principle 4).
    """
    detector = detector or _default_detector()
    seeds = detector.detect(store)
    all_ids = set(store.all_node_ids())
    reachable = reachable_from(store, seeds)
    candidates = [{"id": nid} for nid in _stale_candidates(store, all_ids - reachable)]

    auto_detection = not getattr(detector, "not_implemented", False)
    if not seeds:
        return refuse(
            "no entry points known — every node looks unreachable; these are "
            "NOT dead-code findings until roots are supplied",
            confidence=0.1, provenance=Provenance.AMBIGUOUS,
            result=candidates, count=len(candidates),
        )
    if not auto_detection:
        # Real (user-supplied) roots, but the auto-detector isn't wired, so the
        # entry set may be incomplete -> the list can include live code reachable
        # only from undeclared roots. Honest middle ground, not a confident verdict.
        return refuse(
            "entry set is user-provided overrides only; automatic entry-point "
            "detection is not yet wired, so live code reachable only from "
            "undeclared roots may appear here",
            confidence=0.5, provenance=Provenance.INFERRED,
            result=candidates, count=len(candidates),
        )
    # Grounding in a runtime trace raises confidence: these were neither reached
    # statically nor observed executing (design §2c). Otherwise resolution is
    # name-based, so present as review candidates (design §5: LSP raises this).
    if store.get_meta("has_runtime") == "1":
        res = ok(candidates, confidence=0.78, provenance=Provenance.INFERRED,
                 count=len(candidates))
        res.add_reason("not reached statically AND not executed in the ingested "
                       "trace (may still be used on paths the trace didn't cover)")
        return res
    res = ok(candidates, confidence=0.6, provenance=Provenance.INFERRED,
             count=len(candidates))
    res.add_reason("reachability is from name-based resolution (no type info yet); "
                   "verify before removal")
    return res


@operation("Orientation: node counts, top hubs to read first.")
def orient(store: Store) -> Result:
    """Where to start (design §6.A). Hubs by direct fan-in for M0; transitive
    fan-in / centrality is the GraphBLAS upgrade."""
    counts: dict[str, int] = {}
    for kind in NodeKind:
        n = len(store.nodes_by_kind(kind))
        if n:
            counts[kind.value] = n
    # Transitive importance (PageRank over the whole graph) when GraphBLAS is
    # available; direct fan-in otherwise (design §6.A).
    ranking, metric = _hub_ranking(store)
    hubs = sorted(ranking.items(), key=lambda kv: kv[1], reverse=True)[:10]
    payload = {
        "node_counts": counts,
        "top_hubs": [{"id": nid, metric: round(score, 4)} for nid, score in hubs],
    }
    return ok(payload, total_nodes=store.node_count(), hub_metric=metric)


def _hub_ranking(store: Store) -> tuple[dict[str, float], str]:
    from . import algebra
    from .config import load_config

    metric = load_config().hub_metric
    if algebra.HAS_GRAPHBLAS and metric != "fan_in":
        if metric == "pagerank":
            ranks = algebra.pagerank(store)
            if ranks:
                return ranks, "pagerank"
        else:  # transitive_fan_in (default) — most-depended-on
            tfi = algebra.transitive_fan_in(store)
            if tfi:
                return {k: float(v) for k, v in tfi.items()}, "transitive_fan_in"
    return {k: float(v) for k, v in fan_in(store).items()}, "fan_in"


# --------------------------------------------------------------------------
# Declared-but-not-yet-implemented operations. They refuse honestly so the tool
# surface is complete and an LLM never gets a confident wrong answer.
# --------------------------------------------------------------------------


@operation("Blast radius of changing a symbol (which tests to run).")
def impact_of(store: Store, name: str) -> Result:
    """Reverse reachability: everything that transitively depends on a symbol,
    plus which tests it reaches (design §6.B/G)."""
    target = _resolve_one(store, name)
    if target is None:
        return refuse(f"'{name}' is not a unique symbol in the index", confidence=0.0)
    dependents = reverse_reachable_from(store, {target.id})
    tests = sorted(d for d in dependents
                   if (n := store.get_node(d)) and "test" in n.roles)
    payload = {
        "symbol": target.id,
        "blast_radius": sorted(dependents),
        "count": len(dependents),
        "tests_to_run": tests,
    }
    return ok(payload, confidence=0.9, provenance=Provenance.EXTRACTED,
              count=len(dependents), tests=len(tests))


@operation("Full-stack path between a source and a sink, with confidence.")
def trace_path(store: Store, source: str, sink: str) -> Result:
    """Highest-confidence path source -> sink under the (max, x) semiring
    (design §6.B/§13.2). Cross-language edges flow through the same algebra once
    the resolver lands; for now it traces whatever edges exist."""
    src = _resolve_one(store, source) or store.get_node(source)
    dst = _resolve_one(store, sink) or store.get_node(sink)
    if src is None or dst is None:
        return refuse("source or sink is not a unique/known symbol", confidence=0.0)
    found = best_path(store, src.id, dst.id)
    if found is None:
        return refuse(f"no path from {src.id} to {dst.id} in the graph",
                      confidence=0.0, result=[])
    path, conf = found
    prov = Provenance.EXTRACTED if conf >= 0.99 else Provenance.INFERRED
    return ok(path, confidence=conf, provenance=prov, hops=len(path) - 1)


@operation("Ranked issue list with urgency (structural scan).")
def scan(store: Store, detector: EntryPointDetector | None = None) -> Result:
    """Structural issue flagging with urgency (design §7). Suspicion, not
    diagnosis: wiring/structure defects only, each ranked by liveness."""
    detector = detector or _default_detector()
    seeds = detector.detect(store)
    # Liveness-ranked issues (stubs/holes) need seeds; structural issues (cycles,
    # data loops, god objects) don't — so report what we can even without roots.
    reachable = reachable_from(store, seeds) if seeds else set()
    issues: list[dict] = []

    # Live stubs on a reachable path: a NotImplementedError that actually runs.
    for node in store.stub_nodes():
        live = node.id in reachable
        issues.append({
            "kind": "live_stub" if live else "stub",
            "node": node.id, "location": node.location,
            "urgency": Urgency.RED.value if live else Urgency.GREEN.value,
            "reason": "unimplemented body on a reachable path" if live
            else "unimplemented body, not reachable",
        })

    # Implementation holes (dangling references), ranked by liveness.
    for edge in store.unresolved_edges():
        live = edge.src in reachable
        issues.append({
            "kind": "hole", "node": edge.src, "missing": edge.dst_symbol,
            "location": edge.location,
            "urgency": Urgency.ORANGE.value if live else Urgency.GREEN.value,
            "reason": f"reference to missing '{edge.dst_symbol}'"
            + (" on a reachable path" if live else " (unreachable)"),
        })

    # Circular dependencies (SCC > 1). Single-node self-loops are ordinary
    # recursion, not a coupling smell, so they're excluded.
    for comp in strongly_connected_components(store):
        if len(comp) < 2:
            continue
        issues.append({
            "kind": "cycle", "node": comp[0], "members": comp,
            "urgency": Urgency.ORANGE.value,
            "reason": f"circular dependency among {len(comp)} symbols",
        })

    # Data loops: feedback through mutable global state (design §6.F).
    from .dataloop import find_data_loops
    for comp in find_data_loops(store):
        var = next((c for c in comp if c.startswith("var::")), comp[0])
        issues.append({
            "kind": "data_loop", "node": var, "members": comp,
            "urgency": Urgency.ORANGE.value,
            "reason": f"data feedback loop through state '{var.rsplit('::', 1)[-1]}'",
        })

    # God objects: high fan-in AND fan-out.
    fi, fo = fan_in(store), fan_out(store)
    for nid in set(fi) & set(fo):
        if fi[nid] >= 5 and fo[nid] >= 5:
            issues.append({
                "kind": "god_object", "node": nid,
                "urgency": Urgency.ORANGE.value,
                "reason": f"high coupling (fan-in {fi[nid]}, fan-out {fo[nid]})",
            })

    rank = {Urgency.RED.value: 0, Urgency.ORANGE.value: 1, Urgency.GREEN.value: 2}
    issues.sort(key=lambda i: rank[i["urgency"]])

    top = (issues[0]["urgency"] if issues else Urgency.GREEN.value)
    res = ok(issues, provenance=Provenance.EXTRACTED, count=len(issues),
             red=sum(i["urgency"] == "red" for i in issues),
             orange=sum(i["urgency"] == "orange" for i in issues))
    res.urgency = Urgency(top) if issues else Urgency.GREEN
    if not seeds:
        res.add_reason("no entry points found — liveness-ranked issues (stubs, "
                       "holes) are omitted; only structural issues are shown")
    return res


@operation("Find code most similar to a snippet (where's the code that does X).")
def find_similar(store: Store, snippet: str, limit: int = 10) -> Result:
    """Semantic-ish retrieval over the graph (design §1). Ranks functions/methods/
    classes by token similarity (name + docstring + callees) to the snippet."""
    from . import similar

    matches = similar.find_similar(store, snippet, limit)
    if not matches:
        return refuse("no similar code found (or snippet had no usable tokens)",
                      confidence=0.0)
    payload = [{"id": nid, "score": round(s, 3)} for nid, s in matches]
    top = matches[0][1]
    return ok(payload, confidence=min(top + 0.3, 0.9),
              provenance=Provenance.INFERRED, count=len(payload))


@operation("Fuse a coverage.json runtime trace: mark what actually executed.")
def ingest_trace(store: Store, trace: str = "coverage.json") -> Result:
    """Ingest a coverage.py JSON report (design §2c). Marks executed nodes with a
    `runtime` role so they seed reachability and are never flagged dead — grounding
    the graph in what actually ran. Writes only to the index (read-only invariant).
    """
    from . import runtime

    covmap, _ = runtime.load_coverage(trace)
    if not covmap:
        return refuse(f"no usable coverage data in '{trace}' (supported: coverage.py "
                      "JSON, LCOV .info, Go coverprofile)", confidence=0.0)
    root = store.get_meta("root") or "."
    hits = runtime.hit_node_ids(store, covmap, root)
    for nid in hits:
        store.add_role(nid, "runtime")
    store.set_meta("has_runtime", "1")
    return ok({"executed_nodes": len(hits)}, executed=len(hits),
              files=len(covmap))


@operation("Risk hotspots (churn × centrality) and hidden coupling from git history.")
def risk(store: Store, path: str = ".") -> Result:
    """Fuse git history with the structural graph (design §6.H).

    `path` is the repo root (same as the indexed root). Returns risk hotspots
    (files that change often *and* are depended on heavily) and hidden coupling
    (files that co-change in git but have no structural edge — implicit deps the
    call/import graph misses).
    """
    from . import gitrisk

    if not gitrisk.is_git_repo(path):
        return refuse(f"'{path}' is not a git repository", confidence=0.0)

    churn = gitrisk.churn(path)
    if not churn:
        return refuse("no git history found for .py files", confidence=0.0, result={})

    # Node files are relative to the indexed root; git paths to the repo root.
    # Translate node files into git-relative paths so the two spaces line up.
    to_git = _git_path_mapper(store, path)

    # File-level centrality = total importance of the nodes it defines.
    ranking, _ = _hub_ranking(store)
    file_centrality: dict[str, float] = {}
    for nid in store.all_node_ids():
        f = to_git(nid.split("::", 1)[0])
        file_centrality[f] = file_centrality.get(f, 0.0) + ranking.get(nid, 0.0)

    hotspots = []
    for f, c in churn.items():
        cen = file_centrality.get(f, 0.0)
        if cen <= 0:
            continue
        hotspots.append({"file": f, "churn": c, "centrality": round(cen, 2),
                         "risk": round(c * cen, 2)})
    hotspots.sort(key=lambda h: h["risk"], reverse=True)
    if hotspots:
        top = hotspots[0]["risk"]
        for h in hotspots:
            h["urgency"] = (Urgency.ORANGE.value if h["risk"] >= top / 2
                            else Urgency.GREEN.value)

    # Hidden coupling: co-change pairs with no structural edge between the files.
    connected = _connected_file_pairs(store, to_git)
    hidden = []
    for pair, n in sorted(gitrisk.cochange(path).items(), key=lambda kv: -kv[1]):
        a, b = sorted(pair)
        if frozenset((a, b)) not in connected:
            hidden.append({"files": [a, b], "co_changes": n})

    payload = {"hotspots": hotspots[:15], "hidden_coupling": hidden[:15]}
    res = ok(payload, provenance=Provenance.INFERRED, confidence=0.7,
             hotspots=len(hotspots), hidden=len(hidden))
    if hidden:
        res.urgency = Urgency.ORANGE
        res.add_reason("hidden coupling: files co-change but share no structural edge")
    return res


def _connected_file_pairs(store: Store, to_git) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for edge in store.resolved_edges():
        if edge.dst_id is None:
            continue
        a = to_git(edge.src.split("::", 1)[0])
        b = to_git(edge.dst_id.split("::", 1)[0])
        if a != b:
            pairs.add(frozenset((a, b)))
    return pairs


def _git_path_mapper(store: Store, path: str):
    """Map an indexed-root-relative file to a repo-root-relative (git) path."""
    import os
    from . import gitrisk

    root = store.get_meta("root")
    top = gitrisk.toplevel(path)
    if not root or not top:
        return lambda f: f
    prefix = os.path.relpath(root, top)
    if prefix in (".", ""):
        return lambda f: f
    return lambda f: f"{prefix}/{f}".replace(os.sep, "/")


@operation("Compact structural summary of a subsystem (path prefix), for an LLM.")
def summarize_subsystem(store: Store, path: str) -> Result:
    """A terse map of one subsystem (design §8): node counts, the hubs to read
    first, its public surface (who calls in), and what it depends on (calls out)."""
    members = [n for n in store.all_nodes_full() if n.id.startswith(path)]
    if not members:
        return refuse(f"no nodes under '{path}'", confidence=0.0)
    mids = {n.id for n in members}
    counts: dict[str, int] = {}
    for n in members:
        counts[n.kind.value] = counts.get(n.kind.value, 0) + 1

    inbound: dict[str, int] = {}   # external -> member (public surface)
    outbound: set[str] = set()     # member -> external (dependencies)
    for e in store.resolved_edges():
        if e.dst_id is None:
            continue
        if e.dst_id in mids and e.src not in mids:
            inbound[e.dst_id] = inbound.get(e.dst_id, 0) + 1
        elif e.src in mids and e.dst_id not in mids:
            outbound.add(e.dst_id.split("::", 1)[0])

    fi = fan_in(store)
    hubs = sorted((n.id for n in members), key=lambda i: fi.get(i, 0), reverse=True)
    public = sorted(inbound, key=inbound.get, reverse=True)
    payload = {
        "node_counts": counts,
        "read_first": [h.split("::", 1)[-1] for h in hubs[:8]],
        "public_surface": [p.split("::", 1)[-1] for p in public[:8]],
        "depends_on_files": sorted(outbound)[:12],
    }
    return ok(payload, total=len(members))


@operation("A bounded relation submatrix for one subsystem (compact, for an LLM).")
def get_matrix(store: Store, scope: str, relation: str = "CALLS",
               limit: int = 25) -> Result:
    """Return a *bounded* sparse submatrix for the nodes under `scope` (an id
    prefix, e.g. a file or class), for one relation (design §8).

    Never the whole-repo N×N matrix — that's the dense anti-pattern (design §12).
    Refuses when the scope exceeds `limit` so the result stays small enough for an
    LLM to actually reason over.
    """
    try:
        rel = Relation(relation.upper())
    except ValueError:
        return refuse(f"unknown relation '{relation}'", confidence=0.0)

    members = sorted(nid for nid in store.all_node_ids() if nid.startswith(scope))
    if not members:
        return refuse(f"no nodes under scope '{scope}'", confidence=0.0)
    if len(members) > limit:
        return refuse(
            f"scope has {len(members)} nodes (> limit {limit}); narrow the scope "
            "(e.g. a single file or class) — full matrices are the dense anti-pattern",
            confidence=0.0, node_count=len(members))

    idx = {nid: i for i, nid in enumerate(members)}
    cells = [
        {"src": idx[e.src], "dst": idx[e.dst_id], "w": round(e.weight, 2)}
        for e in store.resolved_edges(rel)
        if e.src in idx and e.dst_id in idx
    ]
    labels = [m.split("::", 1)[-1] for m in members]
    payload = {
        "relation": rel.value,
        "labels": labels,
        "cells": cells,        # sparse (src_index, dst_index, weight)
        "n": len(members),
    }
    # A small dense 0/1 grid is easy for an LLM to read directly.
    if len(members) <= 12:
        grid = [[0] * len(members) for _ in members]
        for c in cells:
            grid[c["src"]][c["dst"]] = 1
        payload["grid"] = grid
    return ok(payload, density=f"{len(cells)}/{len(members)**2}")


@operation("Incrementally (re)index a path into the graph (admin).")
def reindex(store: Store, path: str, precise: bool = False) -> Result:
    """Extract a Python project into the graph (design §0/§1). Writes only to the
    index — never to source (read-only invariant).

    precise=True adds the jedi resolver (LSP-grade go-to-definition, design §5):
    slower, needs jedi installed, but sharpens method/attribute resolution.
    """
    from .config import load_config
    from .extract import extract_project
    from .resolve import default_resolvers, run_resolvers

    nodes, edges = extract_project(path, ignore=load_config(path).ignore)
    # Cross-language / framework enrichment (routes, SQL — design §2a), plus the
    # optional jedi precision pass.
    resolvers = default_resolvers()
    if precise:
        from .resolve.jedi_resolver import JediResolver
        resolvers.append(JediResolver())
    nodes, edges = run_resolvers(path, nodes, edges, resolvers)
    files = {n.id.split("::", 1)[0] for n in nodes if "::" in n.id}
    # Full rebuild: the extractor already resolved every edge against the complete
    # symbol table, so bulk-insert (nodes first) and keep those resolutions rather
    # than re-running per-file invalidation. (replace_file remains for single-file
    # incremental updates, design §4.)
    with store.conn:
        store.conn.execute("DELETE FROM nodes")
        store.conn.execute("DELETE FROM edges")
        for n in nodes:
            store.add_node(n)
        for e in edges:
            store.add_edge(e)

    import os
    store.set_meta("root", os.path.abspath(path))
    holes = len(store.unresolved_edges())
    return ok({"files": len(files), "nodes": store.node_count(), "holes": holes},
              files=len(files), nodes=store.node_count())


# --------------------------------------------------------------------------
def _resolve_one(store: Store, name: str):
    nodes = store.nodes_by_name(name)
    return nodes[0] if len(nodes) == 1 else None


def _default_detector() -> PythonLibraryDetector:
    """Build the detector from `stitchgraph.toml` — the entry-point override is
    the trust escape hatch (design §4)."""
    from .config import load_config
    cfg = load_config()
    return PythonLibraryDetector(overrides=cfg.include, include_tests=cfg.include_tests)


_CODE_KINDS = {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS}


def _stale_candidates(store: Store, unreached: set[str]) -> list[str]:
    """Filter the unreachable set down to real dead-code candidates.

    Dead *code* means an unreached function/method/class. Modules/packages
    (liveness is per-symbol), data/route nodes (DBTable, Route, ...), and dunder
    methods (`__init__`, `__enter__`, ... are framework-invoked) are not candidates.
    """
    out: list[str] = []
    for nid in unreached:
        node = store.get_node(nid)
        if node is None or node.kind not in _CODE_KINDS:
            continue
        if node.name.startswith("__") and node.name.endswith("__"):
            continue
        out.append(nid)
    return sorted(out)
