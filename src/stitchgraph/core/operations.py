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
import sqlite3
import warnings
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .entrypoints import EntryPointDetector, PythonLibraryDetector
from .envelope import Provenance, Result, ReviewCode, Urgency, ok, refuse
from .model import Edge, Layer, NodeKind, Relation
from .reach import (
    LIVENESS_RELATIONS,
    best_path,
    fan_in,
    fan_out,
    reachable_from,
    reverse_reachable_from,
    strongly_connected_components,
    transitive_fan_in_estimate,
)
from .store import Store

# --------------------------------------------------------------------------
# Operation registry — adapters iterate this to build their surfaces.
# --------------------------------------------------------------------------


_JSON_TYPES = (str, int, float, bool)
_JSON_NAMES = {"str", "int", "float", "bool", "None"}


def _json_simple(annotation) -> bool:
    """True if a param annotation maps to a JSON-friendly type the CLI/MCP can
    accept. Excludes internal object params (e.g. an EntryPointDetector) that a
    client could never pass anyway — keeping them out of the generated schemas.

    Annotations are *strings* here (`from __future__ import annotations`), so we
    parse them; real type objects are also handled for safety."""
    if annotation is inspect.Parameter.empty:
        return True
    if isinstance(annotation, str):
        parts = annotation.replace("Optional[", "").replace("]", "").split("|")
        return all(p.strip() in _JSON_NAMES for p in parts)
    if annotation in _JSON_TYPES:
        return True
    import typing
    args = typing.get_args(annotation)  # Optional[X] / X | None
    if args:
        return all(a is type(None) or a in _JSON_TYPES for a in args)
    return False


@dataclass(frozen=True)
class Operation:
    name: str  # snake_case; CLI kebab-cases it, MCP uses it verbatim
    func: Callable[..., Result]
    summary: str

    def params(self) -> list[inspect.Parameter]:
        """Caller-facing params (everything after `store`)."""
        return list(inspect.signature(self.func).parameters.values())[1:]

    def exposed_params(self) -> list[inspect.Parameter]:
        """Params the CLI/MCP surfaces — JSON-simple only (drops internal objects
        like `detector`, which fall back to their default)."""
        return [p for p in self.params() if _json_simple(p.annotation)]


_REGISTRY: dict[str, Operation] = {}


def operation(summary: str) -> Callable[[Callable[..., Result]], Callable[..., Result]]:
    def deco(func: Callable[..., Result]) -> Callable[..., Result]:
        _REGISTRY[func.__name__] = Operation(func.__name__, func, summary)
        return func
    return deco


def registry() -> list[Operation]:
    return list(_REGISTRY.values())


def _pos_int(value: Any, default: int) -> int:
    """Coerce a user-supplied count parameter: a positive int passes, anything
    else (bool, zero, negative, wrong type from an MCP JSON client) falls back
    to the op's default. One helper instead of the nine hand-copied inline
    idioms it replaces (self-review 2026-07-09) — the substitute-don't-refuse
    semantics every `limit`-taking analysis op shares."""
    return value if isinstance(value, int) and not isinstance(value, bool) \
        and value > 0 else default


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
    target, reason = _resolve_or_explain(store, name)
    if target is None:
        return refuse(reason, confidence=0.0)
    edges = store.callers_of(target.id)
    callers = [{"src": e.src, "weight": round(e.weight, 3)} for e in edges]
    res = _callgraph_result(callers, edges, symbol=target.id)
    if not callers:
        _annotate_non_call_uses(res, store, target.id, incoming=True)
    return res


@operation("Direct callees of a symbol.")
def get_callees(store: Store, name: str) -> Result:
    """Direct callees of a symbol."""
    target, reason = _resolve_or_explain(store, name)
    if target is None:
        return refuse(reason, confidence=0.0)
    edges = store.callees_of(target.id)
    callees = [{"dst": e.dst_id, "weight": round(e.weight, 3)} for e in edges]
    res = _callgraph_result(callees, edges, symbol=target.id)
    if not callees:
        _annotate_non_call_uses(res, store, target.id, incoming=False)
    return res


def _annotate_non_call_uses(res: Result, store: Store, node_id: str,
                            *, incoming: bool) -> None:
    """The confident-empty guard (docs/LLM_REVIEW.md, field review 2026-07-07):
    an empty CALLS answer is NOT evidence of an unused symbol when other
    resolved relations touch it — a Rust macro-wrapped call extracts as
    REFERENCES, a route handler is invoked by the framework via ROUTES_TO, a
    test reaches it through TESTS. An agent that trusts a confident 'no
    callers' deletes live code; surface the relation counts and demote the
    confidence instead. (Liveness was never at risk — those relations already
    count in reach — but this operation's envelope claimed certainty it did
    not have.)"""
    col = "dst_id" if incoming else "src"
    rows = store.conn.execute(
        f"""SELECT relation, COUNT(*) FROM edges_all
             WHERE {col} = ? AND relation != ? AND dst_id IS NOT NULL
             GROUP BY relation ORDER BY relation""",
        (node_id, Relation.CALLS.value)).fetchall()
    counts = {r[0]: r[1] for r in rows}
    if not counts:
        return  # genuinely nothing touches it: the confident empty stands
    res.meta["non_call_uses"] = counts
    res.confidence = min(res.confidence, 0.6)
    kinds = ", ".join(f"{v} {k}" for k, v in counts.items())
    if incoming:
        res.add_reason(
            f"no CALLS edges, but other edges point at this symbol ({kinds}) — "
            "macro/decorator/dispatch/framework use is invisible to the call "
            "graph; do NOT treat it as unused", code=ReviewCode.NON_CALL_USES)
    else:
        res.add_reason(
            f"no CALLS edges, but this symbol has other outgoing edges ({kinds}) "
            "— its dependencies may run through references/imports, not calls",
            code=ReviewCode.NON_CALL_USES)


def _callgraph_result(payload: list, edges: list, **meta) -> Result:
    """Envelope for get_callers/get_callees whose confidence/provenance reflect the edges
    backing the answer: certain only when all are EXTRACTED, else INFERRED/AMBIGUOUS +
    needs_review — a caller list resting on name-based (heuristic) edges must not report
    confidence 1.0 / EXTRACTED (panel R17B)."""
    if not edges or all(e.provenance is Provenance.EXTRACTED for e in edges):
        return ok(payload, count=len(payload), **meta)
    prov = (Provenance.AMBIGUOUS if any(e.provenance is Provenance.AMBIGUOUS for e in edges)
            else Provenance.INFERRED)
    n_conf = sum(1 for e in edges if e.provenance is Provenance.EXTRACTED)
    res = ok(payload, confidence=round(0.4 + 0.5 * (n_conf / len(edges)), 2),
             provenance=prov, count=len(payload), **meta)
    res.needs_review = True
    res.add_reason("some edges are name-based (inferred/ambiguous) — verify before relying",
                   code=ReviewCode.NAME_BASED_EDGE)
    return res


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
    if holes:
        # Liveness of holes can't be ranked confidently until the entry-point detector
        # lands, so flag them orange + needs_review with an explicit reason.
        res = ok(holes, confidence=0.7, provenance=Provenance.INFERRED, count=len(holes))
        res.urgency = Urgency.ORANGE
        res.add_reason("liveness of holes not yet ranked (entry-point detector pending)",
                       code=ReviewCode.NO_ENTRY_POINTS)
    else:
        # Zero holes is a clean, factual result (every reference resolved), not a low-
        # confidence anomaly — return it confident so needs_review stays False instead of
        # firing with no review_reasons to explain it (panels R17B, R19B).
        res = ok(holes, confidence=1.0, provenance=Provenance.INFERRED, count=0)
        res.urgency = Urgency.GREEN
    return res


