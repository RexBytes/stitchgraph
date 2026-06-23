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


# Roots a real Python library+CLI detector will collect (design §4):
#   - public API: __all__ / __init__ exports  (NEVER flag these dead)
#   - [project.scripts] / [project.entry-points]
#   - if __name__ == "__main__"
#   - tests (pytest collection)
PYTHON_LIBRARY_ROOT_KINDS = (NodeKind.TEST, NodeKind.ENDPOINT, NodeKind.HANDLER)
