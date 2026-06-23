"""Extractors map source files into the shared node/edge ontology (design §0).

`extract_project` is the polyglot dispatcher: it runs each language's extractor
over its files and merges the results into one graph. Cross-language links are
then added by the resolver pipeline (`core/resolve/`).

- **Python** — stdlib `ast` (always available, fully analysed).
- **JavaScript / TypeScript** — tree-sitter (optional; skipped if not installed).

Adding a language is additive: write an extractor with the contract
`(root, ignore) -> (nodes, edges)` and merge it here. The store, algebra,
resolvers, and tools are untouched.
"""

from __future__ import annotations

from pathlib import Path

from ..model import Edge, Node
from . import python as _python


def extract_project(root: str | Path,
                    ignore: list[str] | None = None) -> tuple[list[Node], list[Edge]]:
    nodes, edges = _python.extract_project(root, ignore)
    try:
        from . import treesitter
        if treesitter.HAS_TREE_SITTER:
            jn, je = treesitter.extract(root, ignore)
            nodes += jn
            edges += je
    except Exception:  # noqa: BLE001 — a polyglot extractor must never break Python
        pass
    return nodes, edges


__all__ = ["extract_project"]
