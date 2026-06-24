"""Entry-point detection (design §4, Entry-point detection contract).

The linchpin: dead-code/hole *liveness* is entirely bounded by the entry-point
set, and no static detector catches every dynamic root — so a user override
(`stitchgraph.toml [entry_points]`) always augments whatever a detector finds.

A detector implements `detect(store) -> set[node_id]`. The first real detector
targets the Python **library + CLI** shape; for M0 it is a structural stub that
returns the override set plus any obvious roots already in the graph, and flags
that automatic detection is not yet wired so callers stay honest about it.
"""

from __future__ import annotations

from typing import Protocol

from .model import NodeKind
from .store import Store


class EntryPointDetector(Protocol):
    def detect(self, store: Store) -> set[str]: ...


class ConfigOnlyDetector:
    """M0 placeholder: roots come only from the user override allowlist.

    This deliberately does NOT guess. Until the real Python detector lands,
    `find_stale` must report low confidence + needs_review, because an empty or
    partial entry set produces false 'dead' verdicts — the dangerous failure.
    """

    not_implemented = True

    def __init__(self, overrides: set[str] | None = None) -> None:
        self.overrides = overrides or set()

    def detect(self, store: Store) -> set[str]:
        # Only return overrides that actually exist as nodes.
        return {nid for nid in self.overrides if store.get_node(nid) is not None}


class PythonLibraryDetector:
    """Real detector for the Python library + CLI shape (design §4).

    Roots = the export surface (public API) ∪ __main__ modules ∪ console-script
    targets ∪ tests ∪ user overrides. Critically, **exported public symbols are
    roots** — a library's public API is called by unknown external code, so it is
    never dead for lack of internal callers.

    Role tags are recorded by the extractor (exported / main / script / test);
    this detector applies policy over them.
    """

    not_implemented = False

    def __init__(self, overrides: set[str] | None = None, *,
                 include_tests: bool = True) -> None:
        self.overrides = overrides or set()
        self.include_tests = include_tests

    def detect(self, store: Store) -> set[str]:
        roots: set[str] = set()
        # 'runtime' = observed executing in a trace -> definitely live (design §2c);
        # 'callback' = a framework-invoked override (design §7).
        for role in ("exported", "main", "script", "route", "runtime", "callback"):
            roots.update(n.id for n in store.nodes_with_role(role))
        if self.include_tests:
            roots.update(n.id for n in store.nodes_with_role("test"))
        # HTTP routes / endpoints are entry points (external callers).
        for kind in (NodeKind.ROUTE, NodeKind.ENDPOINT):
            roots.update(n.id for n in store.nodes_by_kind(kind))
        roots.update(nid for nid in self.overrides if store.get_node(nid) is not None)
        # A module's top-level code runs when the module is loaded, and the module is
        # loaded whenever any symbol it defines is reached (you can't call an exported
        # function or import a name without executing the module body). So a module that
        # owns any root is itself a load root: its module-level uses — registries, dispatch
        # tables, instantiations — then propagate liveness, instead of live code used only
        # at module scope being flagged dead (panel R12, cardinal). Module nodes are not
        # dead-code candidates, so seeding them never introduces a false dead.
        root_files = {rid.split("::", 1)[0] for rid in roots}
        roots.update(m.id for m in store.nodes_by_kind(NodeKind.MODULE)
                     if m.id.split("::", 1)[0] in root_files)
        return roots


# Roots a Python library+CLI detector collects (design §4):
#   - public API: __all__ / __init__ exports  (NEVER flag these dead)
#   - [project.scripts] / [project.entry-points]
#   - if __name__ == "__main__"
#   - tests (pytest collection)
PYTHON_LIBRARY_ROOT_KINDS = (NodeKind.TEST, NodeKind.ENDPOINT, NodeKind.HANDLER)
