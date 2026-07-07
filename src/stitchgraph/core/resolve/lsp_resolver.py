"""LSP resolver (design §5, research/24) — jedi's move for every other language.

For each name-based CALLS edge a tree-sitter language emitted (one name, maybe
many candidates), ask the language's server for the definition at the call site
and, when the answer maps to a known node, add a confident EXTRACTED edge with
`source="lsp"` — exactly the shape the jedi resolver adds for Python, deduped
and compressed by the same machinery.

Monotone by contract: the AMBIGUOUS arms stay (a server mis-answer must never
silently drop a true edge); every ranking already prefers EXTRACTED over
AMBIGUOUS, so precision lands where it is read. Opt-in (`reindex(..., lsp=True)`
/ `--lsp` / `[lsp] enabled`) because it spawns external binaries; a missing or
broken server degrades to zero extra edges, never to an error.

Driven from the EXTRACTED GRAPH, not a re-parse: tree-sitter call edges carry
`rel:line:0` locations (line-only), so the callee's column is recovered by
searching that source line for the symbol's last path segment. Definition
answers are mapped to nodes by line CONTAINMENT (innermost node whose
[start, end_line] span holds the answered line) — servers answer name
positions, tree-sitter nodes may start on a decorator/annotation line, so
exact-line equality is too brittle.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from ..envelope import Provenance
from ..model import Edge, Node, NodeKind, Relation
from .lsp import LspClient, server_for

if TYPE_CHECKING:
    from . import ResolveContext

_SITE_CAP = 20_000  # per server, an honest bound reported when hit
_DEF_KINDS = frozenset({
    NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS, NodeKind.VARIABLE,
})


class LspResolver:
    name = "lsp"

    def __init__(self, servers: dict[str, str] | None = None,
                 timeout: float = 15.0) -> None:
        self.overrides = servers or {}
        self.timeout = timeout
        # reindex report: {"<cmd>": {"sites": n, "resolved": n}} plus declines
        self.report: dict[str, dict] = {}

    def resolve(self, ctx: ResolveContext) -> tuple[list[Node], list[Edge]]:
        rows = [(e.src, e.dst_symbol, e.location,
                 e.provenance is Provenance.AMBIGUOUS)
                for e in ctx.edges
                if (e.relation is Relation.CALLS and e.source == "tree-sitter"
                    and e.dst_symbol is not None)]
        return [], self.resolve_rows(ctx.root, ctx.nodes, rows)

    def resolve_rows(self, root: Path, nodes: list[Node],
                     rows: list[tuple[str, str, str, bool]]) -> list[Edge]:
        """The store-driven entry the streaming reindex uses (its resolvers see
        an empty edge list by design — edges are already on disk). `rows` are
        (src id, dst_symbol, location, is_ambiguous) call-edge tuples."""
        root = Path(root)
        sites = self._sites(rows)
        if not sites:
            return []
        span_index = _span_index(nodes)
        edges: list[Edge] = []
        for cmd, cmd_sites in sites.items():
            stats = self.report.setdefault(cmd, {"sites": 0, "resolved": 0})
            if len(cmd_sites) > _SITE_CAP:
                stats["capped"] = len(cmd_sites) - _SITE_CAP
                cmd_sites = cmd_sites[:_SITE_CAP]
            stats["sites"] += len(cmd_sites)
            client = LspClient(cmd, root, timeout=self.timeout)
            if not client.start():
                stats["declined"] = "server unavailable"
                continue
            try:
                edges.extend(self._resolve_server(root, client, cmd_sites,
                                                  span_index, stats))
            finally:
                client.stop()
        return edges

    # -- site discovery ----------------------------------------------------
    def _sites(self, rows: list[tuple[str, str, str, bool]]):
        """Deduped call sites grouped by server command: (src id, rel, line,
        symbol, languageId). One query per (rel, line, symbol) — a widened
        fan-out contributes ONE site, not one per candidate arm. AMBIGUOUS
        sites sort first so the cap spends its budget where precision pays."""
        by_cmd: dict[str, list] = defaultdict(list)
        seen: set[tuple[str, int, str]] = set()
        for src, symbol, location, ambiguous in rows:
            parts = location.rsplit(":", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            rel, line = parts[0], int(parts[1])
            server = server_for(Path(rel).suffix, self.overrides)
            if server is None:
                continue
            key = (rel, line, symbol)
            if key in seen:
                continue
            seen.add(key)
            cmd, language_id = server
            by_cmd[cmd].append((not ambiguous, src, rel, line, symbol, language_id))
        return {cmd: [s[1:] for s in sorted(sites)]
                for cmd, sites in by_cmd.items()}

    # -- per-server pass -----------------------------------------------------
    def _resolve_server(self, root: Path, client: LspClient,
                        sites: list, span_index, stats: dict) -> list[Edge]:
        edges: list[Edge] = []
        line_cache: dict[str, list[str]] = {}
        warmed = False
        # group by file so each is opened once, in stable order
        by_rel: dict[str, list] = defaultdict(list)
        for site in sites:
            by_rel[site[1]].append(site)
        for rel in sorted(by_rel):
            language_id = by_rel[rel][0][4]
            if not client.did_open(rel, language_id):
                continue
            for src, _rel, line, symbol, _lang in by_rel[rel]:
                for col in _columns(root, rel, line, symbol, line_cache):
                    if not warmed:
                        client.warm_up(rel, line, col)
                        warmed = True
                    tid = _map_definitions(
                        client.definition(rel, line, col), span_index)
                    if tid is None or tid == src:
                        continue
                    edges.append(Edge(
                        src=src, relation=Relation.CALLS, dst_symbol=symbol,
                        dst_id=tid, weight=1.0, provenance=Provenance.EXTRACTED,
                        location=f"{rel}:{line}:0", source="lsp"))
                    stats["resolved"] += 1
                    break  # first mapping occurrence wins for this site
        return edges


def _columns(root: Path, rel: str, line: int, symbol: str,
             cache: dict[str, list[str]]):
    """0-based columns of `symbol`'s last path segment on the source line, in
    order — `util::greet` / `a.b.greet` query at `greet`. Word-bounded so
    `greet` never matches inside `greeting`."""
    if rel not in cache:
        try:
            cache[rel] = (root / rel).read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            cache[rel] = []
    lines = cache[rel]
    if not 1 <= line <= len(lines):
        return
    leaf = re.split(r"::|\.", symbol)[-1]
    if not leaf or not re.match(r"\w+$", leaf):
        return
    for m in re.finditer(rf"(?<![\w$]){re.escape(leaf)}(?![\w$])", lines[line - 1]):
        yield m.start()


def _map_definitions(defs: list[tuple[str, int, int]], span_index) -> str | None:
    for drel, dline, _dchar in defs:
        tid = span_index(drel, dline)
        if tid is not None:
            return tid
    return None


def _span_index(nodes: list[Node]):
    """(rel, line) -> innermost def node containing that line. Nodes without a
    parseable location or a def-like kind are skipped; ties (same start) pick
    the narrower end, matching lexical nesting."""
    by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for n in nodes:
        if n.kind not in _DEF_KINDS:
            continue
        parts = n.location.rsplit(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        start = int(parts[1])
        end = n.end_line if isinstance(n.end_line, int) and n.end_line >= start \
            else start
        by_file[parts[0]].append((start, end, n.id))
    for spans in by_file.values():
        spans.sort()

    def lookup(rel: str, line: int) -> str | None:
        best: tuple[int, str] | None = None
        for start, end, nid in by_file.get(rel, ()):
            if start > line:
                break
            if line <= end and (best is None or end - start <= best[0]):
                best = (end - start, nid)
        return best[1] if best else None

    return lookup
