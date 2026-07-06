"""Django/Jinja template-variable resolver (v3.39.0, research/19).

`{{ inline_admin_formset.is_collapsible }}` reads a Python property by name
from a template — a real use the call graph cannot see, so the property (and
the private helpers it alone calls) was false-flagged dead on Django itself
(admin's `is_collapsible` / `inline_formset_data`, used only from
`edit_inline/*.html`). This resolver scans templates for `{{ ... }}` /
`{% ... %}` expressions, takes every DOTTED attribute path's member segments
(the leading segment is a context variable the template runtime binds — no
graph meaning), and references matching project functions/methods by name:
INFERRED for a single candidate, AMBIGUOUS fan for homonyms — the same
two-tier rules as every other name-based binding.

The template file becomes a TEMPLATE node, which the entry-point detector
roots (frameworks render templates BY NAME, so a template is an external
entry surface exactly like a route). Cardinal-safe: only ever adds
reachability. A stoplist drops the ubiquitous loop/context names (`items`,
`count`, …) that would fan everywhere while proving nothing.
"""

from __future__ import annotations

import re

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext
from .html import _template_files

# {{ expr }} and {% tag expr %} bodies.
_TAG_RE = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.S)
# A dotted attribute path: context_var.member(.member…). Filters (`|safe`) and
# call parens terminate the match naturally (not in [\w.]).
_PATH_RE = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
# Template-language keywords whose "paths" aren't attribute reads, and
# ubiquitous member names that would fan across the whole graph while proving
# nothing about any one of them.
_STOP_SEGMENTS = frozenset({
    "items", "keys", "values", "count", "id", "pk", "name", "value", "url",
    "title", "label", "type", "data", "all", "first", "last", "get",
})
_STOP_HEADS = frozenset({"forloop", "block", "csrf_token", "request", "settings"})


class DjangoTemplateResolver:
    name = "django-template"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        # Candidate members: functions/methods only (a template read hits a
        # property/method); modules and classes resolve elsewhere.
        member_ids: dict[str, list[str]] = {}
        for n in ctx.nodes:
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                member_ids.setdefault(n.name, []).append(n.id)
        if not member_ids:
            return [], []
        nodes: list[Node] = []
        edges: list[Edge] = []
        for path in _template_files(ctx):
            rel = path.relative_to(ctx.root).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "{{" not in text and "{%" not in text:
                continue  # plain HTML — the form resolver's territory
            names: set[str] = set()
            for m in _TAG_RE.finditer(text):
                body = m.group(1) or m.group(2) or ""
                for pm in _PATH_RE.finditer(body):
                    segs = pm.group(0).split(".")
                    if segs[0] in _STOP_HEADS:
                        continue
                    names.update(s for s in segs[1:]
                                 if len(s) >= 3 and s not in _STOP_SEGMENTS)
            hits = {nm: member_ids[nm] for nm in sorted(names) if nm in member_ids}
            if not hits:
                continue
            tid = f"{rel}::template"
            nodes.append(Node(id=tid, kind=NodeKind.TEMPLATE, name=rel,
                              location=f"{rel}:1:0"))
            for nm, cands in hits.items():
                if len(cands) == 1:
                    edges.append(Edge(
                        src=tid, relation=Relation.REFERENCES, dst_symbol=nm,
                        dst_id=cands[0], weight=0.8, provenance=Provenance.INFERRED,
                        location=f"{rel}:1:0", source="heuristic", name_based=True))
                else:
                    w = round(0.8 / len(cands), 3)
                    for cid in cands:
                        edges.append(Edge(
                            src=tid, relation=Relation.REFERENCES, dst_symbol=nm,
                            dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                            location=f"{rel}:1:0", source="heuristic",
                            name_based=True))
        return nodes, edges
