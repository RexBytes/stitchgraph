"""Shared tree-sitter frontend plumbing for the per-language body-matrix modules.

D2 dedup, stage 1 (review 2026-07-03; `research/14` showed the nine `structure_*.py`
frontends as their own 329-node subsystem): the leaf helpers that were byte-identical —
or identical modulo one grammar-specific tuple — across all nine files now live here once,
so the next repeated-field-class fix lands in one place instead of nine. The per-language
`ev`/`do` expression mappings (where the panel-hardened value lives) stay per-language and
are deliberately NOT unified.

Behaviour-preservation is the gate: this module was extracted under a byte-identical
fingerprint/VFG/PDG differential over a 9-language corpus plus the full per-language
completeness batteries and PDG⇄VFG differential oracles.
"""
from __future__ import annotations

# Most grammars expose one "comment" node type; a frontend whose grammar differs passes its
# own tuple (Java: line_comment/block_comment).
_DEFAULT_COMMENT_TYPES: tuple[str, ...] = ("comment",)


def make_parser(grammar: str):
    """A tree-sitter parser for `grammar`, or None if the extra isn't installed — the body
    layer then adds nothing (advisory degrade, identical in every frontend)."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar(grammar))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def nc(node, comment_types: tuple[str, ...] = _DEFAULT_COMMENT_TYPES):
    """Named children minus comment trivia. Tree-sitter exposes comments as named nodes, so any
    positional pick over ``named_children`` (``[0]`` / ``[-1]`` / ``[i]``) can be silently displaced
    by a leading/trailing comment — filter them out before selecting a child by position."""
    return [c for c in node.named_children if c.type not in comment_types]


def first(node, comment_types: tuple[str, ...] = _DEFAULT_COMMENT_TYPES):
    k = nc(node, comment_types)
    return k[0] if k else None


def last(node, comment_types: tuple[str, ...] = _DEFAULT_COMMENT_TYPES):
    k = nc(node, comment_types)
    return k[-1] if k else None


def op_text(node) -> str:
    """The operator token of a binary/unary/assignment node: the `operator` field when the
    grammar names one, else the first anonymous child (operator isn't a named field on every
    grammar version)."""
    op = node.child_by_field_name("operator")
    if op is not None:
        return op.text.decode("utf-8", "replace")
    for c in node.children:
        if not c.is_named and c.text:
            return c.text.decode("utf-8", "replace")
    return "?"
