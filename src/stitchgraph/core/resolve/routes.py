"""Web-route resolver (design §2a). Decorator -> Route -> handler.

Detects the common Python web decorators (Flask / FastAPI / APIRouter /
blueprints): `@app.route("/x")`, `@app.get("/x")`, `@router.post("/x")`,
`@bp.route("/x")`. Each becomes a Route node linked ROUTES_TO the decorated
handler. Routes are HTTP entry points, so the detector seeds reachability from
them (see entrypoints — ROUTE/ENDPOINT kinds are roots).

This is heuristic, so edges are INFERRED with confidence < 1 (design §3).
"""

from __future__ import annotations

import ast

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext, iter_function_defs

_VERBS = {"get", "post", "put", "delete", "patch", "head", "options", "route"}


class WebRouteResolver:
    name = "web-routes"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        for rel, tree in ctx.parse():
            for func, handler_id, _ in iter_function_defs(tree, rel):
                if handler_id not in ctx.ids:
                    continue
                for dec in getattr(func, "decorator_list", []):
                    route = _route_of(dec)
                    if route is None:
                        continue
                    method, path = route
                    rid = f"{rel}::route:{method} {path}"
                    nodes.append(Node(
                        id=rid, kind=NodeKind.ROUTE, name=f"{method} {path}",
                        location=f"{rel}:{getattr(dec, 'lineno', func.lineno)}:0",
                        roles=frozenset({"route"}),
                    ))
                    edges.append(Edge(
                        src=rid, relation=Relation.ROUTES_TO, dst_symbol=func.name,
                        dst_id=handler_id, weight=0.9, provenance=Provenance.INFERRED,
                        location=f"{rel}:{func.lineno}:0", source="heuristic",
                    ))
            _django_routes(tree, rel, ctx, nodes, edges)
        return nodes, edges


_DJANGO = {"path", "re_path", "url"}


def _django_routes(tree, rel, ctx, nodes, edges):
    """Django URLconf: `path('users/', views.user_list)` -> Route -> handler."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _DJANGO and len(node.args) >= 2):
            continue
        path_arg = node.args[0]
        if not (isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str)):
            continue
        path = "/" + path_arg.value.lstrip("/")
        handler = _name_of(node.args[1])
        if not handler:
            continue
        cands = ctx.by_name.get(handler, [])
        rid = f"{rel}::route:ANY {path}"
        nodes.append(Node(id=rid, kind=NodeKind.ROUTE, name=f"ANY {path}",
                          location=f"{rel}:{node.lineno}:0", roles=frozenset({"route"})))
        # Link to *all* same-named handlers: under-counting reachability would flag
        # a live handler dead (destructive); over-counting only under-reports dead
        # code (safe). Ambiguity is recorded on the edge (AMBIGUOUS, split weight).
        _route_edges(edges, rid, handler, cands, node.lineno, rel, base_weight=0.85)


def _route_edges(edges, rid, handler, cands, line, rel, base_weight):
    """One ROUTES_TO edge per candidate handler. A single candidate is INFERRED at
    full weight; several are AMBIGUOUS with the weight split across them."""
    loc = f"{rel}:{line}:0"
    if not cands:
        return
    if len(cands) == 1:
        edges.append(Edge(src=rid, relation=Relation.ROUTES_TO, dst_symbol=handler,
                          dst_id=cands[0], weight=base_weight,
                          provenance=Provenance.INFERRED, location=loc, source="heuristic"))
        return
    w = round(base_weight / len(cands), 3)
    for cid in cands:
        edges.append(Edge(src=rid, relation=Relation.ROUTES_TO, dst_symbol=handler,
                          dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                          location=loc, source="heuristic"))


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _route_of(dec: ast.AST) -> tuple[str, str] | None:
    """Return (METHOD, path) if this decorator is a route, else None."""
    if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
        return None
    verb = dec.func.attr
    if verb not in _VERBS:
        return None
    path = None
    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        path = dec.args[0].value
    if path is None:
        return None
    method = "ANY" if verb == "route" else verb.upper()
    return method, path
