"""OpenAPI / Swagger contract resolver (design §2a, STATUS roadmap: contract
resolvers). A spec file is the routing table of a spec-first service: each
`paths` entry becomes a ROUTE node with the SAME id convention as the code-first
route resolvers (`{rel}::route:{METHOD} {path}`), so `<form action>` and JS
`fetch` links converge on it — and each `operationId` links the route to its
same-named handler function(s), which stops spec-wired handlers being flagged
dead for lack of a static caller.

JSON specs parse with stdlib; YAML specs need PyYAML (present in the default
full-power install; without it YAML specs are skipped silently — guarded import,
the modes.py pattern). Precision over recall throughout: no operationId match →
the route node still exists (a real root), just no handler edge; several
same-named handlers → one AMBIGUOUS edge each, recorded as such.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from . import ResolveContext

try:
    import yaml as _yaml  # type: ignore[import-untyped]
except Exception:  # noqa: BLE001 — YAML specs are skipped without PyYAML
    _yaml = None  # type: ignore[assignment]

_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_SPEC_NAMES = ("openapi", "swagger")  # filename stems commonly used for specs


def _load_spec(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    data = None
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            return None
    elif _yaml is not None:
        try:
            data = _yaml.safe_load(text)
        except Exception:  # noqa: BLE001 — malformed YAML is just not a spec
            return None
    if (isinstance(data, dict) and isinstance(data.get("paths"), dict)
            and ("openapi" in data or "swagger" in data)):
        return data
    return None


class OpenApiResolver:
    name = "openapi"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []
        for path in sorted(ctx.root.rglob("*")):
            if path.suffix not in (".json", ".yaml", ".yml") or not path.is_file():
                continue
            stem = path.stem.lower()
            # cheap pre-filter: conventional names, or any yaml/json mentioning a
            # spec key in its first 200 bytes — avoids parsing every data file
            if not any(s in stem for s in _SPEC_NAMES):
                try:
                    head = path.open("rb").read(200)
                except OSError:
                    continue
                if b"openapi" not in head and b"swagger" not in head:
                    continue
            spec = _load_spec(path)
            if spec is None:
                continue
            rel = path.relative_to(ctx.root).as_posix()
            for route_path, ops in spec["paths"].items():
                if not isinstance(ops, dict):
                    continue
                for method, op in ops.items():
                    if method not in _METHODS or not isinstance(op, dict):
                        continue
                    m = method.upper()
                    rid = f"{rel}::route:{m} {route_path}"
                    nodes.append(Node(
                        id=rid, kind=NodeKind.ROUTE, name=f"{m} {route_path}",
                        location=f"{rel}:1:0", roles=frozenset({"route"})))
                    op_id = op.get("operationId")
                    if not isinstance(op_id, str) or not op_id:
                        continue
                    cands = ctx.by_name.get(op_id, [])
                    prov = (Provenance.INFERRED if len(cands) == 1
                            else Provenance.AMBIGUOUS)
                    for hid in cands:  # all same-named handlers (precision over recall)
                        edges.append(Edge(
                            src=rid, relation=Relation.ROUTES_TO, dst_symbol=op_id,
                            dst_id=hid, weight=0.9 if len(cands) == 1 else 0.5,
                            provenance=prov, location=f"{rel}:1:0",
                            source="openapi-spec"))
        return nodes, edges
