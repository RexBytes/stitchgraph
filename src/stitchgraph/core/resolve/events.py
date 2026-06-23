"""Event / pub-sub resolver (design §2a). Decoupled flow: emit → event → handler.

Detects publish/subscribe patterns by the conventional method names:
- emit:    `.emit("x")`, `.publish("x")`, `.send("x")`, `.dispatch("x")`
- handle:  `.on("x", handler)`, `.subscribe("x", handler)`, `.addEventListener(...)`
- signal:  `signal.connect(handler)` — single-arg registration (blinker / Django
           signals / Qt), where the *receiver object* is the event, met by a bare
           `signal.send(...)` emit on the same object.

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
# Single-arg callback registration (blinker/Django signals, Qt, DOM): the event is
# the *receiver* object, not a string argument — `signal.connect(handler)`.
_SIGNAL_CONNECT = {"connect", "addEventListener", "add_listener"}


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
        if method in _EMIT:
            # String name -> event::name; a bare emitter (`signal.send(obj)`) keys
            # on the receiver so it meets the matching `.connect()` registration.
            eid = f"event::{name}" if name else _receiver_event_id(call.func.value)
            if not eid:
                return
            label = name or eid.rsplit(":", 1)[-1]
            events.setdefault(eid, Node(id=eid, kind=NodeKind.EVENT, name=label,
                                        location="event"))
            edges.append(Edge(src=fid, relation=Relation.EMITS, dst_symbol=label,
                              dst_id=eid, weight=0.7, provenance=Provenance.INFERRED,
                              location=loc, source="heuristic"))
        elif method in _HANDLE:
            if name and len(call.args) >= 2:
                handler = _ref(call.args[1])               # .on("x", handler)
                eid = f"event::{name}"
                label = name
            elif method in _SIGNAL_CONNECT:
                handler = _ref(call.args[0])               # signal.connect(handler)
                eid = _receiver_event_id(call.func.value)
                label = eid.rsplit(":", 1)[-1] if eid else None
            else:
                return
            if not eid:
                return
            events.setdefault(eid, Node(id=eid, kind=NodeKind.EVENT, name=label,
                                        location="event"))
            self._handles_edges(edges, eid, handler, ctx, loc)

    def _handles_edges(self, edges, eid, handler, ctx, loc):
        # Link to *all* same-named handlers, not just an unambiguous one: dropping
        # the edge when a name is shared would flag a live handler dead (precision
        # over recall). Ambiguity is recorded on each edge.
        cands = ctx.by_name.get(handler, []) if handler else []
        if not cands:
            return
        if len(cands) == 1:
            edges.append(Edge(src=eid, relation=Relation.HANDLES, dst_symbol=handler,
                              dst_id=cands[0], weight=0.7, provenance=Provenance.INFERRED,
                              location=loc, source="heuristic"))
        else:
            w = round(0.7 / len(cands), 3)
            for cid in cands:
                edges.append(Edge(src=eid, relation=Relation.HANDLES, dst_symbol=handler,
                                  dst_id=cid, weight=w, provenance=Provenance.AMBIGUOUS,
                                  location=loc, source="heuristic"))


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


def _receiver_event_id(recv):
    """Event id derived from a signal/emitter object expression, so a bare
    `signal.send(...)` emit and a `signal.connect(handler)` registration meet on
    the same event node."""
    base = _ref(recv)
    return f"event::signal:{base}" if base else None
