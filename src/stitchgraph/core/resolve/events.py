"""Event / pub-sub resolver (design §2a). Decoupled flow: emit → event → handler.

Detects publish/subscribe patterns by the conventional method names:
- emit:    `.emit("x")`, `.publish("x")`, `.send("x")`, `.dispatch("x")`
- handle:  `.on("x", handler)`, `.subscribe("x", handler)`, `.addEventListener(...)`,
           `.connect(handler)` (named handler)

Creates an `Event` node per name and links the emitting function `EMITS` → event,
and event `HANDLES` → handler. `trace_path` can then cross a decoupled boundary
that the call graph alone can't see (Python via `ast`; same pattern extends to
other languages).
"""

from __future__ import annotations

import ast

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext, iter_function_defs

_EMIT = {"emit", "publish", "send", "dispatch", "fire"}
_HANDLE = {"on", "subscribe", "addEventListener", "add_listener", "connect"}


class EventResolver:
    name = "events"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        events: dict[str, Node] = {}
        edges: list[Edge] = []
        for rel, tree in ctx.parse():
            for func, fid, _ in iter_function_defs(tree, rel):
                if fid not in ctx.ids:
                    continue
                for call in _calls(func):
                    self._handle_call(call, rel, fid, ctx, events, edges)
        return list(events.values()), edges

    def _handle_call(self, call, rel, fid, ctx, events, edges):
        if not isinstance(call.func, ast.Attribute) or not call.args:
            return
        method = call.func.attr
        name = _str(call.args[0])
        loc = f"{rel}:{call.lineno}:0"
        if method in _EMIT and name:
            eid = f"event::{name}"
            events.setdefault(eid, Node(id=eid, kind=NodeKind.EVENT, name=name,
                                        location="event"))
            edges.append(Edge(src=fid, relation=Relation.EMITS, dst_symbol=name,
                              dst_id=eid, weight=0.7, provenance=Provenance.INFERRED,
                              location=loc, source="heuristic"))
        elif method in _HANDLE and name and len(call.args) >= 2:
            handler = _ref(call.args[1])
            eid = f"event::{name}"
            events.setdefault(eid, Node(id=eid, kind=NodeKind.EVENT, name=name,
                                        location="event"))
            cands = ctx.by_name.get(handler, []) if handler else []
            if len(cands) == 1:
                edges.append(Edge(src=eid, relation=Relation.HANDLES, dst_symbol=handler,
                                  dst_id=cands[0], weight=0.7,
                                  provenance=Provenance.INFERRED, location=loc,
                                  source="heuristic"))


def _calls(func):
    out = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            out.append(node)
    return out


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _ref(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None
