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

from .entrypoints import ConfigOnlyDetector, EntryPointDetector
from .envelope import Provenance, Result, Urgency, ok, refuse
from .model import NodeKind, Relation
from .reach import LIVENESS_RELATIONS, fan_in, reachable_from
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
    detector = detector or ConfigOnlyDetector()
    seeds = detector.detect(store)
    all_ids = set(store.all_node_ids())
    reachable = reachable_from(store, seeds)
    candidates = [{"id": nid} for nid in sorted(all_ids - reachable)]

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
    return ok(candidates, confidence=0.85, count=len(candidates))


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
    return refuse("impact_of lands with the algebra layer (M2)", confidence=0.0)


@operation("Full-stack path between a source and a sink, with confidence.")
def trace_path(store: Store, source: str, sink: str) -> Result:
    return refuse("trace_path lands with the cross-language resolver (M3)", confidence=0.0)


@operation("Ranked issue list with urgency (structural scan).")
def scan(store: Store) -> Result:
    return refuse("scan lands with the issue/urgency engine (M2)", confidence=0.0)


@operation("Incrementally (re)index a path into the graph (admin).")
def reindex(store: Store, path: str) -> Result:
    return refuse("the Python extractor (tree-sitter + LSP) is the next M0 slice",
                  confidence=0.0)


# --------------------------------------------------------------------------
def _resolve_one(store: Store, name: str):
    nodes = store.nodes_by_name(name)
    return nodes[0] if len(nodes) == 1 else None
