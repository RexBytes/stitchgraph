"""Cross-language / framework resolver pipeline (design §2a, the M3 'gem').

Resolvers run *after* extraction and enrich the graph with edges that span a
single language's syntax — HTML/route -> handler, query -> table/column — the
links no single-language tool offers. Each is a plugin so you add only the
frameworks you actually use (design principle 6).

Contract: a Resolver reads the project + current nodes/edges and returns *extra*
nodes and edges (typically INFERRED/AMBIGUOUS with confidence < 1, because these
links are heuristic). The pipeline merges them. Resolvers never mutate source.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..model import Edge, Node


@dataclass
class ResolveContext:
    root: Path
    nodes: list[Node]
    edges: list[Edge]
    by_name: dict[str, list[str]] = field(default_factory=dict)
    ids: set[str] = field(default_factory=set)

    def parse(self) -> Iterator[tuple[str, ast.Module]]:
        """Yield (rel_path, tree) for each parseable .py file under root."""
        for path in sorted(self.root.rglob("*.py")):
            parts = path.relative_to(self.root).parts
            if any(p in {".venv", "venv", "build", "dist", "__pycache__", ".git"}
                   for p in parts):
                continue
            if not path.is_file():
                continue  # skip FIFOs/dirs/dead symlinks: open() on a FIFO blocks
                          # forever (panel FFF) and never raises the OSError caught below
            try:
                yield path.relative_to(self.root).as_posix(), ast.parse(
                    path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError, RecursionError):
                continue  # broken symlink / unreadable file, or a pathologically deep AST
                          # (panel OOO) — skip the one file, don't abort the run (panel DDD)


class Resolver(Protocol):
    name: str

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]: ...


def run_resolvers(root: str | Path, nodes: list[Node], edges: list[Edge],
                  resolvers: Iterable[Resolver] | None = None) -> tuple[list[Node], list[Edge]]:
    """Run resolvers over an extracted graph; return the enriched graph."""
    if resolvers is None:
        resolvers = default_resolvers()
    ctx = ResolveContext(
        root=Path(root), nodes=list(nodes), edges=list(edges),
        by_name=_name_index(nodes), ids={n.id for n in nodes},
    )
    for resolver in resolvers:
        try:
            new_nodes, new_edges = resolver.resolve(ctx)
        except Exception:  # noqa: BLE001
            # Resolvers are heuristic enrichment that must NEVER abort the core reindex.
            # A pathologically deep tree (RecursionError, panels QQQ/RRR) or an unexpected
            # parser shape (e.g. sqlglot returning a bool `.this` for `DELETE TABLE`, panel
            # crash-sweep) should drop this resolver's extra edges, not crash the run. The
            # base graph + other resolvers are unaffected.
            continue
        for n in new_nodes:
            if n.id not in ctx.ids:
                ctx.nodes.append(n)
                ctx.ids.add(n.id)
                ctx.by_name.setdefault(n.name, []).append(n.id)
        ctx.edges.extend(new_edges)
    return ctx.nodes, ctx.edges


def default_resolvers() -> list[Resolver]:
    from .events import EventResolver
    from .express import ExpressRouteResolver
    from .html import HtmlRouteResolver
    from .jsfetch import JsFetchResolver
    from .orm import OrmResolver
    from .routes import WebRouteResolver
    from .spring import SpringRouteResolver
    from .sql import SqlResolver
    # Route producers first (HTML/JS-fetch link to the Route nodes they create).
    return [WebRouteResolver(), ExpressRouteResolver(), SpringRouteResolver(),
            HtmlRouteResolver(), JsFetchResolver(), EventResolver(),
            OrmResolver(), SqlResolver()]


# -- shared helper: iterate function defs with their stable node ids ---------
def iter_function_defs(tree: ast.Module, rel: str) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, str]]:
    """Yield (func_ast, node_id, enclosing_module_or_func_id) for every def,
    matching the extractor's id scheme so resolver edges line up with extractor
    nodes."""
    from ..extract.python import _module_qualname  # reuse the id convention

    module_qual = _module_qualname(rel)
    mod_id = f"{rel}::{module_qual}"

    def walk(node: ast.AST, parent: str) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, str]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{parent}.{child.name}" if parent else child.name
                cid = f"{rel}::{qual}"
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield child, cid, mod_id
                yield from walk(child, qual)

    yield from walk(tree, "")


def iter_class_defs(tree: ast.Module, rel: str) -> Iterator[tuple[ast.ClassDef, str]]:
    """Yield (class_ast, node_id) for every class, matching the extractor's ids."""
    def walk(node: ast.AST, parent: str) -> Iterator[tuple[ast.ClassDef, str]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qual = f"{parent}.{child.name}" if parent else child.name
                yield child, f"{rel}::{qual}"
                yield from walk(child, qual)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{parent}.{child.name}" if parent else child.name
                yield from walk(child, qual)

    yield from walk(tree, "")


def _name_index(nodes: list[Node]) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for n in nodes:
        idx.setdefault(n.name, []).append(n.id)
    return idx
