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
# Receivers that are HTTP *clients*, not Express apps/routers: `axios.post("/x")`,
# `http.get("/x")` are client calls (the js-fetch resolver models them as SUBMITS_TO), not
# server route registrations — skip them so they don't become phantom ROUTE nodes (panel
# R16B). Deny-list only (not an app/router allow-list): mislabelling a real route would drop
# its handler's only root and risk a false-dead, so keep every non-client receiver.
_CLIENT_RECEIVERS = {"axios", "http", "https", "fetch", "got", "ky", "superagent",
                     "request", "xhr", "needle", "phin"}


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
            if not path.is_file():
                continue  # skip FIFOs/special files: read_bytes() opens a FIFO
                          # and blocks forever; the OSError guard never fires (panel GGG)
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
    obj = fn.child_by_field_name("object")
    recv = ""
    if obj is not None and obj.type == "identifier":
        recv = src[obj.start_byte:obj.end_byte].decode()
    elif obj is not None and obj.type == "member_expression":
        p = obj.child_by_field_name("property")  # `this.http.get` -> trailing `http`
        recv = src[p.start_byte:p.end_byte].decode() if p else ""
    if recv.lower() in _CLIENT_RECEIVERS:
        return None  # client HTTP call, not a server route
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    string_args = [a for a in args.children if a.type in ("string", "template_string")]
    if not string_args:
        return None
    path = src[string_args[0].start_byte:string_args[0].end_byte].decode().strip("`'\"")
    if not path.startswith("/"):
        return None
    # The handler is the last reference argument. It may be a bare identifier
    # (`handleRequest`) OR a method reference (`ctrl.handleRequest` / `this.handleRequest`)
    # — a member_expression. Resolve the latter by its property (method) name: omitting it
    # left a live route handler method with no ROUTES_TO edge, flagged dead (panel R35B,
    # cardinal — the bare-function case worked, the method case didn't: a symmetry gap).
    handler = None
    for a in reversed(args.children):
        if a.type == "identifier":
            handler = src[a.start_byte:a.end_byte].decode()
            break
        if a.type == "member_expression":
            prop = a.child_by_field_name("property")
            if prop is not None:
                handler = src[prop.start_byte:prop.end_byte].decode()
                break
    return verb.upper() if verb not in ("all", "use") else "ANY", path, handler
