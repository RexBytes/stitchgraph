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


def node_text(n) -> str:
    """A node's source text, decoded tolerantly — the `text` helper every builder redefined."""
    return n.text.decode("utf-8", "replace")


def parse_tree(parser, source: str):
    """The shared `_walk` entry guard: `(tree, data)` or None on a missing extra / unparseable
    or too-deep source (advisory degrade — the body layer then adds nothing)."""
    if parser is None:
        return None
    try:
        data = source.encode("utf-8", "replace")
        return parser.parse(data), data
    except (ValueError, RecursionError):
        return None


def vfg_state():
    """The shared value-flow-graph builder state: `(g, env, free, freevar)`. Every frontend's
    `_build_vfg` opened with this exact block; the per-language `ev`/`do`/`bind` logic stays local."""
    from .structure import _VFG
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    return g, env, free, freevar


def pdg_state():
    """The shared program-dependence-graph builder state:
    `(nodes, edges, last_def, new_id, data_from)`. `data_from` is the R205-hardened data-edge
    emission: name sets iterate SORTED, because a string set iterates in PYTHONHASHSEED order,
    which would make the edge list (and get_matrix cells) non-reproducible across processes —
    previously duplicated (comment and all) in every frontend."""
    nodes: dict[int, str] = {}
    edges: list[tuple[int, int, str]] = []
    counter = [0]
    last_def: dict[str, int] = {}

    def new_id(label: str) -> int:
        i = counter[0]
        counter[0] += 1
        nodes[i] = label
        return i

    def data_from(loads: set, stores: set, sid: int) -> None:
        for nm in sorted(loads):
            if nm in last_def and last_def[nm] != sid:
                edges.append((last_def[nm], sid, "D"))
        for nm in sorted(stores):
            last_def[nm] = sid

    return nodes, edges, last_def, new_id, data_from


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
