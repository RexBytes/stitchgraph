"""JS/TS fetch → route resolver (design §2a). Frontend → backend link.

Scans JavaScript/TypeScript for `fetch("/api/x")` / `axios.get("/x")` calls and
links the enclosing function to the matching backend Route node (created by the
web-route resolver, which runs first). This closes the front of the stack from
the *client* side: a JS handler → route → backend handler → … → DB table, all in
one `trace_path`.

Heuristic (URL-path match) -> INFERRED edges. Optional (needs tree-sitter); a
no-op if it isn't installed.
"""

from __future__ import annotations

from ..envelope import Provenance
from ..model import Edge, NodeKind, Relation
from . import ResolveContext

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
    _HAVE_TS = True
except ModuleNotFoundError:  # pragma: no cover
    _HAVE_TS = False

_EXT_LANG = {".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
             ".ts": "typescript", ".tsx": "tsx"}
_HTTP_CALLS = {"fetch", "get", "post", "put", "delete", "patch", "request"}


class JsFetchResolver:
    name = "js-fetch"

    def resolve(self, ctx: ResolveContext) -> tuple[list, list[Edge]]:
        if not _HAVE_TS:
            return [], []
        routes: dict[str, list[str]] = {}
        for n in ctx.nodes:
            if n.kind == NodeKind.ROUTE:
                parts = n.name.split(" ", 1)  # "METHOD /path" (guard like html.py)
                if len(parts) == 2:
                    routes.setdefault(parts[1], []).append(n.id)
        if not routes:
            return [], []
        edges: list[Edge] = []
        parsers: dict[str, Parser] = {}
        for path in sorted(ctx.root.rglob("*")):
            if path.suffix not in _EXT_LANG:
                continue
            if any(p in {"node_modules", ".git", "dist", "build"}
                   for p in path.relative_to(ctx.root).parts):
                continue
            lang = _EXT_LANG[path.suffix]
            if lang not in parsers:
                try:
                    parsers[lang] = Parser(get_language(lang))
                except Exception:  # noqa: BLE001
                    continue
            try:
                src = path.read_bytes()
            except OSError:
                continue
            rel = path.relative_to(ctx.root).as_posix()
            tree = parsers[lang].parse(src)
            _walk(tree.root_node, src, rel, parent="", routes=routes,
                  ids=ctx.ids, edges=edges)
        return [], edges


def _walk(node, src, rel, parent, routes, ids, edges):
    for child in node.children:
        if child.type in ("function_declaration", "method_definition",
                          "generator_function_declaration"):
            name = _name(child, src)
            qual = f"{parent}.{name}" if parent and name else (name or parent)
            fid = f"{rel}::{qual}"
            owner = fid if fid in ids else None
            _scan_calls(child, src, rel, owner, routes, ids, edges, parent)
            _walk(child, src, rel, qual, routes, ids, edges)
        elif child.type == "class_declaration":
            name = _name(child, src)
            qual = f"{parent}.{name}" if parent and name else (name or parent)
            _walk(child, src, rel, qual, routes, ids, edges)
        else:
            _walk(child, src, rel, parent, routes, ids, edges)


def _scan_calls(func, src, rel, owner, routes, ids, edges, parent):
    """Find fetch/axios calls in this function body and link to routes."""
    def rec(n):
        for c in n.children:
            if c.type in ("function_declaration", "method_definition"):
                continue
            if c.type == "call_expression":
                path = _http_path(c, src)
                if path is not None and owner:
                    rids = routes.get(path) or routes.get(path.rstrip("/")) or []
                    # Link to *all* routes sharing the path (e.g. GET and POST /x),
                    # never just one, so trace_path can't miss the real target.
                    loc = f"{rel}:{c.start_point[0] + 1}:0"
                    if len(rids) == 1:
                        edges.append(Edge(
                            src=owner, relation=Relation.SUBMITS_TO, dst_symbol=path,
                            dst_id=rids[0], weight=0.75, provenance=Provenance.INFERRED,
                            location=loc, source="heuristic"))
                    elif len(rids) > 1:
                        w = round(0.75 / len(rids), 3)
                        for rid in rids:
                            edges.append(Edge(
                                src=owner, relation=Relation.SUBMITS_TO, dst_symbol=path,
                                dst_id=rid, weight=w, provenance=Provenance.AMBIGUOUS,
                                location=loc, source="heuristic"))
            rec(c)
    rec(func)


def _http_path(call, src):
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    callee = fn.text.decode("utf-8", "replace") if fn.type == "identifier" else None
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        callee = prop.text.decode("utf-8", "replace") if prop else None
    if callee not in _HTTP_CALLS:
        return None
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    for a in args.children:
        if a.type in ("string", "template_string"):
            text = src[a.start_byte:a.end_byte].decode("utf-8", "replace").strip("`'\"")
            if text.startswith("/"):
                return text.split("?", 1)[0]
    return None


def _name(node, src):
    nm = node.child_by_field_name("name")
    return src[nm.start_byte:nm.end_byte].decode("utf-8", "replace") if nm else None
