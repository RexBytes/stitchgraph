"""Express / server-side JS route resolver (design §2a).

Detects `app.get("/users", handler)` / `router.post("/x", mw, handler)` calls and
creates a Route node linked ROUTES_TO the (named) handler. Server-side
counterpart to the client-side fetch resolver.

Optional (needs tree-sitter); a no-op without it.
"""

from __future__ import annotations

from typing import Any, cast

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
    _HAVE_TS = True
except ModuleNotFoundError:  # pragma: no cover
    _HAVE_TS = False

_EXT = {".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
        ".ts": "typescript", ".tsx": "tsx"}
_VERBS = {"get", "post", "put", "delete", "patch", "all", "use"}


class ExpressRouteResolver:
    name = "express-routes"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        if not _HAVE_TS:
            return [], []
        nodes: list[Node] = []
        edges: list[Edge] = []
        parsers: dict[str, Parser] = {}
        for path in sorted(ctx.root.rglob("*")):
            if path.suffix not in _EXT or any(
                    p in {"node_modules", ".git", "dist", "build"}
                    for p in path.relative_to(ctx.root).parts):
                continue
            lang = _EXT[path.suffix]
            if lang not in parsers:
                try:
                    parsers[lang] = Parser(get_language(cast(Any, lang)))
                except Exception:  # noqa: BLE001
                    continue
            try:
                src = path.read_bytes()
            except OSError:
                continue
            rel = path.relative_to(ctx.root).as_posix()
            tree = parsers[lang].parse(src)
            _scan(tree.root_node, src, rel, ctx, nodes, edges)
        return nodes, edges


def _scan(root, src, rel, ctx, nodes, edges):
    def rec(n):
        if n.type == "call_expression":
            r = _express_call(n, src)
            if r is not None:
                method, path, handler = r
                rid = f"{rel}::route:{method} {path}"
                nodes.append(Node(id=rid, kind=NodeKind.ROUTE, name=f"{method} {path}",
                                  location=f"{rel}:{n.start_point[0] + 1}:0",
                                  roles=frozenset({"route"})))
                if handler:
                    cands = ctx.by_name.get(handler, [])
                    # Link to *all* same-named handlers (precision over recall):
                    # dropping the edge on an ambiguous name would risk flagging a
                    # live handler dead. Ambiguity is recorded on each edge.
                    loc = f"{rel}:{n.start_point[0] + 1}:0"
                    if len(cands) == 1:
                        edges.append(Edge(src=rid, relation=Relation.ROUTES_TO,
                                          dst_symbol=handler, dst_id=cands[0], weight=0.8,
                                          provenance=Provenance.INFERRED, location=loc,
                                          source="heuristic"))
                    elif len(cands) > 1:
                        w = round(0.8 / len(cands), 3)
                        for cid in cands:
                            edges.append(Edge(src=rid, relation=Relation.ROUTES_TO,
                                              dst_symbol=handler, dst_id=cid, weight=w,
                                              provenance=Provenance.AMBIGUOUS, location=loc,
                                              source="heuristic"))
        for c in n.children:
            rec(c)
    rec(root)


def _express_call(call, src):
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    prop = fn.child_by_field_name("property")
    verb = src[prop.start_byte:prop.end_byte].decode() if prop else ""
    if verb not in _VERBS:
        return None
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    string_args = [a for a in args.children if a.type in ("string", "template_string")]
    if not string_args:
        return None
    path = src[string_args[0].start_byte:string_args[0].end_byte].decode().strip("`'\"")
    if not path.startswith("/"):
        return None
    # last identifier argument is the handler reference
    handler = None
    for a in reversed(args.children):
        if a.type == "identifier":
            handler = src[a.start_byte:a.end_byte].decode()
            break
    return verb.upper() if verb not in ("all", "use") else "ANY", path, handler
