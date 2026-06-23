"""Extractors map source files into the shared node/edge ontology (design §0).

M0 ships a Python extractor built on the stdlib `ast` module — exact for Python,
zero-dependency, keeping the core stdlib-only. tree-sitter + an LSP are the
documented upgrade for incremental reparse, error tolerance, polyglot coverage,
and live types (design §0/§10); the extractor contract (path -> nodes + edges)
is identical, so they swap in without touching the store or operations.
"""

from .python import extract_project

__all__ = ["extract_project"]
