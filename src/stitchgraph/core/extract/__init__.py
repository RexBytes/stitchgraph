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

from ..model import Edge, Node, NodeKind
from . import python as _python


def extract_project(root: str | Path,
                    ignore: list[str] | None = None, *,
                    cache_asts: bool = True,
                    edge_sink: object = None,
                    report: dict | None = None) -> tuple[list[Node], list[Edge]]:
    # `cache_asts=False` is the streaming (lower-peak-memory) mode for the Python extractor —
    # see python.extract_project. The result is identical; only peak RSS/CPU differ.
    #
    # `edge_sink` (Phase 2b): when given, BOTH extractors stream edges to it as they produce
    # them, so the bulk edge list never materialises in Python. (Until the 2026-07-03 field
    # report this drained the Python extractor's list only AFTER it fully materialised —
    # zero memory reduction on the Python path; a large pure-Python repo (Home Assistant)
    # OOM'd at ~7 GB despite streaming=True while PHP/tree-sitter repos validated fine.)
    # Python edges reach the sink first, then tree-sitter's, mirroring the in-memory order.
    #
    # `report` (research/18 bug 1): when given, gets two keys after the run —
    #   "skipped":  [(rel, exc_name)] files in NO extractor's output (they are MISSING
    #               from the graph; reindex surfaces these as a review reason),
    #   "fallback": [rel] Python files stdlib-ast rejected (syntax newer than the
    #               running interpreter) that the tree-sitter Python grammar rescued
    #               with structural fidelity (defs/calls/inherits/imports; no PDG).
    skips: list[tuple[str, str]] = []
    rescued: set[str] = set()

    def _py_fallback(rels: list[str]) -> tuple[list[Node], list[Edge]]:
        # Called by the Python extractor BETWEEN its passes with the rels ast.parse
        # rejected, so the rescued symbols join its table before reference resolution
        # (v3.37.1 — bolting them on afterwards left every cross-boundary call
        # unresolved and audit_graph recall at 0.299 on HA; research/18 round 2).
        try:
            from . import treesitter
            if not treesitter.HAS_TREE_SITTER:
                return [], []
            fnodes, fedges = treesitter.extract(
                root, ignore, cache_trees=False, py_fallback_only=True,
                py_fallback_files=[Path(root) / rel for rel in rels])
        except Exception as exc:  # noqa: BLE001 — a broken grammar must not kill reindex
            warnings.warn(
                f"tree-sitter Python fallback failed ({type(exc).__name__}: {exc}); "
                f"{len(rels)} file(s) with newer-than-interpreter syntax stay skipped.",
                RuntimeWarning, stacklevel=2)
            return [], []
        # Re-id MODULE nodes from tree-sitter's `rel::stem` to the stdlib-ast
        # convention `rel::dotted.qualname` (packages: `pkg/__init__.py` -> `pkg`),
        # so `import homeassistant.core` binds via `module_by_qual` exactly as it
        # would had ast parsed the file. Function/method ids already match.
        remap: dict[str, str] = {}
        for n in fnodes:
            if n.kind is NodeKind.MODULE:
                rel_f = n.id.split("::", 1)[0]
                dotted = _python._module_qualname(rel_f)
                new_id = f"{rel_f}::{dotted}"
                if new_id != n.id:
                    remap[n.id] = new_id
                    n.id = new_id
                n.name = dotted
        if remap:
            for e in fedges:
                if e.src in remap:
                    e.src = remap[e.src]
                if e.dst_id in remap:
                    e.dst_id = remap[e.dst_id]
        rescued.update({n.id.split("::", 1)[0] for n in fnodes} & set(rels))
        return fnodes, fedges

    nodes, edges = _python.extract_project(root, ignore, cache_asts=cache_asts,
                                           edge_sink=edge_sink, skip_sink=skips,
                                           fallback=_py_fallback)
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
    if report is not None:
        report["skipped"] = [(rel, why) for rel, why in skips if rel not in rescued]
        report["fallback"] = sorted(rescued)
    return nodes, edges


__all__ = ["extract_project"]
