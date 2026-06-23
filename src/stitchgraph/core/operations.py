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
    detector = detector or PythonLibraryDetector()
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
    # Real detector, but resolution is name-based (no type info yet) so method/
    # attribute reachability is approximate — present as review candidates, not a
    # confident verdict (design §5: live types beat inferred; LSP raises this).
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
    hubs = sorted(fan_in(store).items(), key=lambda kv: kv[1], reverse=True)[:10]
    payload = {
        "node_counts": counts,
        "top_hubs": [{"id": nid, "fan_in": deg} for nid, deg in hubs],
    }
    return ok(payload, total_nodes=store.node_count())


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
    detector = detector or PythonLibraryDetector()
    seeds = detector.detect(store)
    if not seeds:
        return refuse(
            "no entry points known — liveness can't be ranked, so issues can't "
            "be prioritised; run reindex on a project with detectable roots",
            confidence=0.1, provenance=Provenance.AMBIGUOUS, result=[])

    reachable = reachable_from(store, seeds)
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
    return res


@operation("Incrementally (re)index a path into the graph (admin).")
def reindex(store: Store, path: str, precise: bool = False) -> Result:
    """Extract a Python project into the graph (design §0/§1). Writes only to the
    index — never to source (read-only invariant).

    precise=True adds the jedi resolver (LSP-grade go-to-definition, design §5):
    slower, needs jedi installed, but sharpens method/attribute resolution.
    """
    from .extract import extract_project
    from .resolve import default_resolvers, run_resolvers

    nodes, edges = extract_project(path)
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

    holes = len(store.unresolved_edges())
    return ok({"files": len(files), "nodes": store.node_count(), "holes": holes},
              files=len(files), nodes=store.node_count())


# --------------------------------------------------------------------------
def _resolve_one(store: Store, name: str):
    nodes = store.nodes_by_name(name)
    return nodes[0] if len(nodes) == 1 else None


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
