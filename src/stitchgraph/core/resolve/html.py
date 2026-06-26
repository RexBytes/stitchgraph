"""HTML-template resolver (design §2a). Form -> route (`SUBMITS_TO`).

Scans templates (`.html` / `.jinja` / `.j2`) for `<form action="/x">` and links
each to the matching Route node (created by the web-route resolver, which runs
first). This closes the front of the full stack: a form submits to a route, which
routes to a handler, which queries a table — all traceable in one `trace_path`.

Matching is by URL path, so it's heuristic -> INFERRED edges (design §3). Uses the
stdlib html.parser (zero-dependency).
"""

from __future__ import annotations

from html.parser import HTMLParser

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext

_TEMPLATE_GLOBS = ("*.html", "*.htm", "*.jinja", "*.j2")
_SKIP = {".venv", "venv", "build", "dist", "__pycache__", ".git", "node_modules"}


class HtmlRouteResolver:
    name = "html-routes"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        route_by_path = _routes_by_path(ctx.nodes)
        if not route_by_path:
            return [], []  # no routes to link to
        nodes: list[Node] = []
        edges: list[Edge] = []
        for path in _template_files(ctx):
            rel = path.relative_to(ctx.root).as_posix()
            try:
                # errors="replace": a non-UTF-8 template (Latin-1, etc.) must still be
                # scanned for forms, not silently skipped — an uncaught UnicodeDecodeError
                # here is swallowed by run_resolvers' broad except, disabling the HTML
                # resolver for the whole project (panel R11B). Decode lossily instead.
                forms = _find_forms(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not forms:
                continue
            tid = f"{rel}::template"
            nodes.append(Node(id=tid, kind=NodeKind.TEMPLATE, name=rel,
                              location=f"{rel}:1:0"))
            for action, _method in forms:
                rids = (route_by_path.get(action)
                        or route_by_path.get(action.rstrip("/")) or [])
                _submits_edges(edges, tid, action, rids, rel)
        return nodes, edges


def _submits_edges(edges: list[Edge], src: str, action: str, rids: list[str],
                   rel: str, base_weight: float = 0.8) -> None:
    """One SUBMITS_TO edge per route sharing the path. Several routes can share a
    path (e.g. GET and POST /x); link to *all* so `trace_path` can't miss the one
    the form actually targets. A single match is INFERRED; many are AMBIGUOUS."""
    if not rids:
        return
    if len(rids) == 1:
        edges.append(Edge(src=src, relation=Relation.SUBMITS_TO, dst_symbol=action,
                          dst_id=rids[0], weight=base_weight,
                          provenance=Provenance.INFERRED, location=f"{rel}:1:0",
                          source="heuristic"))
        return
    w = round(base_weight / len(rids), 3)
    for rid in rids:
        edges.append(Edge(src=src, relation=Relation.SUBMITS_TO, dst_symbol=action,
                          dst_id=rid, weight=w, provenance=Provenance.AMBIGUOUS,
                          location=f"{rel}:1:0", source="heuristic"))


def _routes_by_path(nodes: list[Node]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n in nodes:
        if n.kind == NodeKind.ROUTE:
            parts = n.name.split(" ", 1)  # "METHOD /path"
            if len(parts) == 2:
                out.setdefault(parts[1], []).append(n.id)
    return out


def _template_files(ctx: ResolveContext):
    for glob in _TEMPLATE_GLOBS:
        for path in ctx.root.rglob(glob):
            # skip FIFOs/special files: read_text() opens a FIFO and blocks
            # forever; the OSError guard at the read site never fires (panel GGG)
            if path.is_file() and not any(
                    p in _SKIP for p in path.relative_to(ctx.root).parts):
                yield path


class _FormFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "form":
            d = {k.lower(): (v or "") for k, v in attrs}
            if d.get("action"):
                self.forms.append((d["action"], d.get("method", "get").upper()))


def _find_forms(html: str) -> list[tuple[str, str]]:
    parser = _FormFinder()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — malformed HTML, take what we got
        pass
    return parser.forms
