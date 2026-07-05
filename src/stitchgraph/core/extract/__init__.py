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

import warnings
from pathlib import Path

from ..model import Edge, Node
from . import python as _python


def extract_project(root: str | Path,
                    ignore: list[str] | None = None, *,
                    cache_asts: bool = True,
                    edge_sink: object = None) -> tuple[list[Node], list[Edge]]:
    # `cache_asts=False` is the streaming (lower-peak-memory) mode for the Python extractor —
    # see python.extract_project. The result is identical; only peak RSS/CPU differ.
    #
    # `edge_sink` (Phase 2b): when given, BOTH extractors stream edges to it as they produce
    # them, so the bulk edge list never materialises in Python. (Until the 2026-07-03 field
    # report this drained the Python extractor's list only AFTER it fully materialised —
    # zero memory reduction on the Python path; a large pure-Python repo (Home Assistant)
    # OOM'd at ~7 GB despite streaming=True while PHP/tree-sitter repos validated fine.)
    # Python edges reach the sink first, then tree-sitter's, mirroring the in-memory order.
    nodes, edges = _python.extract_project(root, ignore, cache_asts=cache_asts,
                                           edge_sink=edge_sink)
    try:
        from . import treesitter
        if treesitter.HAS_TREE_SITTER:
            # The same streaming flag drives both extractors: in streaming mode tree-sitter
            # drops each file's parse tree + source after pass 1 (Magento/PHP's memory hog),
            # exactly as the Python extractor drops its ASTs. Result is identical either way.
            jn, je = treesitter.extract(root, ignore, cache_trees=cache_asts,
                                        edge_sink=edge_sink)
            nodes += jn
            edges += je  # je is empty when a sink is used
    except Exception as exc:  # noqa: BLE001 — must never break Python extraction
        # Keep Python results, but do NOT vanish silently: a blanket swallow turned a
        # broken tree-sitter install into "ran fine, found nothing" (issue #7).
        warnings.warn(
            f"tree-sitter extraction failed ({type(exc).__name__}: {exc}); non-Python "
            f"files were not analysed. Python results are unaffected.",
            RuntimeWarning, stacklevel=2)
    return nodes, edges


__all__ = ["extract_project"]
