"""Jedi resolver (design §5 — live resolution, optional).

Upgrades call resolution from name/scope heuristics to precise go-to-definition
using jedi (in-process, no subprocess — LSP-grade resolution without LSP
plumbing). For each call site it asks jedi for the definition and, when that
definition maps to a known node, adds a confident CALLS edge.

Opt-in (`reindex(..., precise=True)` / `--precise`) because jedi is an optional
dependency and per-call inference costs time; the default pipeline stays
zero-dependency. Every jedi call is guarded — any failure is skipped, never
fatal, so enabling precision can only *add* resolution, never break extraction.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..envelope import Provenance
from ..model import Edge, Node, Relation
from . import ResolveContext, iter_function_defs


class JediResolver:
    name = "jedi"

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        try:
            import jedi
        except ModuleNotFoundError:  # pragma: no cover
            return [], []
        from ..extract.python import _direct_calls, _name_of

        loc = _location_index(ctx.nodes)
        edges: list[Edge] = []
        for rel, tree in ctx.parse():
            path = ctx.root / rel
            try:
                script = jedi.Script(path=str(path))
            except Exception:  # noqa: BLE001
                continue
            for func, fid, _ in iter_function_defs(tree, rel):
                if fid not in ctx.ids:
                    continue
                for call in _direct_calls(func):
                    name = _name_of(call.func)
                    pos = _ref_pos(call.func)
                    if name is None or pos is None:
                        continue
                    tid = _goto(script, ctx.root, loc, *pos)
                    if tid and tid != fid:
                        edges.append(Edge(
                            src=fid, relation=Relation.CALLS, dst_symbol=name,
                            dst_id=tid, weight=1.0, provenance=Provenance.EXTRACTED,
                            location=f"{rel}:{call.lineno}:0", source="jedi"))
        return [], edges


def _goto(script, root: Path, loc: dict, line: int, col: int) -> str | None:
    try:
        defs = script.goto(line, col, follow_imports=True)
    except Exception:  # noqa: BLE001
        return None
    for d in defs:
        if d.module_path is None:
            continue
        try:
            drel = Path(d.module_path).relative_to(root).as_posix()
        except ValueError:
            continue  # outside the project (stdlib / third-party)
        tid = loc.get((drel, d.line))
        if tid:
            return tid
    return None


def _location_index(nodes: list[Node]) -> dict[tuple[str, int], str]:
    idx: dict[tuple[str, int], str] = {}
    for n in nodes:
        parts = n.location.rsplit(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            idx[(parts[0], int(parts[1]))] = n.id
    return idx


def _ref_pos(func: ast.AST) -> tuple[int, int] | None:
    """jedi position (1-based line, 0-based col) of the called name."""
    if isinstance(func, ast.Name):
        return func.lineno, func.col_offset
    if isinstance(func, ast.Attribute):
        end_col = getattr(func, "end_col_offset", None)
        end_line = getattr(func, "end_lineno", None)
        if end_col is None or end_line is None:
            return None
        return end_line, max(end_col - len(func.attr), 0)
    return None
