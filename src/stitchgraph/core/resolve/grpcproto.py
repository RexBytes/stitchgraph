"""gRPC / protobuf contract resolver (design §2a, STATUS roadmap: contract
resolvers). A `.proto` service definition is the routing table of an RPC
service: each `rpc` becomes a ROUTE node (`{rel}::rpc:{Service}.{Method}`), and
the node links to the server-side implementations — methods named `{Method}` on
classes following the generated-stub conventions (`{Service}Servicer` for
grpcio, `{Service}Base` for grpclib, `{Service}Impl`) — so servicer methods,
which nothing in the static call graph ever calls, stop surfacing as dead code.

Parsing is a deliberately small regex pass (no protobuf dependency): `service X
{ rpc M (...) returns (...); }`. Precision over recall: a Method with no
conventionally-named implementing class still gets its ROUTE node (a real root),
but only falls back to name-wide AMBIGUOUS linking when at least one candidate's
qualified id mentions the service name — never blanket edges to every same-named
method in the repo.
"""

from __future__ import annotations

import re

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext

_SERVICE = re.compile(r"\bservice\s+(\w+)\s*\{", re.MULTILINE)
_RPC = re.compile(r"\brpc\s+(\w+)\s*\(")
_IMPL_SUFFIXES = ("Servicer", "Base", "Impl")


def _service_blocks(text: str):
    """Yield (service_name, body) for each `service X { ... }` block — brace-matched
    from the header, so nested message blocks inside don't truncate the body."""
    for m in _SERVICE.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        yield m.group(1), text[m.end():i - 1]


class GrpcProtoResolver:
    name = "grpc-proto"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        for path in sorted(ctx.root.rglob("*.proto")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(ctx.root).as_posix()
            for service, body in _service_blocks(text):
                for rpc in _RPC.finditer(body):
                    method = rpc.group(1)
                    rid = f"{rel}::rpc:{service}.{method}"
                    nodes.append(Node(
                        id=rid, kind=NodeKind.ROUTE, name=f"rpc {service}.{method}",
                        location=f"{rel}:1:0", roles=frozenset({"route"})))
                    cands = [hid for hid in ctx.by_name.get(method, [])
                             if any(f"{service}{sfx}." in hid
                                    for sfx in _IMPL_SUFFIXES)]
                    if not cands:
                        # fall back only to candidates whose id mentions the service
                        # at all — never every same-named method in the repo
                        cands = [hid for hid in ctx.by_name.get(method, [])
                                 if service in hid]
                    prov = (Provenance.INFERRED if len(cands) == 1
                            else Provenance.AMBIGUOUS)
                    for hid in cands:
                        edges.append(Edge(
                            src=rid, relation=Relation.ROUTES_TO, dst_symbol=method,
                            dst_id=hid, weight=0.9 if len(cands) == 1 else 0.5,
                            provenance=prov, location=f"{rel}:1:0",
                            source="grpc-proto"))
        return nodes, edges