# Extensions whose translation units run namespace-scope static initializers at program
# startup once the TU is LINKED — i.e. once any of its symbols is reached (C++). Unlike
# package-scoped Go (seeded by directory in entrypoints), a C++ TU is linked on use, so its
# module node's startup-edge liveness is reachability-driven (panel R36A). C files are included
# harmlessly — C static initializers must be constant expressions, so they carry no call edges.
_LINK_ON_USE_EXTS = (".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".h", ".c")


def _live_set(store: Store, seeds: set[str]) -> set[str]:
    """`reachable_from(seeds)` extended with C++ translation-unit static-init liveness: a C++
    file's module node (which carries its namespace-scope static-initializer call edges) is
    promoted to a root once ANY symbol in that file is reached — the TU is then linked and its
    initializers run at startup — and reachability is recomputed to a fixpoint (panel R36A:
    self-registering globals / `static X g;` were flagged dead though they run on link). A
    no-op (single reachable_from) when no C/C++ module is present, so non-C++ indexes — incl.
    the Python dogfood — are unaffected."""
    reachable = reachable_from(store, seeds)
    tu_modules = {m.id for m in store.nodes_by_kind(NodeKind.MODULE)
                  if m.id.split("::", 1)[0].endswith(_LINK_ON_USE_EXTS)}
    if not tu_modules:
        return reachable
    extra: set[str] = set()
    while True:
        live_files = {nid.split("::", 1)[0] for nid in reachable}
        newly = {m for m in tu_modules
                 if m not in reachable and m.split("::", 1)[0] in live_files}
        if not newly:
            return reachable
        extra |= newly
        reachable = reachable_from(store, seeds | extra)


@operation("Code reachable from no entry point (dead/stale candidates).")
def find_stale(store: Store, detector: EntryPointDetector | None = None) -> Result:
    """Unreachable-from-entry-points nodes (design §6.B).

    NEVER asserts 'dead' as fact. With only the config-stub detector the entry
    set is unreliable, so results are low-confidence + needs_review by contract
    (a false 'dead' is destructive — design principle 4).
    """
    detector = detector or _default_detector(store)
    seeds = detector.detect(store)
    all_ids = set(store.all_node_ids())
    reachable = _live_set(store, seeds)
    candidates = [{"id": nid}
                  for nid in _stale_candidates(store, all_ids - reachable, reachable)]

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
    if store.get_meta("has_runtime") == "1" and store.nodes_with_role("runtime"):
        # Require *actual* runtime-role nodes, not just the meta flag: an incremental
        # replace_file could in principle drop the last runtime node while the flag
        # lingers, which would inflate confidence to 0.78 with no grounding left (panel
        # R33A). With grounding genuinely present, the trace earns the higher confidence.
        res = ok(candidates, confidence=0.78, provenance=Provenance.INFERRED,
                 count=len(candidates))
        res.add_reason("not reached statically AND not executed in the ingested "
                       "trace (may still be used on paths the trace didn't cover)")
        return res
    res = ok(candidates, confidence=0.6, provenance=Provenance.INFERRED,
             count=len(candidates))
    res.add_reason("reachability is from name-based resolution (no type info yet); "
                   "verify before removal", code=ReviewCode.NAME_BASED_EDGE)
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
    test_ids = _test_node_ids(store)
    ranking, metric = _hub_ranking(store, exclude_sources=test_ids)
    # "Read these first" hubs are code entities (functions/classes/methods). Module and
    # other container/pseudo nodes carry high import-coupling — amplified by the module->module
    # IMPORTS edges that make module-level liveness work — so they would crowd out the actual
    # functions a reader should open first; exclude them from the hub list (module COUNT is
    # still reported in node_counts). Liveness is unaffected (panel R13A metric).
    # Test-owned defs (fixtures, suite helpers) are likewise excluded from the LIST
    # on every metric — a pytest fixture is never the answer to "read these first" —
    # and the transitive metrics also exclude test nodes as dependency MASS, so a
    # suite closing 1,117 stores can't crown Store.close the #1 hub (research/25).
    ranked = sorted(ranking.items(), key=lambda kv: kv[1], reverse=True)
    hubs = [(nid, score) for nid, score in ranked
            if (hub := store.get_node(nid)) is not None
            and hub.kind in _CODE_KINDS and nid not in test_ids][:10]
    payload = {
        "node_counts": counts,
        "top_hubs": [{"id": nid, metric: round(score, 4)} for nid, score in hubs],
    }
    return ok(payload, total_nodes=store.node_count(), hub_metric=metric)


def _test_node_ids(store: Store) -> set[str]:
    """Node ids owned by the test suite — Test kind, a `test` role, or a
    test-path file. The orientation metrics exclude these as dependency mass
    and from the hub list (research/25): correct arithmetic that ranks
    `Store.close` #1 because every test closes a store is useless orientation."""
    out: set[str] = set()
    for nid, kind, roles in store.conn.execute("SELECT id, kind, roles FROM nodes"):
        if not isinstance(nid, str):
            continue  # corrupt-index BLOB row (panel R31B): skip, never crash
        if (kind == NodeKind.TEST.value
                or "test" in (roles if isinstance(roles, str) else "").split(",")
                or _is_test_path(nid)):
            out.add(nid)
    return out


def _hub_ranking(store: Store,
                 exclude_sources: set[str] | None = None) -> tuple[dict[str, float], str]:
    from . import algebra
    from .config import load_config

    # Config from the *indexed* root (stored at reindex), not the process cwd — an
    # operation run from another directory must still honour the project's config.
    metric = load_config(store.get_meta("root")).hub_metric
    from .purity import pure_mode
    if algebra.HAS_GRAPHBLAS and not pure_mode() and metric != "fan_in":
        if metric == "pagerank":
            ranks = algebra.pagerank(store)
            if ranks:
                return ranks, "pagerank"
        else:  # transitive_fan_in (default) — most-depended-on
            tfi = algebra.transitive_fan_in(store, exclude_sources=exclude_sources)
            if tfi:
                return {k: float(v) for k, v in tfi.items()}, "transitive_fan_in"
    if metric not in ("fan_in", "pagerank"):
        # Past the exact closure's node cap (or without GraphBLAS at all), the
        # sidecar estimator keeps the TRANSITIVE ranking: sampled distinct-
        # ancestor counts, exact when the graph fits inside the sample budget
        # (v3.42.0, reach.transitive_fan_in_estimate). Only below the sampled
        # tier does orient degrade to direct confident fan-in.
        est = transitive_fan_in_estimate(store, exclude_sources=exclude_sources)
        if est is not None and est[0]:
            est_counts, exact = est
            return est_counts, ("transitive_fan_in" if exact
                                else "transitive_fan_in_sampled")
    # Fallback: direct fan-in over CONFIDENT (EXTRACTED) edges only. Counting every
    # AMBIGUOUS widening arm at full weight let homonym attributes drown the hub list at
    # scale — Home Assistant's top "hubs" were `.hass`/`.data` attribute nodes with
    # fan-in ~12,000 across 8,600 classes, pure resolution artifact (field analysis
    # 2026-07-03). Sidecar first (0.06 s vs 4–61 s for the GROUP BY on the field graph),
    # else one SQL GROUP BY, O(nodes) output, never a Python edge sweep.
    # (Since v3.32.0 the GraphBLAS metrics above also rank confident-only — every
    # hub metric now applies the same provenance discount.)
    # When the DEFAULT (transitive) metric degrades to this direct fallback, the
    # test-mass exclusion must degrade WITH it (research/25: core-only installs
    # otherwise re-crown the suite's favourite helper). An explicitly-chosen
    # `fan_in`/`pagerank` metric keeps raw degree semantics as documented.
    excl = exclude_sources if metric not in ("fan_in", "pagerank") else None
    from .adjcache import load_cache
    cache = load_cache(store)
    if cache is not None and not excl:
        counts = cache.fan_in(LIVENESS_RELATIONS, confident_only=True)
        return {k: float(v) for k, v in counts.items()}, "confident_fan_in"
    lv = tuple(r.value for r in LIVENESS_RELATIONS)
    extra = ""
    if excl:
        store.conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _hub_excl(id TEXT PRIMARY KEY)")
        store.conn.execute("DELETE FROM _hub_excl")
        store.conn.executemany("INSERT OR IGNORE INTO _hub_excl(id) VALUES (?)",
                               ((i,) for i in excl))
        extra = "AND src NOT IN (SELECT id FROM _hub_excl)"
    rows = store.conn.execute(
        f"""SELECT dst_id, COUNT(*) AS c FROM edges_all
             WHERE provenance = ?
               AND relation IN ({",".join("?" * len(lv))})
               AND dst_id IN (SELECT id FROM nodes)
               {extra}
             GROUP BY dst_id""",
        (Provenance.EXTRACTED.value, *lv)).fetchall()
    return {r["dst_id"]: float(r["c"]) for r in rows}, "confident_fan_in"


# --------------------------------------------------------------------------
# Declared-but-not-yet-implemented operations. They refuse honestly so the tool
# surface is complete and an LLM never gets a confident wrong answer.
# --------------------------------------------------------------------------


_IMPACT_DETAIL_CAP = 5_000  # radius NODE count above which hop distances are skipped
# The node cap alone does not bound memory: radj grows one entry per radius-induced
# edges_all ROW, and group expansion makes >95% of resolved rows at framework density —
# a 5k-node radius on a dense graph can induce millions of entries (self-review
# 2026-07-09). When the induced edge count passes this budget the detail pass is
# abandoned mid-stream (radj discarded, tiers fall back to id order) so impact_of can
# never rematerialize the edge table it streams to avoid.
_IMPACT_DETAIL_EDGE_CAP = 250_000


@operation("Blast radius of changing a symbol (which tests to run).")
def impact_of(store: Store, name: str, limit: int = 50) -> Result:
    """Reverse reachability: everything that transitively depends on a symbol,
    plus which tests it reaches (design §6.B/G).

    The radius is TIERED (field review 2026-07-09, request 4 — a single
    1,400-node blob at confidence 0.47 is not actionable): `confident` is the
    sub-radius reachable through EXTRACTED edges alone (act on it), `ambiguous`
    is the rest — dependents whose every route to the symbol crosses at least
    one name-based guess (verify before acting). Both tiers are ranked
    nearest-first by call-graph hop distance and capped at `limit` entries
    (`*_count` always carries the full number; `blast_radius` stays the full
    flat list for compatibility). On radii past ~5k nodes the per-node
    distances are skipped — the induced-subgraph walk is what the streamed
    tally exists to avoid at that scale — and the tiers fall back to id order.
    """
    target, candidates = _resolve_target(store, name)
    if target is None:
        if len(candidates) > 1:
            # Don't silently union homonyms and don't refuse blankly: show the
            # candidates and how to scope to one (issue #9).
            res = refuse(
                f"'{name}' matches {len(candidates)} symbols — pass a qualified id to "
                f"scope to one (e.g. {candidates[0].id!r}); not unioning their blast radii",
                confidence=0.0)
            res.alternatives = [n.to_dict() for n in candidates]
            return res
        return refuse(f"'{name}' is not in the index", confidence=0.0)
    lim = _pos_int(limit, 50)
    dependents = reverse_reachable_from(store, {target.id})
    # The certain tier: one more reverse BFS restricted to EXTRACTED edges.
    confident = (reverse_reachable_from(store, {target.id}, confident_only=True)
                 if dependents else set())
    ambiguous_only = dependents - confident
    test_set = {d for d in dependents
                if (n := store.get_node(d)) and "test" in n.roles}
    # Confidence/provenance must reflect the edges the blast radius rests on, exactly as
    # get_callers/_callgraph_result and trace_path do: a dependent reached only through
    # name-based (AMBIGUOUS/INFERRED) edges is a heuristic guess, not a certain
    # dependency — asserting provenance=extracted/0.9/no-review over it both inflates the
    # over-approximation into type-certain fact and presents genuinely false dependents
    # (homonym name-binds) as certain (panel R33A). The backing edges are the liveness
    # edges induced on the blast radius (every dependent reaches the target through them).
    radius = dependents | {target.id}
    # Streamed (cursor) scan: impact_of runs on EVERY query, and `resolved_edges()`
    # fetchall+Edge-materializes the whole table — the documented 16M-edge OOM
    # class `iter_resolved` exists to avoid (review 2026-07-03, F11a). The scan
    # feeds exactly two consumers — the radius-induced reverse adjacency for hop
    # distances (detail passes only) and the AMBIGUOUS-vs-INFERRED provenance
    # choice (demoted results only) — so it is SKIPPED outright when neither is
    # needed: a big fully-confident radius pays zero passes (self-review round 2).
    detail = len(radius) <= _IMPACT_DETAIL_CAP
    degraded = None if detail else "node_cap"
    radj: dict[str, list[str]] = {}
    any_ambiguous = False
    if detail or ambiguous_only:
        liveness = {r.value for r in LIVENESS_RELATIONS}
        n_induced = 0
        cur = store.conn.execute(
            "SELECT src, relation, dst_id, provenance FROM edges_all")
        for src, rel, dst_id, prov_s in cur:
            if rel not in liveness or src not in dependents or dst_id not in radius:
                continue
            # Provenance reflects the edges backing the AMBIGUOUS TIER, not any
            # stray edge in the radius: a redundant AMBIGUOUS edge onto a
            # confident-tier dependent must not flip the envelope to AMBIGUOUS
            # (self-review round 2). Every ambiguous-tier path provably crosses
            # a non-EXTRACTED edge whose src is itself ambiguous-only (a node
            # with an EXTRACTED edge to a confident node would BE confident),
            # so scoping the tally to those sources is exact.
            if (prov_s == Provenance.AMBIGUOUS.value and src in ambiguous_only):
                any_ambiguous = True
                if not detail:
                    break  # the only remaining question is answered
            if detail:
                n_induced += 1
                if n_induced > _IMPACT_DETAIL_EDGE_CAP:
                    # The node cap alone does not bound memory (group expansion
                    # makes >95% of rows at framework density): abandon the
                    # detail pass, NEVER silently — `degraded` lands in meta.
                    detail = False
                    degraded = "edge_budget"
                    radj.clear()
                    if any_ambiguous or not ambiguous_only:
                        break
                else:
                    radj.setdefault(dst_id, []).append(src)
    dist: dict[str, int] = {}
    if detail:
        # Hop distances = BFS from the target over the induced reverse adjacency.
        frontier = deque([(target.id, 0)])
        dist[target.id] = 0
        while frontier:
            node, d = frontier.popleft()
            for prev in radj.get(node, ()):
                if prev not in dist:
                    dist[prev] = d + 1
                    frontier.append((prev, d + 1))

    # nearest-first everywhere the order carries meaning: an adapter or consumer
    # that truncates keeps the closest (most relevant) entries, never an
    # alphabetical prefix (self-review 2026-07-09). With no distances (past the
    # detail caps) plain id order — no keyed sort tax on huge radii.
    def _nearest(i: str) -> tuple[int, str]:
        return (dist.get(i, len(radius)), i)

    def _tier(ids: set[str]) -> list[dict]:
        ranked = sorted(ids, key=_nearest) if dist else sorted(ids)
        return [({"id": i, "distance": dist[i]} if i in dist else {"id": i})
                for i in ranked[:lim]]

    tests = sorted(test_set, key=_nearest) if dist else sorted(test_set)
    payload = {
        "symbol": target.id,
        "blast_radius": sorted(dependents),
        "count": len(dependents),
        "confident": _tier(confident),
        "confident_count": len(confident),
        "ambiguous": _tier(ambiguous_only),
        "ambiguous_count": len(ambiguous_only),
        "tests_to_run": tests,
    }
    truncated = {}
    if len(confident) > lim:
        truncated["confident"] = lim
    if len(ambiguous_only) > lim:
        truncated["ambiguous"] = lim
    extra: dict[str, Any] = {"tiers_truncated": truncated} if truncated else {}
    if degraded and dependents:
        # Degradation is never silent: distances were skipped because a memory
        # bound tripped, not because the graph changed (self-review round 2).
        extra["distances_skipped"] = degraded
    # The demotion gate is the NODE-tier split, not the raw edge tally: a
    # dependent with a confident route is certain even when a redundant
    # name-based edge also points at it, so an empty `ambiguous` tier must not
    # produce a hedged envelope whose reason says "0 of N dependents…" while
    # telling the consumer to verify an empty list (self-review 2026-07-09;
    # panel R33A's intent — dependents reached ONLY through guesses are the
    # over-approximation — is exactly the tier definition).
    if not dependents or not ambiguous_only:
        return ok(payload, confidence=0.9, provenance=Provenance.EXTRACTED,
                  count=len(dependents), tests=len(tests), **extra)
    prov = Provenance.AMBIGUOUS if any_ambiguous else Provenance.INFERRED
    res = ok(payload,
             confidence=round(0.4 + 0.5 * (len(confident) / len(dependents)), 2),
             provenance=prov, count=len(dependents), tests=len(tests), **extra)
    res.needs_review = True
    res.add_reason(f"{len(ambiguous_only)} of {len(dependents)} dependents are reached "
                   "only through name-based (inferred/ambiguous) edges — act on the "
                   "'confident' tier, verify the 'ambiguous' tier before relying",
                   code=ReviewCode.NAME_BASED_EDGE)
    return res


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
        # A genuine "no path" is a refusal (ok=False), not a vacuous empty success:
        # passing result=[] would make `refuse` set ok=True (result is not None) and
        # let callers that check `.ok` believe a path was found.
        return refuse(f"no path from {src.id} to {dst.id} in the graph",
                      confidence=0.0)
    path, conf = found
    # Provenance must reflect the edges actually on the path, not just the propagated
    # confidence: a name-based AMBIGUOUS/INFERRED edge can carry weight 1.0 (e.g. a
    # synthetic override edge from _propagate_overrides), giving conf 1.0 that the old
    # conf>=0.99 proxy mislabelled EXTRACTED/no-review. Mirror impact_of/get_callers —
    # the provenance-demotion column (panel R34B).
    hop_edges = _path_edges(store, path)
    if not hop_edges or all(e.provenance is Provenance.EXTRACTED for e in hop_edges):
        return ok(path, confidence=conf, provenance=Provenance.EXTRACTED, hops=len(path) - 1)
    prov = (Provenance.AMBIGUOUS if any(e.provenance is Provenance.AMBIGUOUS for e in hop_edges)
            else Provenance.INFERRED)
    res = ok(path, confidence=conf, provenance=prov, hops=len(path) - 1)
    res.needs_review = True
    res.add_reason("the path includes name-based (inferred/ambiguous) edges — "
                   "verify before relying", code=ReviewCode.NAME_BASED_EDGE)
    return res


def _path_edges(store: Store, path: list[str]) -> list[Edge]:
    """The resolved edges actually traversed on `path` — for each hop, the max-weight edge
    between consecutive nodes (matching best_path's (max, x) choice). Lets trace_path derive
    provenance from real edge evidence rather than the propagated confidence alone."""
    if len(path) < 2:
        return []
    wanted = set(zip(path, path[1:], strict=False))
    by_pair: dict[tuple[str, str], Edge] = {}
    for e in store.resolved_edges():
        if e.dst_id is None:
            continue
        key = (e.src, e.dst_id)
        if key in wanted and (key not in by_pair or e.weight > by_pair[key].weight):
            by_pair[key] = e
    return [by_pair[p] for p in wanted if p in by_pair]


_SCC_RELATIONS = (Relation.CALLS, Relation.IMPORTS)


_GOD_REVIEW_CAP = 500  # hedged god-object flags kept per scan; see the cutoff comment below
_GOD_MIN_POP = 200     # coupled code nodes needed before percentile floors apply


def _god_floors(store: Store, fi: dict, fo: dict) -> tuple[int, int]:
    """Size-scaled god-object floors (research/25 dogfood). The absolute floors
    (5/5) are right for small graphs but a 2,878-node codebase strolls past
    them — 252 of its src/ nodes were flagged ORANGE, which is noise, not
    signal. On graphs with a meaningful population (>= _GOD_MIN_POP code nodes
    coupled in BOTH directions) a god object must also be exceptional among
    its peers: strictly ABOVE the 95th percentile of each direction's degrees
    (p95 + 1 — when the crowd itself sits at one value, >= p95 would flag the
    crowd), never below the absolute floor. Below the population cut the
    behaviour is byte-identical to the historical floors."""
    both = [nid for nid in set(fi) & set(fo) if fi[nid] >= 1 and fo[nid] >= 1]
    if len(both) < _GOD_MIN_POP:
        return 5, 5
    code = {r[0] for r in store.conn.execute(
        "SELECT id FROM nodes WHERE kind IN (?, ?, ?)",
        tuple(k.value for k in _CODE_KINDS))}
    pop = [nid for nid in both if nid in code]
    if len(pop) < _GOD_MIN_POP:
        return 5, 5

    def p95(vals: list[int]) -> int:
        vals = sorted(vals)
        return vals[min(len(vals) - 1, int(0.95 * len(vals)))]

    return (max(5, p95([fi[n] for n in pop]) + 1),
            max(5, p95([fo[n] for n in pop]) + 1))


@operation("Ranked issue list with urgency (structural scan).")
def scan(store: Store, detector: EntryPointDetector | None = None,
         show_heuristic: bool = False) -> Result:
    """Structural issue flagging with urgency (design §7). Suspicion, not
    diagnosis: wiring/structure defects only, each ranked by liveness.

    `show_heuristic=True` (CLI `--show-heuristic`) also lists the cycles whose
    every linking edge is a name-based guess (0/N confident) — pure homonym
    collisions (`new`/`default`/`build` matching across unrelated types) that
    are suppressed by default because they look like architecture problems and
    force the consumer to disprove each one (field review 2026-07-09, request
    2). The suppression is counted in meta, never silent."""
    detector = detector or _default_detector(store)
    seeds = detector.detect(store)
    # Liveness-ranked issues (stubs/holes) need seeds; structural issues (cycles,
    # data loops, god objects) don't — so report what we can even without roots.
    # _live_set adds C++ TU static-init liveness so scan's liveness agrees with find_stale.
    reachable = _live_set(store, seeds) if seeds else set()
    # A stub only shouts RED when its liveness rests on EXTRACTED edges; if it is
    # reachable only through an INFERRED/AMBIGUOUS hop (a heuristic route, an
    # ambiguous name) the liveness itself is uncertain, so the provenance ceiling
    # caps it at ORANGE (envelope §7: nothing low-confidence shouts red).
    certain = (reachable_from(store, seeds, confident_only=True)
               if seeds else set())
    issues: list[dict] = []

    # Live stubs on a reachable path: a NotImplementedError that actually runs.
    for node in store.stub_nodes():
        live = node.id in reachable
        certain_live = node.id in certain
        # Test-owned stubs are deliberate doubles (a fake server's empty run(),
        # a mock's pass body), not unimplemented product code — the same
        # test-ownership principle the god-object detector applies. Found by
        # dogfooding the whole repo (self-review round 2): the sole RED in
        # stitchgraph's own scan was a fake in tests/test_mcp.py. Kept visible
        # (GREEN advisory), never RED/ORANGE.
        if "test" in node.roles or _is_test_path(node.id):
            issues.append({
                "kind": "stub", "node": node.id, "location": node.location,
                "urgency": Urgency.GREEN.value,
                "reason": "unimplemented body in test-owned code — a deliberate "
                          "double/fake, not product debt",
            })
            continue
        issues.append({
            "kind": "live_stub" if live else "stub",
            "node": node.id, "location": node.location,
            "urgency": (Urgency.RED.value if certain_live
                        else Urgency.ORANGE.value if live else Urgency.GREEN.value),
            "reason": "unimplemented body on a reachable path" if certain_live
            else "unimplemented body on an inferred (heuristic) path" if live
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

    # Provenance of the participating edges decides whether a *structural* finding is
    # trustworthy or a resolution artifact (issue #11): a cycle / god object that exists
    # only because of AMBIGUOUS (over-approximated homonym) or INFERRED (heuristic) edges
    # is mostly noise on a language without type resolution. The shares are per-component /
    # per-candidate COUNT queries in SQLite, never a materialised edge list: indexing all
    # edges into Python dicts here was scan's O(edges) peak — MemoryError at a 6 GB cap on
    # Home Assistant's 16M-edge graph while every sweep around it ran at adjacency scale
    # (field analysis 2026-07-03).
    _extracted = Provenance.EXTRACTED.value

    def _share_rows(total: int, confident: int) -> tuple[float, int, int]:
        # same contract as _confident_share: empty -> fully confident (issue #11)
        return (confident / total if total else 1.0), confident, total

    # Circular dependencies (SCC > 1). Single-node self-loops are ordinary
    # recursion, not a coupling smell, so they're excluded.
    heuristic_cycles_suppressed = 0
    for comp in strongly_connected_components(store):
        if len(comp) < 2:
            continue
        # A symbol cycle is a code-entity smell (mutual recursion / tangled calls). The
        # module->module IMPORTS edges (which carry module-level liveness) let MODULE nodes
        # form import cycles too; a circular *import* is a different analysis, so don't
        # surface it here as a "circular dependency among symbols" — skip components that
        # include any pseudo node (panel R14A, consistent with god_object/orient).
        if any((cn := store.get_node(m)) is None or cn.kind not in _CODE_KINDS
               for m in comp):
            continue
        # CROSS JOIN pins the join order (SQLite honours it as a directive): drive from
        # the small member table and probe edges via idx_edges_src per member. The
        # relation/membership predicates live in the aggregates, NOT the WHERE — a
        # `relation IN (...)` there invites a stat-less planner onto idx_edges_rel
        # (a walk of every CALLS entry in the table, per component) instead.
        store.conn.execute("DROP TABLE IF EXISTS temp._scan_comp")
        store.conn.execute("CREATE TEMP TABLE _scan_comp (id TEXT PRIMARY KEY)")
        store.conn.executemany("INSERT OR IGNORE INTO _scan_comp VALUES (?)",
                               [(m,) for m in comp])
        _srel = tuple(r.value for r in _SCC_RELATIONS)
        # TWO branch queries, summed in Python — NOT one query over `edges_all`:
        # SQLite cannot flatten a UNION-ALL view that sits inside a join, so the
        # single-view form MATERIALISES all 16M logical rows (plus an automatic
        # index over them) PER COMPONENT — observed live as a >1 h scan on the
        # HA field index (py-spy, 2026-07-06) where the flat-table original took
        # minutes. Driving each branch directly keeps idx_edges_src /
        # idx_groups_src probes per member, exactly the plan the CROSS JOIN
        # directive was written to pin.
        row = store.conn.execute(
            """SELECT COALESCE(SUM(e.relation IN (?, ?)
                                   AND e.dst_id IN (SELECT id FROM _scan_comp)), 0) AS t,
                      COALESCE(SUM(e.relation IN (?, ?) AND e.provenance = ?
                                   AND e.dst_id IN (SELECT id FROM _scan_comp)), 0) AS c
                 FROM _scan_comp s CROSS JOIN edges e ON e.src = s.id
                WHERE e.dst_id IS NOT NULL""",
            (*_srel, *_srel, _extracted)).fetchone()
        grow = store.conn.execute(
            """SELECT COALESCE(SUM(g.relation IN (?, ?)
                                   AND m.dst_id IN (SELECT id FROM _scan_comp)), 0) AS t,
                      COALESCE(SUM(g.relation IN (?, ?) AND g.provenance = ?
                                   AND m.dst_id IN (SELECT id FROM _scan_comp)), 0) AS c
                 FROM _scan_comp s CROSS JOIN edge_groups g ON g.src = s.id
                 JOIN cand_members m ON m.set_id = g.set_id""",
            (*_srel, *_srel, _extracted)).fetchone()
        store.conn.execute("DROP TABLE temp._scan_comp")
        frac, conf_n, total = _share_rows(row["t"] + grow["t"], row["c"] + grow["c"])
        artifact = frac < 0.5  # majority of the linking edges are guesses
        # A "cycle" with ZERO confident edges is not a hedged finding, it is
        # noise manufactured by name-based resolution — bare `new`/`default`/
        # `build` method-name collisions across unrelated types (common on
        # languages without type info). Down-ranking wasn't enough: they still
        # read as real architecture problems and force the consumer to disprove
        # each one (field review 2026-07-09, request 2 — 8 spurious cycles on a
        # Rust crate). Suppress by default, count in meta, opt back in with
        # `show_heuristic`. A cycle with even ONE confident edge stays: partial
        # confirmation is a genuine hedged finding, not pure collision.
        if total and conf_n == 0 and not show_heuristic:
            heuristic_cycles_suppressed += 1
            continue
        reason = (f"circular dependency among {len(comp)} symbols"
                  + ("; rests mostly on name-ambiguous/heuristic edges "
                     f"({conf_n}/{total} confident) — verify before acting" if artifact else ""))
        issues.append({
            "kind": "cycle", "node": comp[0], "members": comp,
            # An artifact cycle is capped below ORANGE so it sinks in the ranking and
            # never shouts; a confidently-linked cycle keeps its ORANGE "look closer".
            "urgency": Urgency.GREEN.value if artifact else Urgency.ORANGE.value,
            "confidence": round(0.3 + 0.6 * frac, 2),
            "needs_review": artifact,
            "confident_edges": conf_n, "edges": total,
            "reason": reason,
            # Mirror the envelope's needs_review => review_reasons contract on the inner item
            # so a consumer keying on review_reasons isn't left empty when needs_review is set
            # (panel R34B).
            "review_reasons": [reason] if artifact else [],
            "review_codes": [ReviewCode.CYCLE_HEURISTIC.value] if artifact else [],
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

    # Unused parameters (design §6.E, research/22 deliverable 3): computed per
    # function from source at scan time — deliberately NOT persisted as param
    # nodes (variable granularity stays opt-in for scale). GREEN advisory: an
    # unused param is a cleanup candidate, never urgent, and interface-shaped
    # code (overrides, callbacks, stubs) is excluded rather than hedged.
    for issue in _unused_params(store):
        issues.append(issue)

    # God objects: high fan-in AND fan-out.
    _lv = tuple(r.value for r in LIVENESS_RELATIONS)
    fi, fo = fan_in(store), fan_out(store)
    t_in, t_out = _god_floors(store, fi, fo)
    god_issues: list[dict] = []
    god_test_mass = 0
    _tsrc_ready = False
    for nid in set(fi) & set(fo):
        if fi[nid] >= t_in and fo[nid] >= t_out:
            # "God object" is a code-entity smell; a MODULE node with many importers
            # (fan-in) plus many module-level calls (fan-out, from _module_scope_edges) is
            # not an OOP god object — skip pseudo nodes so the label isn't mis-applied
            # (panel R14A). Liveness/holes are unaffected.
            gnode = store.get_node(nid)
            if gnode is None or gnode.kind not in _CODE_KINDS:
                continue
            # Test-owned entities are not design feedback (the same principle as
            # the orient hub-list exclusion, research/25 generalization check: a
            # hono test file's router mock survived the scaled floors).
            if "test" in gnode.roles or _is_test_path(nid):
                continue
            # Confident-only degree: fan-in over liveness relations, fan-out over CALLS
            # (matching fan_in/fan_out), counting only EXTRACTED edges. If the high
            # coupling is mostly ambiguous/heuristic edges (homonym `new`/`build` calls
            # that edge to every same-named def), the god-object smell is an artifact.
            # Only the selective equality (dst_id / src) goes in the WHERE; relation and
            # provenance are SUM aggregates. `WHERE src = ? AND relation = ?` let a
            # stat-less planner pick idx_edges_rel — a 12.9M-entry index walk PER
            # candidate on the 16M-edge field graph (~2 s each, hours in total) instead
            # of an idx_edges_src probe (caught live by py-spy, 2026-07-04).
            ph = ",".join("?" * len(_lv))
            # Test-SOURCED fan-in mass is likewise not design feedback (self-
            # audit 2026-07-07, the scan-side gap of the research/25 orient
            # exclusion): a production helper with 4 src callers and hundreds
            # of test callers is well-tested, not a god object. Candidates are
            # discovered on the raw (cheap, bulk) degrees, then rechecked here
            # against non-test fan-in — the same per-candidate SQL that already
            # computes the confident share, one temp-table probe extra.
            if not _tsrc_ready:
                store.conn.execute(
                    "CREATE TEMP TABLE IF NOT EXISTS _god_tsrc(id TEXT PRIMARY KEY)")
                store.conn.execute("DELETE FROM _god_tsrc")
                store.conn.executemany(
                    "INSERT OR IGNORE INTO _god_tsrc(id) VALUES (?)",
                    ((i,) for i in _test_node_ids(store)))
                _tsrc_ready = True
            in_row = store.conn.execute(
                f"""SELECT COALESCE(SUM(relation IN ({ph})), 0) AS t,
                           COALESCE(SUM(relation IN ({ph}) AND provenance = ?), 0) AS c
                     FROM edges_all WHERE dst_id = ?
                       AND src NOT IN (SELECT id FROM _god_tsrc)""",
                (*_lv, *_lv, _extracted, nid)).fetchone()
            if in_row["t"] < t_in:
                god_test_mass += 1  # coupling melts away without the suite
                continue
            out_row = store.conn.execute(
                """SELECT COALESCE(SUM(relation = ?), 0) AS t,
                          COALESCE(SUM(relation = ? AND provenance = ?), 0) AS c
                     FROM edges_all WHERE src = ?""",
                (Relation.CALLS.value, Relation.CALLS.value, _extracted,
                 nid)).fetchone()
            in_frac, c_in, _ = _share_rows(in_row["t"], in_row["c"])
            out_frac, c_out, _ = _share_rows(out_row["t"], out_row["c"])
            # An artifact needs BOTH halves to survive on confident edges; if either the
            # confident fan-in or fan-out falls below the floor the coupling isn't
            # really there once the guesses are removed.
            artifact = c_in < t_in or c_out < t_out
            frac = (in_frac + out_frac) / 2
            reason = (f"high coupling (fan-in {fi[nid]}, fan-out {fo[nid]}"
                      + (f"; floors {t_in}/{t_out}, size-scaled" if (t_in, t_out)
                         != (5, 5) else "") + ")"
                      + (f"; mostly name-ambiguous edges (confident fan-in {c_in}, "
                         f"fan-out {c_out}) — verify before acting" if artifact else ""))
            god_issues.append({
                "kind": "god_object", "node": nid,
                "urgency": Urgency.GREEN.value if artifact else Urgency.ORANGE.value,
                "confidence": round(0.3 + 0.6 * frac, 2),
                "needs_review": artifact,
                "confident_fan_in": c_in, "confident_fan_out": c_out,
                "reason": reason,
                "review_reasons": [reason] if artifact else [],  # inner-item contract (panel R34B)
                "review_codes": [ReviewCode.NAME_BASED_EDGE.value] if artifact else [],
            })
    # Scale cutoff: on the 16M-edge field graph the hedged (needs_review) god-object
    # flags numbered 11,117 of 11,124 — individually honest, collectively unusable
    # (field analysis 2026-07-04). Confident flags always survive; hedged ones are
    # capped at the top _GOD_REVIEW_CAP by (confidence desc, node) — deterministic,
    # and a no-op on any graph a human would read unfiltered. The suppression is
    # reported, never silent.
    god_suppressed = 0
    hedged = [g for g in god_issues if g["needs_review"]]
    if len(hedged) > _GOD_REVIEW_CAP:
        keep = set(id(g) for g in sorted(
            hedged, key=lambda g: (-g["confidence"], g["node"]))[:_GOD_REVIEW_CAP])
        god_suppressed = len(hedged) - _GOD_REVIEW_CAP
        god_issues = [g for g in god_issues
                      if not g["needs_review"] or id(g) in keep]
    issues.extend(god_issues)

    rank = {Urgency.RED.value: 0, Urgency.ORANGE.value: 1, Urgency.GREEN.value: 2}
    issues.sort(key=lambda i: rank[i["urgency"]])

    top = (issues[0]["urgency"] if issues else Urgency.GREEN.value)
    res = ok(issues, provenance=Provenance.EXTRACTED, count=len(issues),
             red=sum(i["urgency"] == "red" for i in issues),
             orange=sum(i["urgency"] == "orange" for i in issues))
    res.urgency = Urgency(top) if issues else Urgency.GREEN
    if heuristic_cycles_suppressed:
        # Suppressed, never silent (the god-object precedent below): the count is
        # reported and `show_heuristic=True` lists them.
        res.meta["heuristic_cycles_suppressed"] = heuristic_cycles_suppressed
        res.add_reason(
            f"{heuristic_cycles_suppressed} cycle(s) whose every linking edge is a "
            "name-based guess (0 confident) were suppressed — pure method-name "
            "collisions, likely resolution artifacts; rerun with show_heuristic=True "
            "(--show-heuristic) to list them", code=ReviewCode.CYCLE_HEURISTIC)
    if god_test_mass:
        res.meta["god_objects_test_mass_suppressed"] = god_test_mass
    if god_suppressed:
        res.meta["god_objects_suppressed"] = god_suppressed
        res.add_reason(
            f"{god_suppressed} low-confidence (needs_review) god-object flags beyond "
            f"the top {_GOD_REVIEW_CAP} were suppressed — individually hedged, "
            "collectively noise at this graph size; confident flags are never dropped",
            code=ReviewCode.SUPPRESSED_RESULTS)
    if not seeds:
        res.add_reason("no entry points found — liveness-ranked issues (stubs, "
                       "holes) are omitted; only structural issues are shown",
                       code=ReviewCode.NO_ENTRY_POINTS)
    return res


@operation("Structural chokepoints: nodes whose removal fragments the graph (criticality).")
def find_chokepoints(store: Store, limit: int = 20) -> Result:
    """Articulation points (cut vertices) of the call/reference graph — advisory structural
    *criticality* (design §6). A chokepoint is a node whose removal disconnects the graph; each is
    ranked by its **blast radius** — how many nodes get cut off from the main body if it fails
    (a robustness/"dangerous to touch" signal distinct from the `orient` hub ranking, which measures
    centrality, not cut-vertex-ness). Structural and advisory ONLY: like hubs, cycles and god
    objects it never feeds `find_stale` — the cardinal rule is a liveness property. Code entities
    only (Module / pseudo nodes are excluded, as in `orient`/`scan`). Returns [] on an empty graph;
    never raises."""
    from .reach import articulation_points

    lim = _pos_int(limit, 20)
    aps = articulation_points(store)
    items: list[dict] = []
    for nid, blast in sorted(aps.items(), key=lambda kv: (-kv[1], kv[0])):
        node = store.get_node(nid)
        if node is None or node.kind not in _CODE_KINDS:
            continue  # a chokepoint label is a code-entity smell; skip Module/pseudo (panel R14A parity)
        items.append({
            "id": nid, "name": node.name, "location": node.location,
            "blast_radius": blast,
            "reason": f"removing this isolates {blast} node(s) from the rest of the graph",
        })
        if len(items) >= lim:
            break
    return ok(items, provenance=Provenance.EXTRACTED, count=len(items),
              chokepoints=len(aps))


@operation("Discover the natural subsystems of a codebase (spectral clustering, auto-labelled).")
def find_subsystems(store: Store, k: int | None = None) -> Result:
    """Partition the call/reference graph into its natural **subsystems** by spectral clustering of
    the graph Laplacian (design §6), each auto-labelled with the identifier tokens that most
    distinguish it — the structural complement to the semantic `find_similar`/`summarize_subsystem`
    (it *discovers* the boundaries rather than describing a named scope). `k` is the number of
    subsystems (auto-selected from the spectral eigengap when None). Clusters the giant component of
    the graph, largest subsystem first. Advisory and read-only: like `orient`/`risk` it never feeds
    `find_stale`. Needs numpy; the optional `[spectral]` extra (scipy) removes the dense-solver size
    cap and scales via a sparse solver — without it, a graph beyond the cap refuses cleanly."""
    from . import spectral

    if not spectral.HAS_NUMPY:
        return refuse("subsystem decomposition needs numpy (install 'stitchgraph[spectral]')",
                      confidence=0.0)
    want = k if isinstance(k, int) and not isinstance(k, bool) and k >= 2 else None
    try:
        clusters, meta = spectral.decompose(store, k=want)
    except RuntimeError as exc:  # too large for the dense fallback / numpy missing
        return refuse(str(exc), confidence=0.0)
    return ok(clusters, provenance=Provenance.EXTRACTED, count=len(clusters), **meta)


@operation("Behavioural modes from runtime coverage (POD): functional modes + minimal test set.")
def find_modes(store: Store, coverage: str = "coverage_modes.json",
               k: int | None = None) -> Result:
    """Decompose a codebase's **runtime behaviour** via POD (SVD of the per-test co-activation matrix)
    — the runtime complement to the static `find_subsystems` (design §6). Reads a per-test coverage
    artifact (canonical `stitchgraph-coverage-v1` JSON of which test executed which function) and
    returns the ranked **behavioural modes** (function groups that fire together — routing, sessions,
    …), the **intrinsic dimensionality** (modes to 90% energy), a **minimal test set** that covers all
    executed functions, and a redundant-test-pair count. Advisory and read-only — never feeds
    `find_stale`; stitchgraph never runs your code, it only reads the inert matrix (produce it in your
    own sandbox with `scaffold_coverage`). Needs numpy; the `[spectral]` extra scales large matrices."""
    from . import modes

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    if not modes.HAS_NUMPY:
        return refuse("behavioural-mode analysis needs numpy (install 'stitchgraph[spectral]')",
                      confidence=0.0)
    if not modes.load_coverage(coverage):
        return refuse(f"no usable per-test coverage in '{coverage}' (expected {modes.FORMAT} JSON; "
                      "generate it with scaffold_coverage)", confidence=0.0)
    want = k if isinstance(k, int) and not isinstance(k, bool) and k >= 2 else None
    try:
        payload, meta = modes.decompose(store, coverage, k=want)
    except (RuntimeError, MemoryError) as exc:  # matrix too big for dense path / OOM on a huge artifact
        return refuse(f"coverage matrix too large to decompose in memory ({exc}); "
                      "install the 'spectral' extra or reduce the suite", confidence=0.0)
    res = ok(payload, provenance=Provenance.EXTRACTED,
             count=len(payload["modes"]), **meta)
    # The sparse solver decomposes the UNCENTRED matrix (mode 1 ≈ the mean profile), so mode
    # rankings differ qualitatively from the dense mean-centred path — say so instead of hiding
    # it in meta["solver"] (review 2026-07-03, F7).
    if meta.get("solver") == "scipy":
        res.add_reason("modes computed on the uncentred matrix (sparse solver): mode 1 "
                       "approximates the mean coverage profile, unlike the dense mean-centred path")
    if meta.get("intrinsic_dimensionality_is_lower_bound"):
        res.add_reason("intrinsic_dimensionality is a lower bound — only the top modes were "
                       "computed and they capture <90% of total energy")
    return res


@operation("Generate a sandboxed per-test-coverage capture kit (Docker / shell / CI) for find_modes.")
def scaffold_coverage(store: Store, out_dir: str = "stitchgraph-coverage",
                      language: str | None = None) -> Result:
    """Write a sandboxed capture kit that produces the per-test coverage artifact `find_modes` needs
    (design §6). Producing coverage means running the project's tests (arbitrary code), so stitchgraph
    **generates the recipe but never runs it** — you run it in your own jail. Emits, per detected
    language, three interchangeable options (Docker, plain shell, CI) plus a README and the canonical
    format spec; Python is turnkey, other languages ship a wired template. Writes helper files into
    `out_dir` only (like `report`) — never touches source, never executes. Read-only w.r.t. the graph;
    never feeds `find_stale`."""
    from . import coverage_scaffold

    if not isinstance(out_dir, str) or not out_dir:
        return refuse("out_dir must be a non-empty string", confidence=0.0)
    if language is not None and not isinstance(language, str):
        return refuse("language must be a string", confidence=0.0)
    try:
        manifest = coverage_scaffold.generate(store, out_dir, language=language)
    except OSError as exc:
        return refuse(f"could not write coverage kit to '{out_dir}': {exc}", confidence=0.0)
    return ok(manifest, provenance=Provenance.EXTRACTED,
              count=len(manifest["files"]), languages=manifest["languages"])


@operation("Feature map: each behavioural mode's implementing functions × files × expressing tests.")
def feature_map(store: Store, coverage: str = "coverage_modes.json", k: int | None = None) -> Result:
    """The actionable, full-id view of the behavioural modes (design §6): per mode, the top-loading
    **functions** (the feature's implementation), the **files** they span, and the **tests** that most
    express it — a feature ↔ code ↔ test map for "which tests exercise feature X", coverage-gap-by-
    feature, and onboarding slices. Advisory, read-only; needs numpy (POD/SVD)."""
    from . import modes

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    if not modes.HAS_NUMPY:
        return refuse("feature-map analysis needs numpy (install 'stitchgraph[spectral]')",
                      confidence=0.0)
    if not modes.load_coverage(coverage):
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    want = k if isinstance(k, int) and not isinstance(k, bool) and k >= 2 else None
    try:
        features, meta = modes.feature_map(store, coverage, k=want)
    except (RuntimeError, MemoryError) as exc:
        return refuse(f"coverage matrix too large to decompose in memory ({exc}); install the "
                      "'spectral' extra or reduce the suite", confidence=0.0)
    return ok({"features": features}, provenance=Provenance.EXTRACTED, count=len(features), **meta)


@operation("Behavioural outlier tests: unique-behaviour vs everything-touching smoke (mode residual).")
def find_outlier_tests(store: Store, coverage: str = "coverage_modes.json",
                       limit: int = 20, k: int | None = None) -> Result:
    """Tests the mainstream behavioural modes reconstruct poorly (design §6): a high residual marks a
    *unique-behaviour* test (the only thing covering something — keep it) or an *everything-touching
    smoke* test (high load on mode 1). Ranked by residual. Advisory, read-only; needs numpy (POD/SVD)."""
    from . import modes

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    if not modes.HAS_NUMPY:
        return refuse("outlier analysis needs numpy (install 'stitchgraph[spectral]')", confidence=0.0)
    if not modes.load_coverage(coverage):
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    lim = _pos_int(limit, 20)
    want = k if isinstance(k, int) and not isinstance(k, bool) and k >= 2 else None
    try:
        rows, meta = modes.outlier_tests(store, coverage, k=want, limit=lim)
    except (RuntimeError, MemoryError) as exc:
        return refuse(f"coverage matrix too large to decompose in memory ({exc}); install the "
                      "'spectral' extra or reduce the suite", confidence=0.0)
    return ok({"outliers": rows}, provenance=Provenance.EXTRACTED, count=len(rows), **meta)


@operation("Which tests to run for a change: runtime coverage fused with the static blast radius.")
def select_tests(store: Store, name: str, coverage: str = "coverage_modes.json") -> Result:
    """Forward-looking test selection for a change to `name` (design §6). Fuses two signals: the tests
    that **actually executed** the symbol (from a per-test coverage artifact — ground truth) and the
    tests that **statically reach** it via the call graph (like `impact_of`). Classifies the union into
    `both` (high confidence), `runtime_only` (ran it via a path the static graph missed — dynamic
    dispatch / framework), and `static_only` (reachable but never run in the recorded suite — a coverage
    gap). `run_these` is the recommended set. `name` may be a **changeset** — several comma-separated
    symbols (e.g. a PR's touched functions) — whose tests are unioned. Advisory, read-only; needs no
    numpy — pure set math over the inert matrix (produce it in your own sandbox with
    `scaffold_coverage`)."""
    from . import coverage_query

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    if not isinstance(name, str):
        return refuse("symbol name must be a string", confidence=0.0)
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if not parts:
        return refuse("no symbol given", confidence=0.0)
    unresolved: list[str] = []
    if len(parts) == 1:  # single symbol: refuse an ambiguous homonym with candidates (as before)
        target, candidates = _resolve_target(store, parts[0])
        if target is None:
            if len(candidates) > 1:
                res = refuse(
                    f"'{parts[0]}' matches {len(candidates)} symbols — pass a qualified id to scope to "
                    f"one (e.g. {candidates[0].id!r})", confidence=0.0)
                res.alternatives = [n.to_dict() for n in candidates]
                return res
            return refuse(f"'{parts[0]}' is not in the index", confidence=0.0)
        targets = [target.id]
    else:  # changeset: resolve each; an unresolvable/ambiguous one is noted, not fatal
        targets = []
        for p in parts:
            t = _resolve_one(store, p)
            (targets.append(t.id) if t is not None else unresolved.append(p))
        if not targets:
            return refuse(f"none of the changeset symbols resolve to a unique indexed symbol: {parts}",
                          confidence=0.0)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage); for static-only "
                      "selection use impact_of", confidence=0.0)
    tset = set(targets)
    runtime = coverage_query.tests_for(cov, tset)
    dependents = reverse_reachable_from(store, tset)
    static = {d for d in dependents if (n := store.get_node(d)) and "test" in n.roles}
    recommended = sorted(runtime | static)
    payload = {
        "symbols": sorted(tset),
        "run_these": recommended,
        "count": len(recommended),
        "ran_it": sorted(runtime),
        "both": sorted(runtime & static),
        "runtime_only": sorted(runtime - static),   # exercised via a path the call graph missed
        "static_only": sorted(static - runtime),     # reachable but never executed in this coverage
    }
    if len(targets) == 1:
        payload["symbol"] = targets[0]               # back-compat for the single-symbol shape
    if unresolved:
        payload["unresolved"] = sorted(unresolved)
    res = ok(payload, provenance=Provenance.EXTRACTED, count=len(recommended),
             runtime=len(runtime), static=len(static))
    if not runtime:
        res.needs_review = True
        res.add_reason("none of the symbols were executed in this coverage artifact — 'run_these' is "
                       "the static blast radius only; coverage may predate them or not exercise them",
                       code=ReviewCode.STATIC_ONLY)
    if unresolved:
        res.needs_review = True
        res.add_reason(f"{len(unresolved)} changeset symbol(s) did not resolve to a unique indexed "
                       "symbol and were skipped", code=ReviewCode.UNRESOLVED_SYMBOL)
    return res


@operation("What code moves together with a symbol (co-activation neighbourhood, for planning a change).")
def co_change(store: Store, name: str, coverage: str = "coverage_modes.json",
              limit: int = 20) -> Result:
    """Functions whose runtime activation most resembles `name`'s across the suite — the behavioural
    neighbourhood you likely touch together when changing it (or the code that implements a given
    outcome, anchored on one of its functions) (design §6). Score is cosine similarity over the per-test
    activation columns. The runtime complement to static `get_callers`/`get_callees`: it surfaces
    co-movement the call graph can't (functions that merely fire in the same behaviours). Advisory,
    read-only; needs no numpy — pure set math over the inert coverage matrix."""
    from . import coverage_query

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    lim = _pos_int(limit, 20)
    target, candidates = _resolve_target(store, name)
    if target is None:
        if len(candidates) > 1:
            res = refuse(
                f"'{name}' matches {len(candidates)} symbols — pass a qualified id to scope to one "
                f"(e.g. {candidates[0].id!r})", confidence=0.0)
            res.alternatives = [n.to_dict() for n in candidates]
            return res
        return refuse(f"'{name}' is not in the index", confidence=0.0)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    if "test" in target.roles:
        # Anchored on a TEST (v3.33.0, research/11 A5): the answer flips from "what
        # co-moves with this function" to "what does this test REALLY cover" — its
        # executed function set, the test-intent audit. Parametrized/phase rows are
        # collapsed onto the base id, so one logical test reports one union.
        from .modes import base_test_id
        covered: set[str] = set()
        rows = 0
        for tid, funcs in cov.items():
            if base_test_id(tid) == target.id:
                covered.update(funcs)
                rows += 1
        if not rows:
            res = ok({"test": target.id, "covers": []},
                     provenance=Provenance.EXTRACTED, count=0)
            res.needs_review = True
            res.add_reason("this test has no row in the coverage artifact — it was not "
                           "part of the recorded run", code=ReviewCode.COVERAGE_ABSENT)
            return res
        payload = {"test": target.id, "covers": sorted(covered), "coverage_rows": rows}
        return ok(payload, provenance=Provenance.EXTRACTED, count=len(covered))
    neighbours = coverage_query.co_functions(cov, target.id, k=lim)
    if not neighbours:
        res = ok({"symbol": target.id, "co_changing": []}, provenance=Provenance.EXTRACTED, count=0)
        res.needs_review = True
        res.add_reason("the symbol was never executed in this coverage artifact — no "
                       "co-activation neighbourhood to report",
                       code=ReviewCode.COVERAGE_ABSENT)
        return res
    payload = {
        "symbol": target.id,
        "co_changing": [{"function": g, "score": s, "shared_tests": c} for g, s, c in neighbours],
    }
    return ok(payload, provenance=Provenance.EXTRACTED, count=len(neighbours))


@operation("Audit the call graph against runtime ground truth (static reach vs executed, per test).")
def audit_graph(store: Store, coverage: str = "coverage_modes.json",
                limit: int = 20) -> Result:
    """A standing precision/recall audit of the call graph, using runtime coverage as
    ground truth (design §6 / research/11 C3). For every test that has both a coverage
    row and a node in the graph, compare the functions it EXECUTED with the functions
    it statically REACHES:

    - executed ∧ reachable — the graph predicted reality (recall's numerator);
    - executed ∧ ¬reachable — a path the graph MISSED (dynamic dispatch, getattr,
      framework wiring): these aggregate into `missed_functions`, the actionable
      resolver-gap list — each is a place the extractor/resolvers could improve;
    - reachable ∧ ¬executed is NOT an error (static reach over-approximates by
      design and the run may simply not exercise a branch), so it is reported only
      as the over-approximation ratio, never as a defect list.

    Advisory, read-only, no numpy — set math over the inert matrix plus one forward
    closure per test (sidecar-fast). Only functions that exist as graph nodes are
    compared, so id-scheme drift between the artifact and the index reads as
    `unmatched`, not as fake misses."""
    from . import coverage_query
    from .modes import base_test_id

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    lim = _pos_int(limit, 20)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    nodes = set(store.all_node_ids())
    # collapse parametrized/phase rows onto the base test id (one logical test, one row)
    by_test: dict[str, set[str]] = {}
    for tid, funcs in cov.items():
        by_test.setdefault(base_test_id(tid), set()).update(funcs)

    # Path-prefix drift tolerance (field review 2026-07-09, request 14): the same
    # artifact find_modes consumed happily made audit_graph refuse, because ONLY
    # audit_graph additionally requires the ids to exist as graph nodes — and a
    # capture kit run from a different root (`sandbox/…` vs the indexed tree)
    # prefixes every id. Remap unmatched ids by (path basename, qualname) when
    # that key is unambiguous, and REPORT the remap — never silently.
    remapped = 0
    missing = ({t for t in by_test if t not in nodes}
               | {f for fs in by_test.values() for f in fs} - nodes)
    if missing:
        remap = _suffix_remap(missing, nodes)
        if remap:
            collapsed: dict[str, set[str]] = {}
            for tid, fset in by_test.items():
                collapsed.setdefault(remap.get(tid, tid), set()).update(
                    remap.get(f, f) for f in fset)
            by_test = collapsed
            remapped = len(remap)

    per_test: list[dict] = []
    missed_count: dict[str, int] = {}
    unmatched = 0
    tot_exec = tot_hit = tot_reach = 0
    audit_ids: list[str] = []
    for tid in sorted(by_test):
        if tid not in nodes or not (by_test[tid] & nodes):
            unmatched += 1
            continue
        audit_ids.append(tid)
    # One closure per test, batched 64-per-sweep through the bit-parallel BFS
    # (v3.39.0): sequentially this loop WAS the op's cost — 2,056 BFS = 31.6 min
    # on the HA field index; identical per-lane results are pinned by the
    # reachable_many differential test.
    from .reach import reachable_from_many
    reach_by_test = dict(zip(audit_ids,
                             reachable_from_many(store, [{t} for t in audit_ids]),
                             strict=True))
    for tid in audit_ids:
        executed = by_test[tid] & nodes
        reached = reach_by_test[tid]
        hit = executed & reached
        for f in sorted(executed - reached):
            missed_count[f] = missed_count.get(f, 0) + 1
        tot_exec += len(executed)
        tot_hit += len(hit)
        tot_reach += len(reached & nodes)
        per_test.append({
            "test": tid, "executed": len(executed), "reached": len(hit),
            "recall": round(len(hit) / len(executed), 3),
        })
    if not per_test:
        # Diagnose the id-scheme gap instead of just asserting it: one sample from
        # each namespace lets the consumer SEE the drift (prefix, separator, …).
        # min(), not sorted() — the error path needs one stable sample, not a
        # total order of every test id on a large graph.
        sample_cov = min(by_test, default="?")
        sample_graph = min(_test_node_ids(store), default="(no test nodes indexed)")
        return refuse("no coverage row matched a test node in the graph — are the artifact "
                      "and the index from the same tree? "
                      f"(artifact test id e.g. {sample_cov!r}; graph test node "
                      f"e.g. {sample_graph!r})", confidence=0.0)
    per_test.sort(key=lambda r: (r["recall"], r["test"]))
    missed = sorted(missed_count.items(), key=lambda kv: (-kv[1], kv[0]))[:lim]
    payload = {
        "tests_audited": len(per_test), "tests_unmatched": unmatched,
        "recall": round(tot_hit / tot_exec, 3) if tot_exec else 1.0,
        "overapproximation": round(tot_reach / tot_exec, 2) if tot_exec else 0.0,
        "worst_tests": per_test[:lim],
        "missed_functions": [{"function": f, "tests_missing_it": n} for f, n in missed],
    }
    res = ok(payload, provenance=Provenance.EXTRACTED, count=len(per_test))
    if remapped:
        res.meta["ids_remapped"] = remapped
        res.add_reason(f"{remapped} artifact id(s) did not match a graph node exactly and "
                       "were remapped by (file basename, symbol) — the artifact and the "
                       "index disagree on a path prefix; regenerate the artifact from the "
                       "indexed root for exact ids", code=ReviewCode.COVERAGE_MISMATCH)
    if missed:
        res.needs_review = True
        res.add_reason("missed_functions are executed on paths the static graph cannot see "
                       "(dynamic dispatch, getattr, framework wiring) — resolver-gap "
                       "candidates, not necessarily bugs in the analyzed code",
                       code=ReviewCode.RESOLVER_GAP)
    return res


def _suffix_remap(missing: set[str], nodes: set[str]) -> dict[str, str]:
    """artifact id -> graph node id, for ids whose trees disagree on a path
    PREFIX (capture kit run from a sandbox root, crate subdir, …). Two guards,
    both required (self-review 2026-07-09 — a bare basename+symbol match
    silently grafted a stale/vendored id onto an unrelated same-named file,
    fabricating recall; runtime.py's `_by_suffix` refuses bare basenames for
    the same reason, panel R34A):

    - the (basename, qualname) key must be UNIQUE among graph nodes, and
    - the two paths must actually be prefix-drifted versions of each other:
      one path is a whole-segment suffix of the other. `sandbox/tests/a.py`
      aligns with `tests/a.py`; `src/utils.py` does NOT align with
      `tools/utils.py` — that id stays unmatched (honest `tests_unmatched`)
      rather than becoming a wrong-node audit."""
    def key(nid: str) -> tuple[str, str]:
        path, _, qual = nid.partition("::")
        return (path.replace("\\", "/").rsplit("/", 1)[-1], qual)

    def aligned(a: str, b: str) -> bool:
        pa, pb = (i.partition("::")[0].replace("\\", "/") for i in (a, b))
        longer, shorter = (pa, pb) if len(pa) >= len(pb) else (pb, pa)
        return longer == shorter or longer.endswith("/" + shorter)

    # Key only the candidates that could serve a missing id — never a dict over
    # every node in a multi-million-node graph to remap a handful of ids.
    wanted = {key(mid) for mid in missing}
    by_key: dict[tuple[str, str], str | None] = {}
    for nid in nodes:
        k = key(nid)
        if k in wanted:
            by_key[k] = None if k in by_key else nid  # duplicate -> ambiguous -> never map
    out: dict[str, str] = {}
    for mid in missing:
        cand = by_key.get(key(mid))
        if cand is not None and cand != mid and aligned(mid, cand):
            out[mid] = cand
    return out


@operation("Hidden coupling: functions that co-run but never statically call each other (implicit deps).")
def find_coupling(store: Store, coverage: str = "coverage_modes.json",
                  limit: int = 40, min_shared: int = 3, scope: str = "all") -> Result:
    """Function pairs that **co-activate** strongly across the suite yet have **no static edge** between
    them (design §6) — the runtime∖structure gap. A high co-activation score with no call/inheritance
    edge flags *implicit* coupling the call graph cannot see: shared global state, event/dispatch, a
    protocol contract, or a common caller. Advisory, read-only; needs no numpy — pure set math over the
    inert coverage matrix. Each pair is a *candidate* (it also catches common-caller siblings — inspect
    before acting); `cross_file` pairs are usually the more interesting ones.

    `scope` filters to "cross_file" / "same_file" pairs ("all" default), and every reported
    pair carries `common_callers` — static callers shared by both sides (v3.33.0, research/11
    A5): a populated list usually *explains* the co-activation (siblings of one dispatcher),
    ranking truly-hidden coupling (empty list) above sibling noise."""
    from . import coverage_query

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    if scope not in ("all", "cross_file", "same_file"):
        return refuse("scope must be 'all', 'cross_file' or 'same_file'", confidence=0.0)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    lim = _pos_int(limit, 40)
    ms = _pos_int(min_shared, 3)
    # Every structurally-linked function pair (any resolved edge) is "visible" —
    # exclude those. Probed per CANDIDATE pair via two indexed lookups instead of
    # materialising a frozenset per resolved edge (v3.39.0: that set was the op's
    # entire 10-12 GB peak at 27-30M edges — the recorded known-cost-op hazard —
    # while only the few hundred co-activation candidates are ever checked).
    _q = "SELECT 1 FROM edges_all WHERE src=? AND dst_id=? LIMIT 1"

    def _linked(a: str, b: str) -> bool:
        return (store.conn.execute(_q, (a, b)).fetchone() is not None
                or store.conn.execute(_q, (b, a)).fetchone() is not None)

    try:
        pairs = coverage_query.hidden_coupling(cov, _linked, min_shared=ms, limit=lim)
    except MemoryError:
        return refuse("coverage matrix too large to correlate in memory; raise min_shared or reduce "
                      "the suite", confidence=0.0)

    def _file(fid: str) -> str:
        return fid.split("::", 1)[0]

    if scope != "all":
        want_cross = scope == "cross_file"
        pairs = [p for p in pairs if (_file(p[0]) != _file(p[1])) == want_cross]

    def _common_callers(a: str, b: str) -> list[str]:
        ca = {e.src for e in store.callers_of(a)}
        if not ca:
            return []
        return sorted(ca & {e.src for e in store.callers_of(b)})[:3]

    payload = {
        "pairs": [{"a": a, "b": b, "score": s, "shared_tests": c,
                   "cross_file": _file(a) != _file(b),
                   "common_callers": _common_callers(a, b)}
                  for a, b, s, c in pairs],
        "count": len(pairs),
    }
    res = ok(payload, provenance=Provenance.EXTRACTED, count=len(pairs))
    res.needs_review = True
    res.add_reason("co-activation without a static edge is a *candidate* for implicit coupling — "
                   "populated common_callers usually explains it (siblings of one dispatcher); "
                   "pairs with no common caller are the truly hidden ones",
                   code=ReviewCode.IMPLICIT_COUPLING)
    return res


def _function_ids(store: Store) -> set[str]:
    """Function/method node ids in the graph (the universe coverage is measured against)."""
    return {nid for nid in store.all_node_ids()
            if (n := store.get_node(nid)) and n.kind in (NodeKind.FUNCTION, NodeKind.METHOD)}


@operation("Coverage gaps: functions no test executed, split into live (write a test) vs dead.")
def find_gaps(store: Store, coverage: str = "coverage_modes.json") -> Result:
    """Functions the suite never executed, fused with reachability (design §6): `untested_live` are
    reachable-from-entry-points **and** never run — genuine coverage gaps to write a test for;
    `untested_dead` are unreachable **and** untested — corroborated dead code (cross-checks
    `find_stale`). The runtime complement to `find_stale`: static says "no one *can* reach it",
    coverage says "no test *did*". Advisory, read-only; needs no numpy. NOTE only sees code the suite
    exercised, and coverage function ids must share the reindex namespace (same root as the converter's
    SRC)."""
    from . import coverage_query

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    funcs = _function_ids(store)
    exercised = {f for fs in cov.values() for f in fs}
    ungapped = coverage_query.untested(cov, funcs)
    detector = _default_detector(store)
    reachable = _live_set(store, detector.detect(store))
    untested_live = sorted(f for f in ungapped if f in reachable)
    untested_dead = sorted(f for f in ungapped if f not in reachable)
    payload = {
        "untested_live": untested_live,
        "untested_dead": untested_dead,
        "tested": len(funcs) - len(ungapped),
        "total_functions": len(funcs),
    }
    res = ok(payload, provenance=Provenance.INFERRED, count=len(untested_live),
             untested_live=len(untested_live), untested_dead=len(untested_dead))
    res.needs_review = True
    if funcs and not (exercised & funcs):
        res.add_reason("no coverage function id matches a graph node id — likely a namespace mismatch "
                       "(reindex root vs converter SRC); results are not meaningful until they align",
                       code=ReviewCode.COVERAGE_MISMATCH)
    else:
        res.add_reason("reachability is name-based (no type info); an 'untested_live' gap is real, but "
                       "verify a symbol before treating 'untested_dead' as removable",
                       code=ReviewCode.NAME_BASED_EDGE)
    return res


@operation("Fail-fast test order: run tests so new coverage accrues fastest (prefix = a minimal cover).")
def test_order(store: Store, coverage: str = "coverage_modes.json") -> Result:
    """Order the suite so each next test adds the most *new* function coverage (greedy over the
    coverage matrix) — a regression surfaces early instead of last (design §6). The prefix up to the
    first zero-gain test is a minimal cover; the rest add no new function coverage (a fast-tier
    candidate list). Advisory, read-only; needs no numpy. Note: coverage-greedy front-loads breadth,
    not failure-likelihood."""
    from . import coverage_query

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    order = coverage_query.greedy_order(cov)
    minimal = [t for t, gain in order if gain > 0]
    payload = {
        "order": [{"test": t, "new_functions": gain} for t, gain in order],
        "minimal_prefix": minimal,
        "minimal_count": len(minimal),
        "total_tests": len(order),
    }
    return ok(payload, provenance=Provenance.EXTRACTED, count=len(order),
              minimal=len(minimal), total=len(order))


@operation("Redundant tests: groups sharing an identical coverage profile (review aid, not auto-delete).")
def redundant_tests(store: Store, coverage: str = "coverage_modes.json") -> Result:
    """Groups of tests with an **identical** function-coverage profile (design §6) — candidates for
    consolidation review. Advisory, read-only; needs no numpy. IMPORTANT: coverage-identical is NOT
    behavioural redundancy — parametrized/data-driven tests share a profile yet exercise different
    inputs (never auto-delete on this alone); this is a review aid. Near-duplicate (not identical)
    profiles are `co_change`'s territory."""
    from . import coverage_query

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    groups = coverage_query.redundant_groups(cov)
    payload = {
        "groups": [{"tests": g, "size": len(g)} for g in groups],
        "group_count": len(groups),
        "redundant_tests": sum(len(g) - 1 for g in groups),
    }
    res = ok(payload, provenance=Provenance.EXTRACTED, count=len(groups),
             redundant=sum(len(g) - 1 for g in groups))
    res.needs_review = True
    res.add_reason("identical coverage profile != behavioural redundancy (parametrized tests share a "
                   "profile but test different inputs) — a consolidation review aid, not a delete list",
                   code=ReviewCode.PROFILE_IDENTITY)
    return res


@operation("The always-on core: functions executed by the most tests (highest behavioural blast radius).")
def find_core(store: Store, coverage: str = "coverage_modes.json", limit: int = 20) -> Result:
    """Functions executed by the largest fraction of tests (design §6) — the always-on core touched by
    nearly every behaviour, so the highest-blast-radius code to review before changing. The runtime
    complement to static `find_chokepoints`. Advisory, read-only; needs no numpy."""
    from . import coverage_query

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    lim = _pos_int(limit, 20)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    core = coverage_query.core_functions(cov, top=lim)
    payload = {"core": [{"function": f, "test_count": c, "fraction": frac} for f, c, frac in core]}
    return ok(payload, provenance=Provenance.EXTRACTED, count=len(core))


@operation("Runtime risk: files that change often AND are exercised by many behaviours (churn × coverage).")
def runtime_risk(store: Store, coverage: str = "coverage_modes.json", path: str | None = None,
                 limit: int = 15) -> Result:
    """The runtime companion to `risk` (design §6.H): fuses git **churn** with **behavioural
    centrality** — how many tests exercise a file's functions (from the coverage matrix). A file that
    changes often *and* is touched by many behaviours is the most dangerous to modify, a sharper hotspot
    than churn × static-centrality alone. Advisory, read-only; needs no numpy (git + set math)."""
    from . import coverage_query, gitrisk

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    cov = coverage_query.load_coverage(coverage)
    if not cov:
        return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                      "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
    path = path or store.get_meta("root") or "."
    if not gitrisk.is_git_repo(path):
        return refuse(f"'{path}' is not a git repository", confidence=0.0)
    churn = gitrisk.churn(path)
    if not churn:
        return refuse("no git history found for indexed source files", confidence=0.0)
    lim = _pos_int(limit, 15)
    # behavioural centrality per file = total activation frequency of the functions it defines.
    # Coverage fids are relative to the INDEXED root; git churn paths to the REPO root — on a
    # src-layout project the raw join matched nothing and returned ok/0 hotspots silently
    # (found dogfooding v3.25.0 on itself). Translate exactly as `risk` does.
    to_git = _git_path_mapper(store, path)
    file_beh = _file_behaviour(cov, to_git)
    hotspots: list[dict[str, Any]] = []
    for f, c in churn.items():
        beh = file_beh.get(f, 0.0)
        if beh <= 0:
            continue
        hotspots.append({"file": f, "churn": c, "behavioural_centrality": round(beh, 2),
                         "risk": round(c * beh, 2)})
    hotspots.sort(key=lambda h: h["risk"], reverse=True)
    if hotspots:
        top = hotspots[0]["risk"]
        for h in hotspots:
            h["urgency"] = (Urgency.ORANGE.value if h["risk"] >= top / 2 else Urgency.GREEN.value)
    payload = {"hotspots": hotspots[:lim]}
    res = ok(payload, provenance=Provenance.INFERRED, confidence=0.7, hotspots=len(hotspots))
    res.needs_review = True
    if not hotspots:
        res.add_reason("no file's coverage functions matched a churned file — likely a namespace "
                       "mismatch (coverage SRC vs git root) or the changed files are untested",
                       code=ReviewCode.COVERAGE_MISMATCH)
    return res


@operation("Cross-lens hotspots: files ranked high across static centrality, git churn, and runtime behaviour.")
def find_hotspots(store: Store, path: str | None = None,
                  coverage: str = "coverage_modes.json", limit: int = 15) -> Result:
    """The convergence command (field review 2026-07-09, request 12): the single
    most valuable insight of the field report — files that rank high across
    *independent* lenses at once — previously had to be assembled by hand from
    four command outputs. This fuses whatever lenses are available:

    - **static_centrality** — hub ranking aggregated per file (always available);
    - **churn** — git commit frequency per file (needs a git repo);
    - **behavioural_centrality** — how many tests exercise the file's functions
      (needs a per-test coverage artifact from `scaffold_coverage`).

    Each lens ranks files into percentiles (ties share their average rank); a
    file's score is the geometric mean of its percentiles over the lenses it
    appears in, and only files visible to at least TWO lenses are listed — one
    strong lens is `orient`/`risk` territory, convergence is what turns
    "probably important" into "provably central". Test files and test-sourced
    dependency mass are excluded, as in `orient` (research/25): the suite is
    hot on every lens by construction — top churner, credited by its own
    coverage rows, fan-in from every test — and that convergence is an
    artifact, not centrality. Refuses when fewer than two lenses are
    available. Advisory, read-only."""
    import math

    from . import coverage_query, gitrisk

    if not isinstance(coverage, str):
        return refuse("coverage path must be a string", confidence=0.0)
    lim = _pos_int(limit, 15)
    path = path or store.get_meta("root") or "."
    to_git = _git_path_mapper(store, path)

    # Probe the two CHEAP lenses first: static centrality is always available,
    # so whether we can answer at all is decided entirely by these — refusing
    # after a hub ranking (up to a minute at field scale) wasted 100% of the
    # work (self-review 2026-07-09).
    lenses: dict[str, dict[str, float]] = {}
    if gitrisk.is_git_repo(path):
        churn = {f: float(c) for f, c in gitrisk.churn(path).items()
                 if not _is_test_path(f)}
        if churn:
            lenses["churn"] = churn
    cov = coverage_query.load_coverage(coverage)
    if cov:
        beh = _file_behaviour(cov, to_git, exclude_tests=True)
        if beh:
            lenses["behavioural_centrality"] = beh
    if not lenses:
        # Honest about what was OBSERVED: static centrality has NOT been
        # computed on this path (that is the point of the early exit), and this
        # branch is also reachable when the user supplied both inputs but the
        # test-file exclusion emptied them — say so instead of asserting an
        # availability nobody verified (self-review round 2).
        return _lens_refusal(
            "neither git churn nor usable per-test coverage; at most static "
            "centrality remains, and one lens cannot converge")
    # Static lens: test-sourced fan-in excluded exactly as orient does
    # (research/25) — a suite that closes 1,117 stores must not crown
    # Store.close, and here it would converge on 2-3 lenses at once.
    test_ids = _test_node_ids(store)
    ranking, _metric = _hub_ranking(store, exclude_sources=test_ids)
    static = _file_centrality(store, ranking, to_git, exclude=test_ids)
    if static:
        lenses["static_centrality"] = static

    if len(lenses) < 2:
        return _lens_refusal(", ".join(sorted(lenses)) or "none")

    pct = {lens: _tied_percentiles(values) for lens, values in lenses.items()}
    files = {f for values in lenses.values() for f in values}
    hotspots: list[dict[str, Any]] = []
    for f in files:
        present = [lens for lens in lenses if f in lenses[lens]]
        if len(present) < 2:
            continue  # convergence needs independent agreement, not one loud lens
        score = math.prod(pct[lens][f] for lens in present) ** (1.0 / len(present))
        hotspots.append({
            "file": f,
            "score": round(score, 4),
            "converging_lenses": len(present),
            "lenses": {lens: {"value": round(lenses[lens][f], 2),
                              "percentile": round(pct[lens][f], 3)}
                       for lens in present},
        })
    hotspots.sort(key=lambda h: (-h["converging_lenses"], -h["score"], h["file"]))
    payload = {"hotspots": hotspots[:lim], "lenses": sorted(lenses)}
    res = ok(payload, provenance=Provenance.INFERRED, confidence=0.75,
             hotspots=len(hotspots), lenses=len(lenses))
    res.add_reason("convergence is advisory: percentile fusion over "
                   f"{len(lenses)} lenses ({', '.join(sorted(lenses))}) — a ranking "
                   "aid, not a measurement")
    return res


@operation("Coverage drift: which functions gained or lost test exposure between two coverage snapshots.")
def coverage_drift(store: Store, old: str = "coverage_old.json",
                   new: str = "coverage_modes.json") -> Result:
    """Behavioural diff between two per-test coverage snapshots (design §6): functions that **gained**
    test exposure (newly exercised) or **lost** it (no longer run) between releases — a behavioural
    changelog to pair with the structural `graph_diff`. Advisory, read-only; needs no numpy."""
    from . import coverage_query

    if not isinstance(old, str) or not isinstance(new, str):
        return refuse("both coverage paths must be strings", confidence=0.0)
    o = coverage_query.load_coverage(old)
    n = coverage_query.load_coverage(new)
    if not o:
        return refuse(f"no usable coverage in old snapshot '{old}'", confidence=0.0)
    if not n:
        return refuse(f"no usable coverage in new snapshot '{new}'", confidence=0.0)
    drift = coverage_query.mode_drift(o, n)
    payload = {
        "gained_coverage": drift["gained_coverage"],
        "lost_coverage": drift["lost_coverage"],
        "gained": len(drift["gained_coverage"]),
        "lost": len(drift["lost_coverage"]),
    }
    return ok(payload, provenance=Provenance.EXTRACTED,
              count=len(drift["gained_coverage"]) + len(drift["lost_coverage"]))


@operation("Find code most similar to a snippet (where's the code that does X).")
def find_similar(store: Store, snippet: str, limit: int = 10,
                 mode: str = "semantic",
                 coverage: str = "coverage_modes.json") -> Result:
    """Semantic-ish retrieval over the graph (design §1). mode="semantic" (default) ranks
    functions/methods/classes by token similarity (name + docstring + callees) to the snippet;
    mode="structure" ranks stored functions by body-shape similarity to the snippet's function —
    name-agnostic, advisory. The snippet's language is auto-detected (Python, the JS/TS family, Go,
    Rust, C/C++, Java, C#, Ruby, PHP, or Bash) and ranked only against stored functions of the SAME
    language (a fingerprint's topology tracks its extractor, so cross-language scores aren't
    comparable). Every language but Python needs the tree-sitter extra.

    mode="behavior" (v3.33.0, research/11 B4): `snippet` names a SYMBOL, and the ranking is
    nearest neighbours in the coverage matrix's MODE space — functions that *behave* like it
    across the suite even when lexically/structurally unrelated (the denoised complement to
    `co_change`'s raw column cosine). Needs numpy and the `coverage` artifact; only functions
    the suite exercised can appear."""
    from . import similar

    # Guard arg types before the tokeniser/slice — a non-str snippet or non-int limit would
    # raise (re.findall on a non-str; `max(0, limit)` / slice on a non-int) instead of
    # returning a Result (panel R18B). The dense-embedder path masks the snippet case, so
    # the stdlib-only default install is what crashes — guard here, at the op boundary.
    if not isinstance(snippet, str):
        return refuse("snippet must be a string", confidence=0.0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        # aligned with find_component's check (self-review round 2): the three
        # refuse-style limit validations had drifted — limit=0/-5 passed here
        # and produced degenerate output (empty slice / a matrix op refusing
        # every non-empty scope) while the sibling op refused cleanly.
        return refuse("limit must be a positive integer", confidence=0.0)
    if mode not in ("semantic", "structure", "behavior"):
        return refuse("mode must be 'semantic', 'structure' or 'behavior'", confidence=0.0)
    if mode == "behavior":
        from . import coverage_query, modes
        target, candidates = _resolve_target(store, snippet)
        if target is None:
            if len(candidates) > 1:
                res = refuse(
                    f"'{snippet}' matches {len(candidates)} symbols — pass a qualified id "
                    f"(e.g. {candidates[0].id!r})", confidence=0.0)
                res.alternatives = [n.to_dict() for n in candidates]
                return res
            return refuse(f"'{snippet}' is not in the index", confidence=0.0)
        cov = coverage_query.load_coverage(coverage)
        if not cov:
            return refuse(f"no usable per-test coverage in '{coverage}' (expected the "
                          "stitchgraph-coverage-v1 JSON from scaffold_coverage)", confidence=0.0)
        try:
            neighbours = modes.behavioural_neighbours(cov, target.id, limit=max(0, limit))
        except RuntimeError as exc:
            return refuse(str(exc), confidence=0.0)
        if not neighbours:
            return refuse(f"'{target.id}' was never executed in this coverage artifact — "
                          "no behavioural embedding to rank from", confidence=0.0)
        return ok([{"id": nid, "score": s} for nid, s in neighbours],
                  provenance=Provenance.INFERRED, count=len(neighbours))
    matches = similar.find_similar(store, snippet, limit, mode=mode)
    if not matches:
        hint = ("no structurally-similar function found (snippet must be Python, JS/TS, Go, Rust, "
                "C/C++, Java, C#, Ruby, PHP, or Bash function source; the tree-sitter languages "
                "need the extra)"
                if mode == "structure"
                else "no similar code found (or snippet had no usable tokens)")
        return refuse(hint, confidence=0.0)
    payload = [{"id": nid, "score": round(s, 3)} for nid, s in matches]
    top = matches[0][1]
    return ok(payload, confidence=min(top + 0.3, 0.9),
              provenance=Provenance.INFERRED, count=len(payload))


# Test-file path fragments for find_component's exclusion (mirrors the research spike):
# the `test` role marks test FUNCTIONS, but helpers nested inside test files are
# first-class nodes without the role — the path signal catches those.
_TEST_PATH_HINTS = ("/test", "test_", "_test", "/tests/", "/spec", ".spec.", ".test.")


def _is_test_path(node_id: str) -> bool:
    rel = node_id.split("::", 1)[0].lower()
    leaf = rel.rsplit("/", 1)[-1]
    return any(h in rel for h in _TEST_PATH_HINTS) or leaf.startswith("test")


@operation("Locate the public component that implements a described purpose.")
def find_component(store: Store, query: str, limit: int = 5,
                   public_boost: float = 0.15) -> Result:
    """Purpose-aware component locator: "parse command line options" -> `Command` /
    `Option`. `find_similar`'s semantic ranking (name + docstring + callees), made
    navigational by two structural facts the graph already holds (design §1 /
    IDEAS §3, quantified in research/05-archetype-purpose): TEST code is excluded
    (by role and by test-file path), and EXPORTED / public-API symbols are boosted —
    the answer to "where is the thing that does X" is almost always public surface,
    not an internal helper. Ablation on 17 labelled queries x 17 packages: raw
    find_similar 53% P@1 -> drop-tests 59% -> +public-boost **76% P@1 / 0.80 MRR**.

    Advisory and INFERRED like find_similar: token/embedding similarity, not proof —
    minified/bundled sources (single-char identifiers) defeat name search, and a
    specific public function can drown under same-token siblings. The score carries
    the boost explicitly so a consumer can see why something ranked."""
    if not isinstance(query, str) or not query.strip():
        return refuse("query must be a non-empty string", confidence=0.0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return refuse("limit must be a positive integer", confidence=0.0)
    from . import similar

    # Over-fetch so the post-filter can drop tests without starving the result;
    # 80 matched the research eval and is plenty above any sane `limit`.
    matches = similar.find_similar(store, query, max(80, limit))
    if not matches:
        return refuse("no similar code found (or query had no usable tokens)",
                      confidence=0.0)
    out = []
    for nid, score in matches:
        node = store.get_node(nid)
        if node is None or "test" in node.roles or _is_test_path(nid):
            continue
        exported = "exported" in node.roles
        out.append({
            "id": nid, "name": node.name, "kind": node.kind.value,
            "location": node.location, "exported": exported,
            "score": round(score + (public_boost if exported else 0.0), 3),
        })
    if not out:
        return refuse("every match was test code — no public component found",
                      confidence=0.0)
    out.sort(key=lambda r: (-float(r["score"]), str(r["id"])))
    out = out[:limit]
    return ok(out, confidence=min(float(out[0]["score"]) + 0.3, 0.9),
              provenance=Provenance.INFERRED, count=len(out))


@operation("Structurally diff this index against another (translation / plan-vs-actual).")
def graph_diff(store: Store, other_db: str, mode: str = "id", body: bool = True,
               body_threshold: float = 0.95) -> Result:
    """Compare this index with another built index at `other_db` (a stitchgraph `.db` path).

    A two-LAYER diff of the code-property graph (design §5c): the CALL layer (located node/edge
    deltas) always, and — with `body` — the EXPRESSION layer (per-function value-flow shape, the
    same graph `get_matrix(layer="expression")` surfaces). mode="id" is exact (same codebase: did a
    refactor change the graph? does the actual match the plan?), mode="leaf" reduces names to their
    last component so two *different* codebases (e.g. a translation) can be compared (advisory:
    cross-language topology tracks the extractor). With `body`, Python, JS/TS/TSX, Go, Rust, C/C++,
    Java, C#, Ruby, PHP, and Bash functions present in both whose *body shape* diverged are listed
    too (same-language only). Advisory and read-only; never edits source, never feeds find_stale.

    Note: the body layer fingerprints functions from their **source files at diff time** (the body
    matrix is computed on demand, not persisted, for scale). If a side's source has moved or been
    deleted since indexing, its unreadable files are skipped, so `body_changed` may be empty and a
    pure body-only change can read as equivalent. Node/edge deltas (from the index) are unaffected."""
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path

    from . import graphdiff as gd

    if not isinstance(other_db, str):
        return refuse("other_db must be a path string", confidence=0.0)
    if mode not in ("id", "leaf"):
        return refuse("mode must be 'id' or 'leaf'", confidence=0.0)
    if not isinstance(body_threshold, (int, float)) or isinstance(body_threshold, bool) \
            or not 0.0 < body_threshold <= 1.0:
        return refuse("body_threshold must be a number in (0.0, 1.0]", confidence=0.0)
    if not Path(other_db).is_file():
        return refuse(f"no index database at '{other_db}'", confidence=0.0)
    # Validate it's a real stitchgraph index via a READ-ONLY probe *before* constructing a Store —
    # Store() runs CREATE TABLE migrations, which would add tables to (mutate) an alien sqlite file,
    # breaking the read-only-on-other-files promise. The probe also turns a corrupt file into a
    # Result instead of a raw sqlite3 traceback (panel R153 F1/F2).
    try:
        # as_uri() percent-encodes the path, so a filename containing URI-reserved chars (?, #)
        # isn't mis-parsed as a query/fragment and falsely refused (panel R154 LOW).
        probe_uri = Path(other_db).resolve().as_uri() + "?mode=ro"
        probe = sqlite3.connect(probe_uri, uri=True)
        try:
            root_row = probe.execute("SELECT value FROM meta WHERE key='root'").fetchone()
        finally:
            probe.close()
    except sqlite3.Error:
        return refuse(f"'{other_db}' is not a readable stitchgraph index", confidence=0.0)
    if root_row is None:
        return refuse(f"'{other_db}' does not look like a stitchgraph index (no indexed root)",
                      confidence=0.0)
    # Diff over a TEMP COPY, never the original. Store() runs schema migrations (ALTER TABLE /
    # schema_version insert / commit) on open, which would MUTATE a valid but older-schema
    # stitchgraph index — the probe above only rejects non-indexes, so an older real index would
    # pass and then be silently upgraded on disk. Copying first keeps the user's file byte-identical
    # (panel R160 HIGH). The copy retains the original `meta` (incl. 'root'), so the body layer still
    # fingerprints the same source files at diff time.
    tmp_dir = tempfile.mkdtemp(prefix="sg-graphdiff-")
    try:
        tmp_db = str(Path(tmp_dir) / "other.db")
        shutil.copyfile(other_db, tmp_db)
        other = Store(tmp_db)
        try:
            d = gd.graph_diff(store, other, mode=mode, body=bool(body),
                              body_threshold=float(body_threshold))
        finally:
            other.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    delta = (len(d["nodes_only_a"]) + len(d["nodes_only_b"]) + len(d["edges_only_a"])
             + len(d["edges_only_b"]) + len(d["body_changed"]))
    return ok(d, confidence=0.9 if d["equivalent"] else 0.6,
              provenance=Provenance.INFERRED, count=delta)


@operation("Fuse a coverage.json runtime trace: mark what actually executed.")
def ingest_trace(store: Store, trace: str = "coverage.json") -> Result:
    """Ingest a coverage.py JSON report (design §2c). Marks executed nodes with a
    `runtime` role so they seed reachability and are never flagged dead — grounding
    the graph in what actually ran. Writes only to the index (read-only invariant).
    """
    from . import runtime

    if not isinstance(trace, str):
        # load_coverage does Path(trace) before its own guard; a non-str trace would raise
        # instead of honouring the "empty on any problem" contract (panel R18B).
        return refuse("trace path must be a string", confidence=0.0)
    covmap, _ = runtime.load_coverage(trace)
    if not covmap:
        return refuse(f"no usable coverage data in '{trace}' (supported: coverage.py "
                      "JSON, LCOV .info, Go coverprofile)", confidence=0.0)
    root = store.get_meta("root") or "."
    hits = runtime.hit_node_ids(store, covmap, root)
    if not hits:
        # The file parsed but nothing it covers maps to an indexed symbol (wrong
        # project, an unindexed language, or stale line ranges). Grounding nothing is
        # not a success: don't set has_runtime (which would wrongly raise find_stale
        # confidence as if liveness were trace-grounded) — refuse for review instead.
        return refuse(f"coverage in '{trace}' grounded no indexed symbols "
                      "(its files/lines map to no node) — not marking runtime",
                      confidence=0.1, files=len(covmap), executed=0)
    for nid in hits:
        store.add_role(nid, "runtime")
    store.set_meta("has_runtime", "1")
    return ok({"executed_nodes": len(hits)}, executed=len(hits),
              files=len(covmap))


@operation("Risk hotspots (churn × centrality) and hidden coupling from git history.")
def risk(store: Store, path: str | None = None) -> Result:
    """Fuse git history with the structural graph (design §6.H).

    `path` is the repo root for git history. It defaults to the **indexed root
    recorded in the DB** (so `risk --db <db>` works from any cwd, like every other
    read op — issue #18); pass `--path` to override. Returns risk hotspots (files
    that change often *and* are depended on heavily) and hidden coupling (files that
    co-change in git but have no structural edge — implicit deps the call/import
    graph misses).
    """
    from . import gitrisk

    # Scope from the DB, not the process cwd: the indexed root was stored at reindex.
    path = path or store.get_meta("root") or "."

    if not gitrisk.is_git_repo(path):
        return refuse(f"'{path}' is not a git repository", confidence=0.0)

    churn = gitrisk.churn(path)
    if not churn:
        # A genuine refusal (ok=False), like the not-a-git-repo case above — NOT a
        # vacuous ok=True with result={}, which made `report` render a blank Risk
        # section (no "skipped" line) and broke the "no ok=True with empty result"
        # envelope contract (panels QQ/RR).
        return refuse("no git history found for indexed source files", confidence=0.0)

    # Node files are relative to the indexed root; git paths to the repo root.
    # Translate node files into git-relative paths so the two spaces line up.
    to_git = _git_path_mapper(store, path)

    # File-level centrality = total importance of the nodes it defines.
    ranking, _ = _hub_ranking(store)
    file_centrality = _file_centrality(store, ranking, to_git)

    hotspots: list[dict[str, Any]] = []
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


def _lens_refusal(available: str) -> Result:
    """find_hotspots' one refusal message (two call sites had already drifted
    ~90%-identical prose — self-review round 2). `available` states what was
    actually observed, never a guess."""
    return refuse(
        f"cross-lens convergence needs at least two lenses; available: {available}. "
        "Run inside a git repo for churn, and/or pass a per-test coverage artifact "
        "(scaffold_coverage) for behavioural centrality — note test files are "
        "excluded from every lens; a single lens is `orient`/`risk` territory",
        confidence=0.0)


def _tied_percentiles(values: dict[str, float]) -> dict[str, float]:
    """Percentile rank per key, ties sharing their AVERAGE rank. Ordinal ranks
    gave equal lens values arbitrarily different (alphabetical) percentiles,
    inflating low-signal files to near-top scores on tie-heavy lenses like
    churn (self-review 2026-07-09: 100 files at churn 1 spanned 0.01..0.99)."""
    ordered = sorted(values.items(), key=lambda kv: (kv[1], kv[0]))
    n = len(ordered)
    pct: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j < n and ordered[j][1] == ordered[i][1]:
            j += 1
        avg = (i + 1 + j) / 2 / n  # mean of 1-based ranks i+1 .. j
        for k in range(i, j):
            pct[ordered[k][0]] = avg
        i = j
    return pct


def _file_centrality(store: Store, ranking: dict[str, float], to_git,
                     exclude: set[str] | None = None) -> dict[str, float]:
    """File-level centrality: total hub-ranking mass of the nodes each file
    defines, in git-relative path space. The ONE aggregation `risk` and
    `find_hotspots` share (self-review 2026-07-09) — the src-layout mapping bug
    class (v3.25.0: raw join matched nothing, silent zero hotspots) must have a
    single fix point. Zero-mass files are dropped."""
    skip = exclude or set()
    out: dict[str, float] = {}
    for nid in store.all_node_ids():
        if nid in skip:
            continue
        f = to_git(nid.split("::", 1)[0])
        out[f] = out.get(f, 0.0) + ranking.get(nid, 0.0)
    return {f: v for f, v in out.items() if v > 0}


def _file_behaviour(cov: dict[str, list[str]], to_git,
                    exclude_tests: bool = False) -> dict[str, float]:
    """Per-file behavioural mass: how many tests execute each file's functions,
    in git-relative path space — shared by `runtime_risk` and `find_hotspots`."""
    from . import coverage_query

    out: dict[str, float] = {}
    for fid, tset in coverage_query.invert(cov).items():
        if exclude_tests and _is_test_path(fid):
            continue
        f = to_git(fid.split("::", 1)[0])
        out[f] = out.get(f, 0.0) + len(tset)
    return out


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


def _under_scope(nid: str, scope: str) -> bool:
    """True if node id `nid` is `scope` itself or a genuine child/member of it.

    A bare `nid.startswith(scope)` bleeds across id boundaries: scope `Foo` wrongly
    swept in the sibling class `FooBar`, and file scope `model.py::Node` pulled in
    `NodeKind` — inflating get_matrix cells/density and summarize_subsystem counts with
    unrelated nodes (panel R33B). The char(s) right after the scope must be a real id
    separator: `/` (dir→file), `::` (file→symbol), or `.` (class→member). A scope that
    already ends in a separator matches by plain prefix.
    """
    if not nid.startswith(scope):
        return False
    if nid == scope or scope.endswith(("/", "::", ".")):
        return True
    rest = nid[len(scope):]
    return rest[:2] == "::" or rest[:1] in ("/", ".")


@operation("Compact structural summary of a subsystem (path prefix), for an LLM.")
def summarize_subsystem(store: Store, path: str) -> Result:
    """A terse map of one subsystem (design §8): node counts, the hubs to read
    first, its public surface (who calls in), and what it depends on (calls out)."""
    if not isinstance(path, str):
        return refuse("path must be a string", confidence=0.0)  # None/wrong type (panel R17A)
    members = [n for n in store.all_nodes_full() if _under_scope(n.id, path)]
    if not members:
        return refuse(f"no nodes under '{path}'", confidence=0.0)
    mids = {n.id for n in members}
    counts: dict[str, int] = {}
    for n in members:
        counts[n.kind.value] = counts.get(n.kind.value, 0) + 1

    inbound: dict[str, int] = {}   # external -> member (public surface)
    outbound: set[str] = set()     # member -> external (dependencies)
    # streamed tuples, not materialized Edge objects (review 2026-07-03, F11a)
    for src, _rel, dst_id, _w in store.iter_resolved():
        if dst_id in mids and src not in mids:
            inbound[dst_id] = inbound.get(dst_id, 0) + 1
        elif src in mids and dst_id not in mids:
            outbound.add(dst_id.split("::", 1)[0])

    fi = fan_in(store)
    hubs = sorted((n.id for n in members), key=lambda i: fi.get(i, 0), reverse=True)
    public = sorted(inbound, key=lambda k: inbound[k], reverse=True)
    payload = {
        "node_counts": counts,
        "read_first": [h.split("::", 1)[-1] for h in hubs[:8]],
        "public_surface": [p.split("::", 1)[-1] for p in public[:8]],
        "depends_on_files": sorted(outbound)[:12],
    }
    return ok(payload, total=len(members))


_EXPRESSION_MAX_NODES = 300  # a value-flow graph beyond this is unreadable as a matrix (advisory)


def _expression_vfg(store: Store, node) -> tuple[list[str], list] | None:
    """The EXPRESSION-layer value-flow graph for one Function/Method node, or None if it can't be
    built (source unreadable, unknown language, or the tree-sitter extra missing). Reads the node's
    source file and runs the matching frontend's `vfg_source`, selecting by the qualname in the id —
    the same on-demand scheme `find_similar(mode="structure")` / `graph_diff(body=True)` use."""
    from pathlib import Path

    from . import (
        structure,
        structure_bash,
        structure_cpp,
        structure_csharp,
        structure_go,
        structure_java,
        structure_js,
        structure_php,
        structure_ruby,
        structure_rust,
    )
    path, sep, qual = node.id.partition("::")
    if not sep:
        return None
    qual = qual.split("#", 1)[0]
    try:
        src = Path(store.get_meta("root") or ".", path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    suf = Path(path).suffix.lower()
    vfgs: dict = {}
    if suf == ".py":
        vfgs = structure.vfg_source(src)
    else:
        for mod in (structure_js, structure_go, structure_rust, structure_cpp, structure_java,
                    structure_csharp, structure_ruby, structure_php, structure_bash):
            lang = mod._lang_for_ext(suf)
            if lang is not None:
                vfgs = mod.vfg_source(src, lang=lang)
                break
    return vfgs.get(qual)


def _pdg_for_node(store: Store, node) -> tuple[list[str], list] | None:
    """The STATEMENT-layer program-dependence graph for one Function/Method node, or None if it can't
    be built (unsupported language, source unreadable, or the tree-sitter extra missing). Python (deep
    stdlib ast), the JS family (js/ts/tsx), Go, Rust, C/C++, Java, C#, Ruby, PHP, and Bash
    (tree-sitter) — the STATEMENT-layer sweep now covers every body-matrix language. Selects the frontend by extension and the function by the qualname in the id."""
    from pathlib import Path

    from . import (
        structure,
        structure_bash,
        structure_cpp,
        structure_csharp,
        structure_go,
        structure_java,
        structure_js,
        structure_php,
        structure_ruby,
        structure_rust,
    )
    path, sep, qual = node.id.partition("::")
    if not sep:
        return None
    qual = qual.split("#", 1)[0]
    try:
        src = Path(store.get_meta("root") or ".", path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    suf = Path(path).suffix.lower()
    if suf == ".py":
        return structure.pdg_source(src).get(qual)
    # tree-sitter frontends with a STATEMENT layer.
    for mod in (structure_js, structure_go, structure_rust, structure_cpp, structure_java,
                structure_csharp, structure_ruby, structure_php, structure_bash):
        lang = mod._lang_for_ext(suf)
        if lang is not None:
            return mod.pdg_source(src, lang=lang).get(qual)
    return None


def _body_matrix(store: Store, scope: str, layer: str) -> Result:
    """Drill into ONE function's body graph — the STATEMENT (program-dependence) or EXPRESSION
    (value-flow) layer. `scope` must resolve to a single Function/Method; returns the graph in the
    same shape as the call-layer matrix (labels + cells tagged by edge kind). Advisory — the body
    matrix never feeds liveness."""
    kind_word = "value-flow graph" if layer == Layer.EXPRESSION.value else "program-dependence graph"
    fns = [nid for nid in store.all_node_ids() if _under_scope(nid, scope)
           and (nd := store.get_node(nid)) is not None
           and nd.kind in (NodeKind.FUNCTION, NodeKind.METHOD)]
    if not fns:
        return refuse(f"no function/method under scope '{scope}' — the {layer} layer drills into one "
                      f"function's {kind_word}", confidence=0.0)
    if len(fns) > 1:
        return refuse(f"scope '{scope}' matches {len(fns)} functions; the {layer} layer needs a "
                      "SINGLE function (give its full id)", confidence=0.0, node_count=len(fns))
    node = store.get_node(fns[0])
    if node is None:
        return refuse(f"node '{fns[0]}' vanished during lookup", confidence=0.0)
    if layer == Layer.STATEMENT.value:
        from . import (
            structure_bash,
            structure_cpp,
            structure_csharp,
            structure_go,
            structure_java,
            structure_js,
            structure_php,
            structure_ruby,
            structure_rust,
        )
        _path = fns[0].partition("::")[0]
        _suf = _path[_path.rfind("."):].lower() if "." in _path else ""
        if (_suf != ".py" and structure_js._lang_for_ext(_suf) is None
                and structure_go._lang_for_ext(_suf) is None
                and structure_rust._lang_for_ext(_suf) is None
                and structure_cpp._lang_for_ext(_suf) is None
                and structure_java._lang_for_ext(_suf) is None
                and structure_csharp._lang_for_ext(_suf) is None
                and structure_ruby._lang_for_ext(_suf) is None
                and structure_php._lang_for_ext(_suf) is None
                and structure_bash._lang_for_ext(_suf) is None):
            return refuse("the statement (PDG) layer supports Python, the JS family (js/ts/tsx), Go, "
                          "Rust, C/C++, Java, C#, Ruby, PHP, and Bash — every body-matrix language; "
                          f"'{fns[0]}' is not a supported-language function",
                          confidence=0.0)
    graph = _expression_vfg(store, node) if layer == Layer.EXPRESSION.value \
        else _pdg_for_node(store, node)
    if graph is None:
        return refuse(f"could not build the {kind_word} for '{fns[0]}' (source unavailable or the "
                      "required extractor is not installed)", confidence=0.0)
    labels, edges = graph
    if len(labels) > _EXPRESSION_MAX_NODES:
        return refuse(f"function '{fns[0]}' has {len(labels)} {layer}-layer nodes (> "
                      f"{_EXPRESSION_MAX_NODES}); too large to read as a matrix", confidence=0.0,
                      node_count=len(labels))
    seen: dict[tuple[int, int, str], None] = {}
    for s, d, k in edges:  # collapse duplicate (src, dst, kind) triples
        seen.setdefault((int(s), int(d), k), None)
    # Emit cells in a deterministic order so the payload is byte-reproducible across
    # processes regardless of how a frontend ordered its edges (R205 — a deep-layer
    # builder that iterates a set would otherwise leak PYTHONHASHSEED order here).
    cells = [{"src": s, "dst": d, "k": k} for (s, d, k) in sorted(seen)]
    payload: dict = {
        "layer": layer,
        "function": node.id.split("::", 1)[-1],
        "labels": labels,       # statements (statement) / ops (expression), by index
        "cells": cells,         # sparse (src_index, dst_index, kind): d/c (expr) or C/D (stmt)
        "n": len(labels),
    }
    if len(labels) <= 12:
        grid = [[0] * len(labels) for _ in labels]
        for s, d, _k in seen:
            grid[s][d] = 1
        payload["grid"] = grid
    return ok(payload, layer=layer, nodes=len(labels), edges=len(cells))


@operation("A bounded relation submatrix for one subsystem (compact, for an LLM).")
def get_matrix(store: Store, scope: str, relation: str = "CALLS",
               limit: int = 25, layer: str = "call") -> Result:
    """Return a *bounded* sparse submatrix for the nodes under `scope` (an id
    prefix, e.g. a file or class), for one relation (design §8).

    Never the whole-repo N×N matrix — that's the dense anti-pattern (design §12).
    Refuses when the scope exceeds `limit` so the result stays small enough for an
    LLM to actually reason over.

    `layer` selects the granularity (design §5c), coarse→fine: "call" (default) is
    the inter-procedural relation graph; "statement" drills into a SINGLE function's
    program-dependence graph (labels = statements, cells tagged C=control / D=data
    dependence — Python + the JS family + Go + Rust + C/C++ + Java + C# + Ruby so far); "expression" drills into its intra-procedural
    value-flow graph (labels = operations, cells tagged data/control). The deeper
    layers are advisory and computed on demand — they never feed liveness.
    """
    # Validate arg types BEFORE using them — relation.upper()/startswith()/`> limit` would
    # otherwise raise on None/wrong-type from a library or MCP call (panel R18B).
    if not isinstance(scope, str):
        return refuse("scope must be a string", confidence=0.0)
    if not isinstance(relation, str):
        return refuse("relation must be a string", confidence=0.0)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        # aligned with find_component's check (self-review round 2): the three
        # refuse-style limit validations had drifted — limit=0/-5 passed here
        # and produced degenerate output (empty slice / a matrix op refusing
        # every non-empty scope) while the sibling op refused cleanly.
        return refuse("limit must be a positive integer", confidence=0.0)
    if not isinstance(layer, str):
        return refuse("layer must be a string", confidence=0.0)
    layer = layer.lower()
    if layer not in (Layer.CALL.value, Layer.STATEMENT.value, Layer.EXPRESSION.value):
        return refuse(f"unknown layer '{layer}' (call|statement|expression)", confidence=0.0)
    if layer in (Layer.STATEMENT.value, Layer.EXPRESSION.value):
        return _body_matrix(store, scope, layer)
    try:
        rel = Relation(relation.upper())
    except ValueError:
        return refuse(f"unknown relation '{relation}'", confidence=0.0)

    members = sorted(nid for nid in store.all_node_ids() if _under_scope(nid, scope))
    if not members:
        return refuse(f"no nodes under scope '{scope}'", confidence=0.0)
    if len(members) > limit:
        return refuse(
            f"scope has {len(members)} nodes (> limit {limit}); narrow the scope "
            "(e.g. a single file or class) — full matrices are the dense anti-pattern",
            confidence=0.0, node_count=len(members))

    idx = {nid: i for i, nid in enumerate(members)}
    # One cell per (src, dst): collapse repeated call sites to a single edge (keep the
    # max weight) so the sparse list and `density` don't double-count — the dense grid
    # is already idempotent.
    cell_w: dict[tuple[int, int], float] = {}
    for e in store.resolved_edges(rel):
        if e.src in idx and e.dst_id in idx:
            k = (idx[e.src], idx[e.dst_id])
            cell_w[k] = max(cell_w.get(k, 0.0), round(e.weight, 2))
    cells = [{"src": s, "dst": d, "w": w} for (s, d), w in sorted(cell_w.items())]
    labels = [m.split("::", 1)[-1] for m in members]
    payload = {
        "layer": Layer.CALL.value,
        "relation": rel.value,
        "labels": labels,
        "cells": cells,        # sparse (src_index, dst_index, weight)
        "n": len(members),
    }
    # A small dense 0/1 grid is easy for an LLM to read directly.
    if len(members) <= 12:
        grid = [[0] * len(members) for _ in members]
        for c in cells:
            grid[int(c["src"])][int(c["dst"])] = 1
        payload["grid"] = grid  # type: ignore[assignment]
    return ok(payload, density=f"{len(cells)}/{len(members)**2}")


@operation("Incrementally (re)index a path into the graph (admin).")
def reindex(store: Store, path: str, precise: bool = False,
            streaming: bool | None = None, lsp: bool | None = None) -> Result:
    """Extract a Python project into the graph (design §0/§1). Writes only to the
    index — never to source (read-only invariant).

    precise=True adds the jedi resolver (LSP-grade go-to-definition, design §5):
    slower, needs jedi installed, but sharpens method/attribute resolution.

    lsp (tri-state, AUTO by default since v3.48.0): the language-server
    resolver (research/24) — go-to-definition upgrades for the tree-sitter
    languages via external server binaries (rust-analyzer,
    typescript-language-server, gopls, clangd; `[lsp.servers]` overrides).
    AUTO runs it whenever a matching server is already installed — the best
    available analysis is the default, and machines without servers fall back
    silently to the name-based graph. `--no-lsp` / `lsp=False` /
    `STITCHGRAPH_NO_LSP=1` / `[lsp] enabled = false` opt out; `lsp=True`
    forces it and reports missing servers loudly. Missing/broken servers can
    only cost coverage, never an error or a wrong edge.

    `streaming` lowers the extraction memory peak (v2) and produces an index BYTE-IDENTICAL to
    the in-memory path — pinned by the streaming differential oracle. It (a) drops each file's
    AST/parse-tree after pass 1 and (b) streams edges straight to SQLite (deduped per-source on
    the fly) instead of building the full Python edge list — the dominant hog on big repos
    (~15.5M edges → ~4 GB on a Magento module; streaming holds peak ≈ node objects + one file).

    Tri-state:
      * None (default) — AUTO: stream when the store is on-disk AND the tree is large
        (>= `_STREAM_AUTO_FILES` code files). Small repos use the slightly faster in-memory
        path; large repos get the memory-safe streaming path automatically.
      * True / False — force the streaming or in-memory path.
    Streaming only saves memory with an on-disk Store (a `:memory:` DB holds the rows in RAM
    regardless), so AUTO never picks it for `:memory:`.
    """
    import os

    from .config import load_config
    from .extract import extract_project
    from .resolve import default_resolvers, run_resolvers

    # A hostile or non-directory root (over-long path, embedded NUL, lone surrogate, or a
    # missing path) must degrade to an empty index like a missing path — NOT crash mid-extract
    # on a stat()/is_file()/meta-bind (panels YYY/ZZZ/crash-sweep). Probe once up front;
    # every downstream Path op on the root is then known-safe.
    try:
        usable = isinstance(path, str) and os.path.isdir(path)
        abs_root = os.path.abspath(path) if isinstance(path, str) else ""
        abs_root.encode("utf-8")  # a surrogate/NUL root can't be stored as meta
    except (OSError, ValueError, UnicodeError, TypeError):
        # A non-str path (None / bytes / wrong type from a library or MCP call) or an
        # unusable string root degrades to an empty index, never raises (panels R17A/R18B).
        # `bytes` is excluded up front: abspath(bytes) returns bytes, whose .encode is an
        # AttributeError the probe wouldn't otherwise catch.
        usable, abs_root = False, ""
    if not usable:
        # An invalid root must NEVER destroy an existing index: a one-character typo
        # (`reindex srcc`), a wrong cwd, or a deleted directory would otherwise wipe a
        # multi-minute index and report success (review 2026-07-03, F1). With content
        # present, refuse and leave the store untouched. Only a store with nothing to
        # lose keeps the historical degrade-to-empty contract (panels R17A/YYY/ZZZ:
        # hostile/missing roots must not raise).
        if store.node_count() > 0:
            return refuse(
                f"root {path!r} is not a readable directory — the existing index "
                "was left untouched; pass a valid project root to rebuild it")
        with store.conn:
            store.conn.execute("DELETE FROM nodes")
            store.wipe_edges()
        store.bump_generation()
        store.set_meta("root", abs_root)
        return ok({"files": 0, "nodes": 0, "holes": 0}, files=0, nodes=0)

    if streaming is None:
        streaming = _auto_stream(path, store)

    resolvers = default_resolvers()
    if precise:
        from .resolve.jedi_resolver import JediResolver
        resolvers.append(JediResolver())
    cfg = load_config(path)
    lsp_mode = _lsp_mode(lsp, cfg)
    lsp_forced = lsp_mode is True
    if lsp_mode is not False:
        from .resolve.lsp import any_server_available
        from .resolve.lsp_resolver import LspResolver
        # AUTO (None): attach the resolver only when some server binary is
        # actually installed — the pass then covers those languages and the
        # rest keep the name-based graph; forced True attaches regardless so
        # missing binaries decline LOUDLY in the report.
        if lsp_forced or any_server_available(cfg.lsp_servers):
            resolvers.append(LspResolver(servers=cfg.lsp_servers,
                                         timeout=cfg.lsp_timeout))

    # [index] edge_compression gates NEW compression for this rebuild and every
    # later replace_file on the store; the env kill switch always wins.
    store.edge_compression = (store._compression_env_ok
                              and load_config(path).edge_compression)

    if streaming:
        return _reindex_streaming(store, path, abs_root, load_config(path).ignore,
                                  resolvers, lsp_forced=lsp_forced)

    xreport: dict = {}
    nodes, edges = extract_project(path, ignore=load_config(path).ignore,
                                   cache_asts=not streaming, report=xreport)
    # Cross-language / framework enrichment (routes, SQL — design §2a), plus the
    # optional jedi precision pass.
    nodes, edges = run_resolvers(path, nodes, edges, resolvers)
    edges = _dedup_edges(edges)
    files = {n.id.split("::", 1)[0] for n in nodes if "::" in n.id}
    # Full rebuild: the extractor already resolved every edge against the complete
    # symbol table, so bulk-insert (nodes first) and keep those resolutions rather
    # than re-running per-file invalidation. (replace_file remains for single-file
    # incremental updates, design §4.)
    with store.conn:
        store.conn.execute("DELETE FROM nodes")
        store.wipe_edges()
        for n in nodes:
            store.add_node(n)
        # Ingest-time compression (research/20): this list is final (deduped,
        # override-propagated), so eligible widened fan-outs are written as
        # interned groups directly — at framework-Python density that is >90%
        # of the rows this loop used to insert.
        store.insert_edges_compressed(edges)
        _persist_symtab(store, xreport)

    store.analyze()
    store.bump_generation()
    store.set_meta("root", abs_root)
    holes = len(store.unresolved_edges())
    res = ok({"files": len(files), "nodes": store.node_count(), "holes": holes},
             files=len(files), nodes=store.node_count())
    _annotate_extraction_gaps(res, xreport)
    _annotate_lsp(res, resolvers, forced=lsp_forced)
    return res


def _annotate_lsp(res: Result, resolvers, forced: bool = True) -> None:
    """Surface the LSP pass's honesty counters (sites asked / resolved /
    declines) on the reindex result — the pass must never pretend coverage
    it didn't have (research/24). Declines become review reasons when the
    pass was FORCED (`lsp=True`) — under AUTO a missing server is the
    expected fallback, not something to flag — OR when the binary is on PATH
    but broken (`broken_binary`, e.g. rustup's uninstalled rust-analyzer
    proxy shim): AUTO only attached the pass because that binary looked
    installed, so its failure must be loud and carry the fix, not silently
    degrade to the name-based graph (field review 2026-07-09, request 1)."""
    for r in resolvers:
        if getattr(r, "name", "") == "lsp" and getattr(r, "report", None):
            if isinstance(res.result, dict):
                res.result["lsp"] = r.report
            for cmd, stats in r.report.items():
                if stats.get("declined") and (forced or stats.get("broken_binary")):
                    res.add_reason(f"lsp: {cmd.split()[0]} declined "
                                   f"({stats['declined']})",
                                   code=ReviewCode.LSP_UNAVAILABLE)


def _lsp_edges_for(path: str, cfg, nodes: list, edges: list,
                   only_files: set[str] | None = None) -> list:
    """The scoped LSP pass shared by the incremental paths: resolve the
    name-based CALLS sites of `only_files` (or all files when None) and return
    the extra EXTRACTED edges. Total: any failure — no servers, mode off,
    resolver error — returns []."""
    lsp_mode = _lsp_mode(None, cfg)
    if lsp_mode is False:
        return []
    from .resolve.lsp import any_server_available
    from .resolve.lsp_resolver import LspResolver
    if not (lsp_mode is True or any_server_available(cfg.lsp_servers)):
        return []
    rows = [(e.src, e.dst_symbol, e.location, e.provenance is Provenance.AMBIGUOUS)
            for e in edges
            if (e.relation is Relation.CALLS and e.source == "tree-sitter"
                and e.dst_symbol is not None
                and (only_files is None
                     or e.src.split("::", 1)[0] in only_files))]
    if not rows:
        return []
    try:
        return LspResolver(servers=cfg.lsp_servers,
                           timeout=cfg.lsp_timeout).resolve_rows(path, nodes, rows)
    except Exception:  # noqa: BLE001 — precision adds, never breaks (jedi contract)
        return []


def _lsp_mode(lsp: bool | None, cfg) -> bool | None:
    """Resolve the effective LSP switch: explicit param > STITCHGRAPH_NO_LSP
    env kill switch > `[lsp] enabled` config > AUTO (None). AUTO is the
    default since v3.48.0: the best available analysis runs by default —
    servers already installed are used, missing ones fall back silently to
    the name-based graph (the same full-power-by-default contract as the
    v3.31.0 install story; field review 2026-07-07, docs/LLM_REVIEW.md)."""
    import os
    if lsp is not None:
        return lsp
    if os.environ.get("STITCHGRAPH_NO_LSP"):
        return False
    return cfg.lsp_enabled


@operation("Type of the symbol at a source position, via the language server (needs --lsp servers).")
def type_at(store: Store, file: str, line: int, col: int = 0) -> Result:
    """Hover-grade type information at (file, 1-based line, 0-based col) —
    research/24's on-demand companion to the `--lsp` reindex pass.

    Spawns the file's language server for one question and shuts it down.
    Refuses honestly when no server covers the extension, the binary is
    missing, or the server has no answer — never guesses."""
    import os
    from pathlib import Path

    from .config import load_config
    from .resolve.lsp import LspClient, server_for

    root = store.get_meta("root")
    if not root or not os.path.isdir(root):
        return refuse("no indexed root on this store — run reindex first")
    rel = file.replace("\\", "/")
    if not os.path.isfile(os.path.join(root, rel)):
        return refuse(f"{rel!r} is not a file under the indexed root {root!r}")
    cfg = load_config(root)
    server = server_for(Path(rel).suffix, cfg.lsp_servers)
    if server is None:
        return refuse(
            f"no language server is registered for '{Path(rel).suffix}' files — "
            "add one under [lsp.servers] in stitchgraph.toml")
    cmd, language_id = server
    client = LspClient(cmd, root, timeout=cfg.lsp_timeout)
    if not client.start():
        from .resolve.lsp import diagnose_server
        diag, _present = diagnose_server(cmd)
        return refuse(f"language server unavailable: {diag}")
    try:
        if not client.did_open(rel, language_id):
            return refuse(f"could not open {rel!r} on the server")
        client.warm_up(rel, line, col)
        text = client.hover(rel, line, col)
    finally:
        client.stop()
    if text is None:
        return refuse(f"the server has no type information at {rel}:{line}:{col}",
                      confidence=0.0)
    return ok({"file": rel, "line": line, "col": col, "type": text,
               "server": cmd.split()[0]},
              provenance=Provenance.EXTRACTED)


def reindex_incremental(store: Store, path: str, changed: set[str]) -> Result:
    """Differential re-index for `watch` (v3.38.0 — the roadmap's "wire replace_file
    to watch"). Extraction runs WHOLE-PROJECT in memory — identical resolution
    semantics to a full reindex, so every convergence oracle keeps holding by
    construction — but store writes happen only for the owners whose graph content
    can have changed: `changed` (root-relative posix paths of mtime-added/modified
    files) plus every PSEUDO owner (aggregates like `db`/`event` whose nodes are
    derived from many source files and can gain/lose members on any edit). Each
    owner goes through `Store.replace_file`, whose worklist re-resolve, name-based
    re-widening, override propagation and dangling invalidation are already pinned
    to converge with a full reindex (LIMITATIONS "Incremental replace_file matches
    a full reindex").

    The win is skipping the full-table rewrite (delete + N-million-row insert +
    dedup endgame), which dominates reindex wall time on mid-size repos — the edit
    loop pays extraction only.

    Callers own the fallback decisions (the CLI `watch` loop does): file DELETIONS
    must full-reindex (the two documented non-cardinal deletion residuals stay out
    of shipped surfaces), and trees big enough for AUTO-streaming must full-reindex
    (in-memory whole-project extraction is exactly what streaming exists to avoid).
    A store/root mismatch or an unusable root also belongs to the caller — this
    function assumes the same validated root `reindex` would accept."""
    import os

    from .config import load_config
    from .extract import extract_project
    from .resolve import default_resolvers, run_resolvers

    if not (isinstance(path, str) and os.path.isdir(path)):
        return refuse(f"root {path!r} is not a readable directory — nothing was changed")
    abs_root = os.path.abspath(path)

    cfg = load_config(path)
    xreport: dict = {}
    nodes, edges = extract_project(path, ignore=cfg.ignore, report=xreport)
    nodes, edges = run_resolvers(path, nodes, edges, default_resolvers())
    # Scoped LSP pass over the CHANGED files only (adversarial self-audit
    # 2026-07-07, docs/BUG_HUNT_PROMPT.md class 5): under the v3.48.0 AUTO
    # default a fresh reindex carries source="lsp" edges, so an incremental
    # update that skipped the pass would silently strip them from every edited
    # file — breaking the incremental==fresh convergence contract exactly
    # where the suite can't see it (tests pin STITCHGRAPH_NO_LSP=1). Scoping
    # to the changed files keeps the watch edit-loop cost at one server
    # session over a handful of sites; unchanged files keep their stored LSP
    # edges because their rows aren't replaced.
    edges = edges + _lsp_edges_for(path, cfg, nodes, edges, only_files=set(changed))
    edges = _dedup_edges(edges)

    nodes_by_owner: dict[str, list] = {}
    for n in nodes:
        nodes_by_owner.setdefault(n.id.split("::", 1)[0], []).append(n)
    edges_by_owner: dict[str, list] = {}
    for e in edges:
        edges_by_owner.setdefault(e.src.split("::", 1)[0], []).append(e)
    owners = set(nodes_by_owner) | set(edges_by_owner)
    # Pseudo owners (`db`, `event`, spec aggregates): not real files, so mtime can't
    # vouch for them — refresh unconditionally (their groups are tiny).
    pseudo = {o for o in owners if not os.path.exists(os.path.join(abs_root, o))}
    # A changed file that now produces nothing (emptied, or all defs removed) is not
    # an owner in the fresh extract — replacing it with () clears its stale rows.
    targets = sorted((changed & owners) | (changed - owners) | pseudo)

    # The complete exported-role surface from the SAME whole-project extract, so an
    # edit that changes a package __init__'s re-exports converges (panel R37A — the
    # exact contract replace_file's docstring asks incremental callers to honour).
    exported_ids = {n.id for n in nodes if "exported" in n.roles}
    xsymtab = xreport.get("symtab") or {}
    for owner in targets:
        store.replace_file(owner, nodes_by_owner.get(owner, ()),
                           edges_by_owner.get(owner, ()), exported_ids=exported_ids,
                           symtab=xsymtab.get(owner))

    store.analyze()
    store.set_meta("root", abs_root)  # replace_file bumped the generation per owner
    res = ok({"files": len({n.id.split('::', 1)[0] for n in nodes if '::' in n.id}),
              "nodes": store.node_count(), "replaced": len(targets),
              "holes": store.unresolved_count()},
             files=len(changed), nodes=store.node_count(), replaced=len(targets))
    _annotate_extraction_gaps(res, xreport)
    return res


def reindex_singlefile(store: Store, path: str, changed: set[str]) -> Result | None:
    """The single-file fast path (research/21 stage C): extract ONLY the changed
    Python files against the persisted symbol table and land them via
    `replace_file`. Returns None when NOT APPLICABLE — the caller falls back to
    `reindex_incremental` / full reindex — so every gate here is conservative:

    * every changed file must be an existing, parseable `.py` file;
    * the index must carry the persisted symbol table (a pre-3.43 index doesn't);
    * neither the file's OLD graph rows nor its NEW content may involve the
      cross-language resolvers (route/event/ORM/SQL shapes, spec-derived
      artifacts) — those still need whole-project context, and a missed
      resolver edge would silently diverge from a full reindex.
    """
    import ast
    import os

    from .extract.single import extract_single_file

    if not (isinstance(path, str) and os.path.isdir(path)):
        return None
    if not changed or any(not rel.endswith(".py") for rel in changed):
        return None
    if store.get_meta("packages") is None:
        return None  # index predates the persisted symbol table: full path only
    abs_root = os.path.abspath(path)
    parsed: list[tuple[str, ast.Module]] = []
    for rel in sorted(changed):
        p = os.path.join(abs_root, rel)
        if not os.path.isfile(p):
            return None  # deletions/renames keep the documented full fallback
        try:
            with open(p, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (SyntaxError, UnicodeDecodeError, OSError, RecursionError):
            return None  # let the whole-project path apply its skip machinery
        if _resolver_sensitive(tree) or _resolver_artifacts_touch(store, rel):
            return None
        parsed.append((rel, tree))

    from .config import load_config as _load_config
    cfg = _load_config(path)
    for rel, _tree in parsed:
        try:
            nodes, edges, contribution = extract_single_file(store, abs_root, rel)
        except (SyntaxError, OSError, RecursionError):
            return None
        exported_ids = _exported_ids_for_single(store, rel, nodes, edges,
                                                contribution)
        # Scoped LSP convergence (self-audit 2026-07-07): a no-op unless the
        # user registered a server for .py (this fast path is Python-only and
        # .py has no default server) — but with one registered, the edited
        # file must keep the LSP edges a fresh reindex would give it. Span
        # index over the store's full node list: definitions can land in any
        # file. The extra edges are deduped by replace_file's own pipeline.
        lsp_extra = _lsp_edges_for(path, cfg, store.all_nodes_full(), edges,
                                   only_files={rel})
        store.replace_file(rel, nodes, edges + lsp_extra,
                           exported_ids=exported_ids, symtab=contribution)
    store.analyze()
    store.set_meta("root", abs_root)
    return ok({"replaced": len(parsed), "nodes": store.node_count(),
               "holes": store.unresolved_count(), "mode": "single-file"},
              files=len(parsed), nodes=store.node_count(), replaced=len(parsed))


def _resolver_sensitive(tree) -> bool:
    """AST gate mirroring each Python-relevant resolver's OWN trigger shape —
    faithful supersets, not keyword soup (an earlier draft gated on any `.get(`
    call, which is every Python file). False positives only cost the fast path;
    a false negative would silently diverge, so each check re-states the
    resolver's firing condition:

    * routes `_route_of`: a DECORATOR that is an attribute call on a verb with
      a string-literal first argument (`@app.get("/x")`); plus Django URLconf
      `path/re_path/url(...)` calls and Flask `.add_url_rule(...)`.
    * events `_handle_call`: an attribute call WITH args whose attr is an emit
      verb (any arg shape — receiver-keyed emits fire too), or a handle verb
      with a string event + handler, or a signal-connect with a callback.
    * sql `_sql_literals`: a string literal matching the resolver's `_SQL_RE`
      (reused verbatim); skipped entirely when sqlglot is absent (the resolver
      is a no-op then).
    * orm: a class with a Model/Base/DeclarativeBase-named base (superset of
      the SQLAlchemy/Django model detection)."""
    import ast
    import re

    from .resolve.events import _EMIT, _HANDLE, _SIGNAL_CONNECT
    from .resolve.routes import _VERBS
    try:
        from .resolve.sql import _HAVE_SQLGLOT, _SQL_RE
    except ImportError:  # pragma: no cover - sql module always importable
        _HAVE_SQLGLOT = False
        _SQL_RE: re.Pattern[str] | None = None  # type: ignore[no-redef]
    urlconf = {"path", "re_path", "url"}
    orm_bases = {"Model", "Base", "DeclarativeBase"}

    def _is_str(a) -> bool:
        return isinstance(a, ast.Constant) and isinstance(a.value, str)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in _VERBS
                        and dec.args and _is_str(dec.args[0])):
                    return True
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                if f.attr == "add_url_rule":
                    return True
                if node.args and f.attr in _EMIT:
                    return True
                if node.args and f.attr in _HANDLE and (
                        (len(node.args) >= 2 and _is_str(node.args[0]))
                        or f.attr in _SIGNAL_CONNECT):
                    return True
            elif isinstance(f, ast.Name) and f.id in urlconf and len(node.args) >= 2:
                return True
        elif isinstance(node, ast.ClassDef):
            for b in node.bases:
                nm = b.attr if isinstance(b, ast.Attribute) else (
                    b.id if isinstance(b, ast.Name) else None)
                if nm in orm_bases:
                    return True
        elif (_HAVE_SQLGLOT and _SQL_RE is not None
                and isinstance(node, ast.Constant) and isinstance(node.value, str)
                and _SQL_RE.match(node.value)):
            return True
    return False


def _resolver_artifacts_touch(store: Store, rel: str) -> bool:
    """True when resolver-derived rows (source beyond the two extractors) are
    attributed to this file or point into it — re-deriving those needs the
    whole-project resolver pass, so the fast path must decline."""
    row = store.conn.execute(
        """SELECT 1 FROM edges
            WHERE source NOT IN ('ast', 'tree-sitter')
              AND (file = ? OR (dst_id IS NOT NULL AND dst_id IN
                   (SELECT id FROM nodes WHERE file = ?))) LIMIT 1""",
        (rel, rel)).fetchone()
    if row:
        return True
    return store.conn.execute(
        """SELECT 1 FROM edge_groups g
            WHERE g.source NOT IN ('ast', 'tree-sitter')
              AND (g.file = ? OR EXISTS
                   (SELECT 1 FROM cand_members m JOIN nodes n ON n.id = m.dst_id
                     WHERE m.set_id = g.set_id AND n.file = ?)) LIMIT 1""",
        (rel, rel)).fetchone() is not None


def _exported_ids_for_single(store: Store, rel: str, fresh_nodes: list,
                             fresh_edges: list,
                             contribution: dict[str, set[str]]) -> set[str]:
    """The COMPLETE post-edit `exported` surface for replace_file's exact-match
    contract, from the store + the fresh extract: `_apply_entrypoint_roles`'s
    name/kind rule, public methods of exported classes, and
    `_seed_exported_inherited_methods`'s first-party ancestor closure."""
    from .model import Relation
    union = store.symtab_names("export", exclude_file=rel)
    union |= contribution.get("export", set())
    fn, mth, cls = (NodeKind.FUNCTION.value, NodeKind.METHOD.value,
                    NodeKind.CLASS.value)
    universe = [(r["id"], r["kind"], r["name"]) for r in store.conn.execute(
        "SELECT id, kind, name FROM nodes WHERE file != ?", (rel,))
        if isinstance(r["id"], str) and isinstance(r["name"], str)]
    universe += [(n.id, n.kind.value, n.name) for n in fresh_nodes]
    ids: set[str] = set()
    class_ids: set[str] = set()
    exported_classes: set[str] = set()
    for nid, kind, name in universe:
        if kind == cls:
            class_ids.add(nid)
            if name in union:
                exported_classes.add(nid)
        if name in union and kind in (fn, mth, cls):
            ids.add(nid)
    # First-party ancestor closure of exported classes (INHERITS, both sides
    # resolved to known classes) — store rows for other files + fresh edges.
    bases: dict[str, set[str]] = {}
    inh = Relation.INHERITS.value
    for src, dst in store.conn.execute(
            """SELECT src, dst_id FROM edges_all
                WHERE relation = ? AND file != ?""", (inh, rel)):
        if src in class_ids and dst in class_ids and src != dst:
            bases.setdefault(src, set()).add(dst)
    for e in fresh_edges:
        if (e.relation is Relation.INHERITS and e.dst_id
                and e.src in class_ids and e.dst_id in class_ids
                and e.src != e.dst_id):
            bases.setdefault(e.src, set()).add(e.dst_id)
    ancestors: set[str] = set()
    stack = [b for cid in exported_classes for b in bases.get(cid, ())]
    while stack:
        a = stack.pop()
        if a not in ancestors:
            ancestors.add(a)
            stack.extend(bases.get(a, ()))
    owner_ok = exported_classes | ancestors
    for nid, kind, name in universe:
        if (kind == mth and "." in nid and not name.startswith("_")
                and nid.rsplit(".", 1)[0] in owner_ok):
            ids.add(nid)
    return ids


def _unused_params(store: Store) -> list[dict]:
    """Parameters never loaded in their function's own body (design §6.E) —
    a scan-time, source-derived advisory. Exclusions, by design rather than
    hedging: `self`/`cls`, `_`-prefixed (the idiomatic 'intentionally unused'
    marker), `*args`/`**kwargs` (pass-through shape), and any function that is
    a stub, abstract, a `callback`-role framework override, or overrides a
    first-party base method (the signature is the INTERFACE's, not the
    function's to slim). Python files only; missing/unparseable sources are
    silently skipped (scan must never fail on a stale index)."""
    import ast as _ast
    import os

    root = store.get_meta("root") or ""
    out: list[dict] = []
    # Overriding methods: same member name defined on any INHERITS ancestor.
    bases: dict[str, list[str]] = {}
    inh = Relation.INHERITS.value
    for src, dst in store.conn.execute(
            "SELECT src, dst_id FROM edges_all WHERE relation = ?", (inh,)):
        if isinstance(src, str) and isinstance(dst, str) and src != dst:
            bases.setdefault(src, []).append(dst)

    def overrides_base(fn_id: str) -> bool:
        owner, _, method = fn_id.rpartition(".")
        if "::" not in owner:
            return False
        seen: set[str] = set()
        stack = list(bases.get(owner, ()))
        while stack:
            b = stack.pop()
            if b in seen:
                continue
            seen.add(b)
            if store.get_node(f"{b}.{method}") is not None:
                return True
            stack.extend(bases.get(b, ()))
        return False

    trees: dict[str, _ast.Module | None] = {}
    # Two passes (research/25 dogfood): pass 1 collects candidates AND, for every
    # (leaf name, arity) family, which params ANY member loads — the ten
    # structure_*.py grammars share `_walk(lang)` / `_build_pdg(data)` signatures
    # where only some languages use every slot, and a param a sibling DOES use is
    # the family's interface, not this member's dead weight. Pass 2 filters.
    family_used: dict[tuple[str, int], set[str]] = {}
    family_count: dict[tuple[str, int], int] = {}
    candidates: list[tuple[str, tuple[str, int], list[str]]] = []
    # Functions referenced AS A VALUE (passed to a dispatcher, stored in a
    # table) have caller-owned signatures: the ten `_build_pdg(fn, data)`
    # grammar builders are invoked uniformly by one shared traversal, so a
    # slot only the CALLER needs still isn't the member's to slim
    # (research/25). The graph already knows: an incoming REFERENCES edge.
    value_refd = {r[0] for r in store.conn.execute(
        "SELECT DISTINCT dst_id FROM edges_all "
        "WHERE relation = ? AND dst_id IS NOT NULL",
        (Relation.REFERENCES.value,))}
    for node in store.all_nodes_full():
        if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD) or node.is_stub:
            continue
        if "callback" in node.roles:
            continue
        rel_path = node.id.split("::", 1)[0]
        if not rel_path.endswith(".py"):
            continue
        if rel_path not in trees:
            try:
                with open(os.path.join(root, rel_path), encoding="utf-8") as f:
                    trees[rel_path] = _ast.parse(f.read())
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                trees[rel_path] = None
        tree = trees[rel_path]
        if tree is None:
            continue
        qual = node.id.split("::", 1)[1]
        fn = _find_def(tree, qual)
        if fn is None:
            continue
        pos_args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        loaded = {n.id for n in _ast.walk(fn)
                  if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load)}
        key = (fn.name, len(pos_args) + bool(fn.args.vararg) + bool(fn.args.kwarg))
        # every parsed def is usage EVIDENCE for its family, even ones the
        # candidate gates below skip — an abstract base that loads a param
        # still proves the slot is the family's contract
        family_count[key] = family_count.get(key, 0) + 1
        family_used.setdefault(key, set()).update(
            a.arg for a in pos_args if a.arg in loaded)
        if _is_interface_like(fn) or _framework_owns_signature(fn) \
                or node.id in value_refd:
            continue
        params = [a.arg for a in pos_args
                  if a.arg not in ("self", "cls") and not a.arg.startswith("_")]
        unused = [p for p in params if p not in loaded]
        if not unused:
            continue
        if node.kind is NodeKind.METHOD and overrides_base(node.id):
            continue
        candidates.append((node.id, key, unused))
    for nid, key, unused in candidates:
        kept = ([p for p in unused if p not in family_used[key]]
                if family_count[key] > 1 else unused)
        if not kept:
            continue
        out.append({
            "kind": "unused_params", "node": nid, "params": sorted(kept),
            "urgency": Urgency.GREEN.value,
            "reason": (f"parameter(s) never used in the body: "
                       f"{', '.join(sorted(kept))}"),
        })
    return sorted(out, key=lambda i: i["node"])


# Decorators that never consume a function's signature. Anything else —
# @operation (the registry calls with the contract shape), @app.command /
# typer callbacks (params filled by introspection), @pytest.fixture, route
# registrations — makes the signature the FRAMEWORK's, so an "unused" param
# may be very much in use (research/25: 10 of 52 self-findings were exactly
# this). Precision-biased: an unknown decorator suppresses the advisory.
_SIGNATURE_SAFE_DECORATORS = frozenset({
    "staticmethod", "classmethod", "property", "cached_property",
    "override", "final", "abstractmethod", "abstractproperty", "overload",
})


def _framework_owns_signature(fn) -> bool:
    import ast as _ast
    for dec in fn.decorator_list:
        base = dec.func if isinstance(dec, _ast.Call) else dec
        if isinstance(base, _ast.Attribute):
            name = base.attr          # @functools.cached_property -> cached_property
        elif isinstance(base, _ast.Name):
            name = base.id
        else:
            name = None               # something exotic: treat as framework-owned
        if name not in _SIGNATURE_SAFE_DECORATORS:
            return True
    return False


def _find_def(tree, qual: str):
    """Locate the def matching a dotted qualname (mirrors _def_node's quals:
    control-flow blocks add no level; classes and defs do)."""
    import ast as _ast
    parts = qual.split(".")

    def walk(node, idx):
        for child in _ast.iter_child_nodes(node):
            if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                  _ast.ClassDef)):
                if child.name != parts[idx]:
                    continue
                if idx == len(parts) - 1:
                    if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        return child
                    continue
                got = walk(child, idx + 1)
                if got is not None:
                    return got
            else:
                got = walk(child, idx)
                if got is not None:
                    return got
        return None

    return walk(tree, 0)


def _is_interface_like(fn) -> bool:
    """A def whose body is a stub/ellipsis/docstring-only, or that is decorated
    abstract/overload — its signature is an interface contract, not dead weight."""
    import ast as _ast
    for d in fn.decorator_list:
        name = d.attr if isinstance(d, _ast.Attribute) else (
            d.id if isinstance(d, _ast.Name) else None)
        if name in ("abstractmethod", "abstractproperty", "overload"):
            return True
    body = [s for s in fn.body
            if not (isinstance(s, _ast.Expr) and isinstance(s.value, _ast.Constant))]
    if not body:
        return True
    if len(body) == 1:
        only = body[0]
        if isinstance(only, _ast.Pass):
            return True
        if isinstance(only, _ast.Raise):
            return True
    return False


def _persist_symtab(store: Store, xreport: dict) -> None:
    """Write the extractor's per-file symbol-table record + the import-internality
    meta (research/21). Both reindex paths call this after nodes land, so a later
    single-file re-extraction can rebuild every cross-file union from the store."""
    import json as _json
    store.replace_symtab_all(xreport.get("symtab") or {})
    store.conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('packages', ?)",
        (_json.dumps(xreport.get("packages") or []),))
    store.conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('source_prefix', ?)",
        (xreport.get("source_prefix") or "",))


def _annotate_extraction_gaps(res: Result, xreport: dict) -> None:
    """Surface extraction gaps on the reindex Result — a file missing from the graph
    must NEVER be silent (research/18 bug 1: 880 PEP 695 files — 10% of Home Assistant,
    half its test-executed functions — vanished without a count, a warning, or a meta
    key; every downstream answer quietly excluded them). `skipped` files are absent
    from the graph: name them and flag review. `fallback` files were rescued by the
    tree-sitter Python grammar at structural fidelity — a count, not a review flag."""
    skipped = xreport.get("skipped") or []
    fallback = xreport.get("fallback") or []
    if fallback:
        res.meta["python_fallback_files"] = len(fallback)
    if skipped:
        res.meta["skipped_files"] = len(skipped)
        shown = ", ".join(f"{rel} ({why})" for rel, why in skipped[:5])
        more = f" (+{len(skipped) - 5} more)" if len(skipped) > 5 else ""
        res.needs_review = True
        res.add_reason(
            f"{len(skipped)} file(s) could not be parsed and are MISSING from the graph: "
            f"{shown}{more}. A SyntaxError under an interpreter older than the code's "
            f"syntax level is the common cause — reindex with a newer Python or install "
            f"tree-sitter for the fallback grammar. Every answer from this index silently "
            f"excludes these files.", code=ReviewCode.PARSE_FAILURE)


_EDGE_INSERT_SQL = (
    "INSERT INTO edges(src, relation, dst_symbol, dst_id, weight, provenance, "
    "location, source, file, name_based) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

# AUTO-streaming threshold: at/above this many indexable source files, `reindex` switches to
# the constant-memory streaming path (on-disk stores only). Below it, the in-memory path is
# slightly faster and the peak is modest. Tuned from the Magento hunt: ~2k dense files already
# push the in-memory path toward ~1 GB, and streaming's ~40% time cost is negligible in
# absolute terms on a tree that small — so erring toward streaming earlier is cheap insurance.
_STREAM_AUTO_FILES = 2000


def _auto_stream(path: str, store: Store) -> bool:
    """Decide whether AUTO mode (`streaming=None`) should stream. Streams only for an on-disk
    store (a `:memory:` DB keeps rows in RAM, so streaming saves nothing there) with a large
    source tree. The count short-circuits at the threshold, so this is O(threshold), not
    O(repo) — a cheap probe, never a second full walk on a big monorepo.

    Counts only files EXTRACTION will actually read: the old bare rglob("*") counted
    everything — a populated .venv alone exceeds the threshold, forcing a 50-file project
    onto the ~40% slower, non-crash-atomic streaming path (review 2026-07-03, F8). Prunes
    the shared SKIP_DIRS exactly like `_wanted` in both extractors."""
    if getattr(store, "path", ":memory:") == ":memory:":
        return False
    import os

    from .extract.python import SKIP_DIRS
    suffixes = {".py"}  # the Python extractor's fixed extension
    try:
        from .extract import treesitter
        suffixes |= set(treesitter.EXT_LANG)
    except Exception:  # noqa: BLE001 — tree-sitter absent: Python-only count still works
        pass
    n = 0
    try:
        for _root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if os.path.splitext(f)[1] in suffixes:
                    n += 1
                    if n >= _STREAM_AUTO_FILES:
                        return True
    except OSError:
        return False
    return False


class _StoreEdgeSink:
    """Append-only edge consumer that streams DEDUPED edges to the store (Phase 2b).

    Stands in for the extractor's in-memory `edges` list so the bulk edge set never
    materialises in Python. On a Magento-scale PHP repo that list is the dominant hog —
    ~15.5M raw edges (~4 GB in Python; ~9 GB if written raw to sqlite). The blow-up is
    name-based ambiguous fan-out: a bare call resolves to every same-named candidate, and the
    same call site repeats. `_dedup_edges` collapses that ~4:1 (15.5M → ~3.9M), and CRUCIALLY
    every dedup key is scoped to the edge's `src` (`(src, relation, dst_id)`; the
    CALLS-subsumes-REFERENCES and self-loop rules are per-`src` too). So dedup can be applied
    one source at a time.

    The extractor emits a definition's edges CONSECUTIVELY (the pass-2 loop finishes one
    def_id before the next), so the sink buffers the current source's edges and, when the
    source changes, runs the exact in-memory `_dedup_edges` over just that group and writes
    the survivors. Only ~3.9M deduped rows ever reach disk, not 15.5M — bounded memory AND a
    ~2 GB DB instead of ~9 GB. A final `_dedup_resolved_edges` in the store still runs as the
    authoritative GLOBAL pass: it catches the rare same-`src` edges split across non-adjacent
    groups (INHERITS/constructor seeds vs the def loop) and any resolver edge that shares a
    src with an extractor edge. The pre-pass only ever drops rows that the global pass would
    also drop, keeping the first-seen survivor (== lowest future rowid), so the final graph is
    byte-identical to the in-memory path (gated by the streaming differential oracle).

    Writes are batched with `executemany` and COMMITTED per batch: a single open transaction
    over millions of rows pins every dirty page and balloons RSS / exhausts temp space. On the
    rare batch holding an unstorable id (non-UTF-8 / embedded NUL), the batch is rolled back
    and re-applied per-row so only the bad edge is skipped, exactly as `add_edge` does.
    """

    __slots__ = ("_store", "_conn", "_group", "_cur_src", "_wbuf", "_gbuf",
                 "_memo", "count")
    _BATCH = 20000

    def __init__(self, store: Store) -> None:
        self._store = store
        self._conn = store.conn
        self._group: list[Any] = []      # edges of the current source, awaiting per-src dedup
        self._cur_src: Any = None
        self._wbuf: list[Any] = []       # deduped edges awaiting a batched write
        self._gbuf: list[Any] = []       # (template, dst_ids) compressed groups awaiting write
        self._memo: dict[str, int] = {}  # candidate-set sig -> set_id across the whole stream
        self.count = 0

    @staticmethod
    def _row(e: Any) -> tuple:
        from .store import _file_of
        return (e.src, e.relation.value, e.dst_symbol, e.dst_id, e.weight,
                e.provenance.value, e.location, e.source, _file_of(e.src), int(e.name_based))

    def append(self, edge: Any) -> None:
        if edge.src != self._cur_src and self._group:
            self._finalize_group()
        self._cur_src = edge.src
        self._group.append(edge)

    def _finalize_group(self) -> None:
        # Per-source dedup, identical to the in-memory full path applied to this source's
        # edges. Survivors are queued for a batched write — eligible widened fan-outs as
        # compressed groups (research/20; a source's rows are complete in this group, the
        # rare cross-group/resolver same-src additions are reconciled by the endgame's
        # collision expansion), everything else flat.
        survivors = _dedup_edges(self._group)
        if self._store.edge_compression:
            flat, groups = self._store.partition_compressible(survivors)
            self._wbuf.extend(flat)
            self._gbuf.extend(groups)
        else:
            self._wbuf.extend(survivors)
        self._group.clear()
        if len(self._wbuf) + len(self._gbuf) >= self._BATCH:
            self._write()

    def _write(self) -> None:
        if not self._wbuf and not self._gbuf:
            return
        try:
            with self._conn:  # atomic batch: COMMIT on success, ROLLBACK on a bind error
                self._conn.executemany(_EDGE_INSERT_SQL, [self._row(e) for e in self._wbuf])
        except (UnicodeEncodeError, ValueError):
            with self._conn:
                for e in self._wbuf:
                    self._store.add_edge(e)
        self.count += len(self._wbuf)
        self._wbuf.clear()
        if self._gbuf:
            # Group rows write inside one transaction; insert_edge_group guards
            # unstorable ids per group (falling back to flat arms) so one bad id
            # never rolls back the batch.
            with self._conn:
                for template, dsts in self._gbuf:
                    self._store.insert_edge_group(template, dsts, self._memo)
                    self.count += len(dsts)
            self._gbuf.clear()

    def flush(self) -> None:
        if self._group:
            self._finalize_group()
        self._write()


def _reindex_streaming(store: Store, path: str, abs_root: str,
                       ignore: list[str], resolvers: list,
                       lsp_forced: bool = True) -> Result:
    """Constant(-ish)-memory reindex: stream nodes/edges to SQLite; dedup as we go + once more
    globally in the store.

    Byte-identical to the in-memory full path (the streaming differential oracle is the gate).
    The facts that make this safe without re-deriving graph logic: (1) within extraction
    `edges` is write-only — override propagation is Python-only (it runs inside
    python.extract_project over the small Python edge set) and the bulk dedup is deferred;
    (2) resolvers read only nodes + source, never the edge list; (3) every dedup key is scoped
    to the edge's `src`, so the sink can collapse each source's fan-out with the exact
    in-memory `_dedup_edges` on the fly, and the store's `_dedup_resolved_edges` (the proven
    twin) is the authoritative GLOBAL pass for the rare cross-group / resolver overlap. We keep
    the (far smaller) node list resident for the seeds/resolvers; only deduped edges hit disk.
    """
    from .extract import extract_project
    from .resolve import run_resolvers

    # NOT one transaction: the sink commits each edge batch so the deduped edge stream never
    # sits in a single open transaction (that pins every dirty page and balloons RSS — the very
    # thing streaming exists to avoid). The trade-off is that a crash mid-rebuild leaves a
    # partial index; a re-run rebuilds cleanly (it clears first). The default in-memory path
    # stays crash-atomic; AUTO only picks streaming for large on-disk repos, where the
    # in-memory alternative is an OOM. The clear, node write, and dedup are each a transaction.
    with store.conn:
        store.conn.execute("DELETE FROM nodes")
        store.wipe_edges()
    sink = _StoreEdgeSink(store)
    try:
        # Pass 1/2: nodes resident, edges streamed (deduped per-source, committed in batches).
        xreport: dict = {}
        nodes, _ = extract_project(path, ignore=ignore, cache_asts=False, edge_sink=sink,
                                   report=xreport)
        # Resolvers enrich from the node list + source only; their (few) extra edges stream to
        # the store after the extractor's, preserving the full path's append order.
        nodes, res_edges = run_resolvers(path, nodes, [], resolvers)
        for e in res_edges:
            sink.append(e)
        # The LSP resolver is edge-DRIVEN (its sites are the extractor's call
        # edges), and this path deliberately hands resolvers an empty edge list
        # — so feed it from the store, where the streamed edges already live
        # (research/24). Its extra edges ride the same sink as every resolver's.
        for r in resolvers:
            if getattr(r, "name", "") != "lsp":
                continue
            sink.flush()  # the sites must include the buffered tail
            from .model import Relation as _Rel
            rows = [(src, sym, loc, prov == Provenance.AMBIGUOUS.value)
                    for src, sym, loc, prov in store.conn.execute(
                        "SELECT DISTINCT src, dst_symbol, location, provenance "
                        "FROM edges_all WHERE relation = ? "
                        "AND source = 'tree-sitter' AND dst_symbol IS NOT NULL",
                        (_Rel.CALLS.value,))]
            try:
                for e in r.resolve_rows(path, nodes, rows):
                    sink.append(e)
            except Exception:  # noqa: BLE001 — same never-abort rule as run_resolvers
                continue
    finally:
        sink.flush()  # never drop the buffered tail — even if extraction/resolvers raise
    with store.conn:
        for n in nodes:
            store.add_node(n)
        _persist_symtab(store, xreport)
        # Orphan sweep (review 2026-07-03, F9): a swallowed tree-sitter failure mid-extract
        # (see extract_project's warn-and-continue) leaves already-COMMITTED edge batches whose
        # defining nodes were never returned — resolved edges into/out of phantom ids that
        # would flood find_holes/scan with findings indistinguishable from real broken
        # references. Drop any edge whose src or resolved dst has no node. On a clean run this
        # deletes nothing (every extractor edge resolves against the full symbol table), so the
        # streamed index stays byte-identical to the in-memory path.
        # Compressed groups first (research/20): a phantom-src group row just
        # drops; a group whose SET contains a phantom member expands so the flat
        # sweep below prunes exactly the phantom arms (clean runs expand nothing).
        store.conn.execute(
            "DELETE FROM edge_groups WHERE src NOT IN (SELECT id FROM nodes)")
        orphan_keys = store._expand_groups(
            """set_id IN (SELECT set_id FROM cand_members
                           WHERE dst_id NOT IN (SELECT id FROM nodes))""")
        store.conn.execute(
            """DELETE FROM edges WHERE
                   src NOT IN (SELECT id FROM nodes)
                OR (dst_id IS NOT NULL AND dst_id NOT IN (SELECT id FROM nodes))""")
    # The store now holds the RAW (pre-dedup) edge set — millions of rows on a large repo.
    # Both endgame passes below correlate rows by (src, relation, dst_id): the override
    # widening's NOT-EXISTS probes and the dedup's EXISTS subqueries. A covering index makes
    # those index lookups instead of full scans. Temporary: dropped after, since the
    # steady-state read indexes differ.
    with store.conn:
        store.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_dedup ON edges(src, relation, dst_id, weight)")
    # Override widening: the in-memory path runs the extractor's _propagate_overrides over
    # the full edge list; the sink path never materialises that list (the HA constant-memory
    # fix), so re-derive the same AMBIGUOUS subclass-override edges from the store — the DB
    # twin already pinned equal to the extractor's by the incremental differential oracle,
    # and itself constant-memory (symbol-scale Python + SQL-side scan/insert; its first cut
    # fetchall'd the edge table and re-OOM'd HA in the endgame, 2026-07-03). Runs after nodes
    # land (it reads class kinds + INHERITS rows) and before the global dedup, exactly where
    # the in-memory path's override edges sit relative to dedup.
    with store.conn:
        store._propagate_overrides()
    with store.conn:
        # Sink-compressed groups vs same-src flat rows that arrived in a LATER
        # stream group (resolver edges, non-adjacent extractor seeds): flatten
        # the colliding groups (unbounded probe — one-off per rebuild) so the
        # global dedup below applies today's exact semantics to every duplicate.
        collision_keys = store._expand_collisions(None)
        store._dedup_resolved_edges()
        store._compress_edges(
            srcs={k[0] for k in orphan_keys} | {k[0] for k in collision_keys})
        if orphan_keys or collision_keys:
            store._gc_cand_sets()
    # OWN transaction, tolerated on failure: on the 2026-07-05 HA run a disk-full
    # during this DROP rolled back the whole enclosing transaction — dedup included —
    # and aborted before ANALYZE / generation / root meta, leaving a silently
    # duplicate-edged index (research/18 bug 3). A leftover temp index only costs
    # disk; a lost dedup + missing endgame costs correctness of degree metrics.
    try:
        with store.conn:
            store.conn.execute("DROP INDEX IF EXISTS idx_edges_dedup")
    except sqlite3.OperationalError as exc:
        warnings.warn(f"could not drop the temporary dedup index ({exc}); the index is "
                      f"correct but larger on disk until the next reindex",
                      RuntimeWarning, stacklevel=2)

    store.analyze()
    store.bump_generation()
    store.set_meta("root", abs_root)
    files = {n.id.split("::", 1)[0] for n in nodes if "::" in n.id}
    # COUNT, not len(unresolved_edges()) — the latter builds an Edge object per hole,
    # an O(holes) allocation this path exists to avoid.
    holes = store.unresolved_count()
    res = ok({"files": len(files), "nodes": store.node_count(), "holes": holes},
             files=len(files), nodes=store.node_count())
    _annotate_extraction_gaps(res, xreport)
    _annotate_lsp(res, resolvers, forced=lsp_forced)
    return res


# --------------------------------------------------------------------------
def _dedup_edges(edges: list) -> list:
    """Collapse parallel resolved edges (same src, relation, dst_id) to one, keeping
    the highest weight. Two call sites to the same target — or the jedi resolver
    re-confirming an AST edge under `--precise` — otherwise double-count direct-degree
    metrics (fan_in/fan_out, the `fan_in`-fallback hubs) and `get_matrix` cells. The
    boolean reachability/GraphBLAS layer already dedups, so this aligns the adjacency
    store with it. Unresolved holes (dst_id is None) are distinct reference sites and
    are kept as-is.

    A CALLS edge also subsumes a REFERENCES edge to the *same* (src, dst): a called
    symbol is already a dependency, so the by-name REFERENCES is redundant and would
    double-count fan_in / pagerank (a function that both calls and names/annotates a
    class). The strong relation wins. A REFERENCES *self-loop* (a def naming itself)
    is likewise dropped — it carries no liveness/impact meaning and only inflates
    degree metrics (unlike a recursive CALLS self-loop, which is kept)."""
    best: dict[tuple, Any] = {}
    order: list[tuple] = []
    holes: list = []
    nb_any: dict[tuple, bool] = {}  # any arm of this (src,rel,dst_id) group was name-based?
    for e in edges:
        if e.dst_id is None:
            holes.append(e)
            continue
        key = (e.src, e.relation, e.dst_id)
        if key not in best:
            best[key] = e
            order.append(key)
            nb_any[key] = e.name_based
        else:
            nb_any[key] = nb_any[key] or e.name_based
            if e.weight > best[key].weight:
                best[key] = e
    # Mirror the store's `_dedup_resolved_edges` step 0: a (src,relation,dst_id) group that
    # contains ANY name-based arm stays re-widenable, so its survivor carries name_based=True
    # even when the kept (highest-weight) row was a PRECISE resolution. Without this the
    # in-memory full path and the store/streaming path (which ORs in SQL) diverge on
    # `name_based` whenever a precise edge — e.g. jedi under `--precise`, which arrives as a
    # separate resolver edge from the extractor's name-based arm — coincides with a name-based
    # edge to the same target (panel R50, opus: a real streaming-vs-full byte-identity break).
    # OR-only, so a pure-precise group keeps False and a precise resolution is never wrongly
    # made re-widenable (R22A preserved); aligning with the store also closes the same latent
    # full-vs-incremental gap (R23A).
    for key, survivor in best.items():
        if nb_any[key] and not survivor.name_based:
            survivor.name_based = True
    called = {(e.src, e.dst_id) for e in best.values() if e.relation is Relation.CALLS}

    def _drop(e) -> bool:  # a redundant or self-looping REFERENCES edge
        return e.relation is Relation.REFERENCES and (
            e.src == e.dst_id or (e.src, e.dst_id) in called)

    return holes + [best[k] for k in order if not _drop(best[k])]


def _resolve_target(store: Store, name: str):
    """Resolve `name` to a single node, accepting (a) a bare name, (b) a qualified
    `Type.method` / dotted suffix, or (c) a full `path::qual` id — so a homonym can be
    scoped from the CLI/MCP instead of just refused (issue #9). Returns
    `(node | None, candidates)`; on an ambiguous bare name `node` is None and
    `candidates` lists every match."""
    if not isinstance(name, str):
        # A non-str symbol (None / wrong type from a library or malformed MCP call) can't
        # name a node — refuse with no match rather than raise, honouring the "every op
        # returns a Result, never raises" contract (panel R17A).
        return None, []
    if "::" in name:  # a full node id pins exactly one
        n = store.get_node(name)
        return n, ([n] if n else [])
    nodes = store.nodes_by_name(name)  # try the name exactly as given first
    if not nodes and "." in name:      # fall back to a qualified `Type.method` suffix
        leaf = name.rsplit(".", 1)[-1]
        nodes = [n for n in store.nodes_by_name(leaf)
                 if n.id.split("::", 1)[-1] == name or n.id.endswith("." + name)]
    return (nodes[0], nodes) if len(nodes) == 1 else (None, nodes)


def _resolve_one(store: Store, name: str):
    return _resolve_target(store, name)[0]


def _resolve_or_explain(store: Store, name: str):
    """Resolve `name` to a single node, or return a *precise* reason it could not — distinguishing
    an unknown name ("no symbol named X") from a genuinely ambiguous one, and in the ambiguous case
    listing the candidate ids so the caller can re-issue with a qualified id (panel R266 / dogfood
    round-2 usability finding: `get_callers` used to say "not a unique symbol" for both cases and
    never surfaced the candidates). Returns `(node, None)` or `(None, reason)`."""
    target, candidates = _resolve_target(store, name)
    if target is not None:
        return target, None
    if not candidates:
        return None, f"no symbol named '{name}' in the index"
    ids = sorted(n.id for n in candidates)
    shown = ", ".join(ids[:8])
    more = "" if len(ids) <= 8 else f" (+{len(ids) - 8} more)"
    return None, (f"'{name}' is ambiguous across {len(candidates)} definitions: {shown}{more}; "
                  "pass a qualified id (Type.method or path::qualified.name) to disambiguate")


def _default_detector(store: Store) -> PythonLibraryDetector:
    """Build the detector from `stitchgraph.toml` — the entry-point override is
    the trust escape hatch (design §4). Config is loaded from the *indexed* root
    (stored at reindex), not the process cwd, so entry-point overrides / test
    inclusion follow the project even when the operation runs from elsewhere."""
    from .config import load_config
    cfg = load_config(store.get_meta("root"))
    return PythonLibraryDetector(overrides=cfg.include, include_tests=cfg.include_tests,
                                 root_modules=cfg.root_modules)


_CODE_KINDS = {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS}


def _stale_candidates(store: Store, unreached: set[str],
                      reachable: set[str]) -> list[str]:
    """Filter the unreachable set down to real dead-code candidates.

    Dead *code* means an unreached function/method/class. Modules/packages
    (liveness is per-symbol), data/route nodes (DBTable, Route, ...), and dunder
    methods (`__init__`, `__enter__`, ... are framework-invoked) are not candidates.

    A CLASS with any *reachable* member is itself live — a live method implies a live
    class (the class must exist for the method to run). This general invariant is the
    backstop for the whole "class dead while a member is live" family across every
    language/idiom (callback/main/exported/interface/trait/partial), so a class is flagged
    only when it AND all its members are unreached (panel XXX — C# partial classes).
    """
    # qual-prefix of every reachable member -> its owning class/function id(s). Split only
    # the qual (after `::`); the rel path may itself contain dots (`f.py`).
    live_owners: set[str] = set()
    for rid in reachable:
        pre, sep, qual = rid.partition("::")
        if not sep or "." not in qual:
            continue
        parts = qual.split(".")
        for i in range(1, len(parts)):
            live_owners.add(f"{pre}::{'.'.join(parts[:i])}")
    out: list[str] = []
    for nid in unreached:
        node = store.get_node(nid)
        if node is None or node.kind not in _CODE_KINDS:
            continue
        if node.name.startswith("__") and node.name.endswith("__"):
            continue
        if node.kind is NodeKind.CLASS and nid in live_owners:
            continue  # a reachable member keeps its class live
        out.append(nid)
    return sorted(out)
