"""Spring (Java) route resolver (design §2a).

Detects `@GetMapping("/x")` / `@RequestMapping("/x")` / `@PostMapping(...)` on
methods and creates a Route node linked ROUTES_TO the annotated handler method.

Optional (needs tree-sitter); a no-op without it.
"""

from __future__ import annotations

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext

try:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language
    _HAVE_TS = True
except ModuleNotFoundError:  # pragma: no cover
    _HAVE_TS = False

_MAPPINGS = {
    "RequestMapping": "ANY", "GetMapping": "GET", "PostMapping": "POST",
    "PutMapping": "PUT", "DeleteMapping": "DELETE", "PatchMapping": "PATCH",
}


class SpringRouteResolver:
    name = "spring-routes"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        if not _HAVE_TS:
            return [], []
        try:
            parser = Parser(get_language("java"))
        except Exception:  # noqa: BLE001
            return [], []
        nodes: list[Node] = []
        edges: list[Edge] = []
        for path in sorted(ctx.root.rglob("*.java")):
            if any(p in {".git", "build", "target"}
                   for p in path.relative_to(ctx.root).parts):
                continue
            try:
                src = path.read_bytes()
            except OSError:
                continue
            rel = path.relative_to(ctx.root).as_posix()
            tree = parser.parse(src)
            _scan(tree.root_node, src, rel, parent="", ctx=ctx, nodes=nodes, edges=edges)
        return nodes, edges


def _scan(node, src, rel, parent, ctx, nodes, edges):
    for child in node.children:
        if child.type == "class_declaration":
            name = _field(child, "name", src)
            qual = f"{parent}.{name}" if parent and name else (name or parent)
            _scan(child, src, rel, qual, ctx, nodes, edges)
        elif child.type == "method_declaration":
            name = _field(child, "name", src)
            mapping = _mapping(child, src)
            if name and mapping:
                method, path = mapping
                mid = f"{rel}::{parent}.{name}" if parent else f"{rel}::{name}"
                rid = f"{rel}::route:{method} {path}"
                nodes.append(Node(id=rid, kind=NodeKind.ROUTE, name=f"{method} {path}",
                                  location=f"{rel}:{child.start_point[0] + 1}:0",
                                  roles=frozenset({"route"})))
                if mid in ctx.ids:
                    edges.append(Edge(src=rid, relation=Relation.ROUTES_TO,
                                      dst_symbol=name, dst_id=mid, weight=0.85,
                                      provenance=Provenance.INFERRED,
                                      location=f"{rel}:{child.start_point[0] + 1}:0",
                                      source="heuristic"))
            _scan(child, src, rel, parent, ctx, nodes, edges)
        else:
            _scan(child, src, rel, parent, ctx, nodes, edges)


def _mapping(method_node, src):
    """Return (METHOD, path) from a *Mapping annotation, else None."""
    for c in method_node.children:
        if c.type != "modifiers":
            continue
        for ann in c.children:
            if ann.type not in ("annotation", "marker_annotation"):
                continue
            nm = ann.child_by_field_name("name")
            verb = _MAPPINGS.get(src[nm.start_byte:nm.end_byte].decode()) if nm else None
            if verb is None:
                continue
            path = _annotation_path(ann, src) or "/"
            return verb, path
    return None


def _annotation_path(ann, src):
    args = ann.child_by_field_name("arguments")
    if args is None:
        return None
    for a in args.children:
        if a.type == "string_literal":
            return src[a.start_byte:a.end_byte].decode().strip('"')
        if a.type == "element_value_pair":  # value = "/x"
            val = a.child_by_field_name("value")
            if val and val.type == "string_literal":
                return src[val.start_byte:val.end_byte].decode().strip('"')
    return None


def _field(node, field, src):
    c = node.child_by_field_name(field)
    return src[c.start_byte:c.end_byte].decode() if c else None
