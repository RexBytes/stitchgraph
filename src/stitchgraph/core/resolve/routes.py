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
        return nodes, edges


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
