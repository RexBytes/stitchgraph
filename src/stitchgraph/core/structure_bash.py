"""Structural (body-level) fingerprints for Bash/shell functions.

The Bash frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So Bash↔Bash bodies compare exactly the way every
other per-language frontend's do (one shared WL kernel).

Bash is the outlier of the sweep — **command-oriented, not expression-oriented**. The model:
- a `command` (`name arg…`) is a CALL — the command name is the callee, its arguments flow as data;
- `$(…)` / `` `…` `` **command substitution** carries the value of the command it runs;
- a `variable_assignment` (`x=…`, `local x=…`) binds the name to its value (copy propagation);
- `$x` / `${x}` **expansions** are variable reads; a string carries flow through its `$(…)`/`$x` holes;
- `$(( … ))` arithmetic, `[[ … ]]`/`[ … ]` tests, pipelines, and if/for/while/case are walked for
  their control + data flow.

Functions are keyed by their bare name (`compute`) — shell functions are flat, matching the extractor.
It is a structural approximation, NOT sound data flow (no word-splitting/alias analysis, exit codes
and side-effecting globals are not tracked, constants collapsed). Two known structural blind spots are
inherent and accepted: (1) a `${var#$(cmd)}`/`${var%…}` **strip pattern** is lexed by tree-sitter as a
single opaque `regex` token, so a command substitution *inside* the strip pattern is not a walkable
child; (2) a **single-quoted deferred action** like `trap '$(cmd)' EXIT` is a `raw_string` whose
expansion only happens at `eval`/trap time — the no-`eval` rule means it reads as a constant. Both are
advisory-only mis-rankings, never cardinal. The method is in `docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _wl_features

_EXTS = {".sh": "bash", ".bash": "bash"}

_FUNC_NODES = frozenset({"function_definition"})

# Leaf literals — one CONST node regardless of value. (A `string`/`raw_string` is handled specially
# so its `$(…)`/`$x` holes carry flow.)
_CONST = frozenset({"number", "raw_string", "word", "regex"})


def _parser():
    """A tree-sitter Bash parser, or None if the extra isn't installed."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar("bash"))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def fingerprint_source(source: str, lang: str = "bash") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function in a Bash source string, keyed by the bare function name (shell
    functions are flat). Returns {} on a parse failure, a missing tree-sitter extra, or a too-deep
    tree (advisory, never raises)."""
    parser = _parser()
    if parser is None:
        return {}
    try:
        data = source.encode("utf-8", "replace")
        tree = parser.parse(data)
    except (ValueError, RecursionError):
        return {}
    out: dict[str, collections.Counter[str]] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def emit(name: str, fn_node) -> None:
        if not name:
            return
        try:
            out[name] = _wl_features(_build_vfg(fn_node, data))
        except RecursionError:
            pass

    def visit(node) -> None:
        for child in node.named_children:
            if child.type == "function_definition":
                nm = child.child_by_field_name("name")
                if nm is not None:
                    emit(text(nm), child)
                # nested function definitions inside a body are opaque — don't recurse for keys.
            else:
                visit(child)

    try:
        visit(tree.root_node)
    except (RecursionError, ValueError):
        return out
    return out


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one Bash function node into a value-flow graph, mirroring
    `structure._build_vfg`: operations + control points as nodes, data/control edges, copy
    propagation through assignments. Command-oriented (a command is a CALL)."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    def _varname(node) -> str:
        # `$x` (simple_expansion) / `${x}` (expansion) -> the bare variable name `x`.
        t = text(node).lstrip("$").strip("{}")
        return t.split("[", 1)[0].split(":", 1)[0] or t

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t == "variable_name":
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t == "simple_expansion":
            name = _varname(node)
            return env[name] if name in env else freevar(name)
        if t == "expansion":
            # ${x}, ${x:-default}, ${x[i]} — read x, and any sub-expressions (default/index) flow.
            n = g.add("ATTR")
            name = _varname(node)
            g.link(env[name] if name in env else freevar(name), n, _DATA)
            for c in node.named_children:
                if c.type == "subscript":
                    # ${arr[i]} — the base name is already read above; the index expression carries
                    # flow (e.g. ${arr[$(helper)]}).
                    idx = c.child_by_field_name("index")
                    if idx is not None:
                        g.link(ev(idx, ctrl), n, _DATA)
                elif c.type != "variable_name":
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in _CONST:
            return g.add("CONST")
        if t in ("string", "raw_string", "translated_string", "ansi_c_string"):
            n = g.add("CONST")
            for c in node.named_children:
                if c.type not in ("string_content",):
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "concatenation":
            n = g.add("CONCAT")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("command_substitution", "process_substitution"):
            inner = [c for c in node.named_children]
            last = None
            for c in inner:
                last = ev(c, ctrl) if c.type not in _STMT_TYPES else _do(c, ctrl)
            return last
        if t in ("arithmetic_expansion", "arithmetic_expression"):
            n = g.add("ARITH")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("unary_expression", "postfix_expression", "parenthesized_expression"):
            n = g.add("UNARY")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "command":
            n = g.add("CALL")
            nm = node.child_by_field_name("name")
            if nm is not None:
                g.link(ev(nm, ctrl), n, _DATA)
            for i, c in enumerate(node.named_children):
                fld = node.field_name_for_named_child(i)
                if fld == "name":
                    continue
                if c.type == "variable_assignment":  # `FOO=bar cmd` prefix assignment
                    _do(c, ctrl)
                else:
                    g.link(ev(c, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "command_name":
            # A literal command name (`echo`, `helper`) is a free callee. A *dynamic* name —
            # `$(…)`/`${…}`/concatenation — carries flow that determines the callee; walk it so a
            # command-substitution CALL in callee position is never dropped.
            parts = node.named_children
            if len(parts) == 1 and parts[0].type in ("word", "number"):
                return freevar(text(node))
            cn = g.add("CALLEE")
            for c in parts:
                g.link(ev(c, ctrl), cn, _DATA)
            return cn
        if t == "test_command":
            n = g.add("TEST")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("array",):
            n = g.add("COMPOSITE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in _STMT_TYPES:
            return _do(node, ctrl)
        if t in _FUNC_NODES:
            return g.add("NESTED")
        # generic fallback — nothing silently vanishes (the completeness oracle makes gaps visible).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def bind(target, val: int | None) -> None:
        if target is None:
            return
        if target.type in ("variable_name", "word"):
            name = text(target)
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)

    def _do(node, ctrl: int | None) -> int | None:
        t = node.type
        if t == "variable_assignment":
            val = ev(node.child_by_field_name("value"), ctrl)
            target = node.child_by_field_name("name")
            if target is not None and target.type == "subscript":
                # `arr[$(helper)]=x` — the index expression on the LHS carries flow (and there's no
                # simple name to copy-propagate into, so we only walk it, not bind).
                idx = target.child_by_field_name("index")
                if idx is not None:
                    ev(idx, ctrl)
            bind(target, val)
            return val
        if t == "declaration_command":  # local/declare/export/readonly/typeset …
            last = None
            for c in node.named_children:
                if c.type == "variable_assignment":
                    last = _do(c, ctrl)
                elif c.type in ("variable_name", "word"):
                    pass  # a bare `local x` declares without a value
                else:
                    last = ev(c, ctrl)
            return last
        if t == "command":
            return ev(node, ctrl)
        if t in ("pipeline", "list"):
            last = None
            for c in node.named_children:
                last = _do(c, ctrl) if c.type in _STMT_TYPES else ev(c, ctrl)
            return last
        if t in ("compound_statement", "subshell", "do_group"):
            _do_body(node, ctrl)
            return None
        if t == "if_statement":
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("condition"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            for c in node.named_children:
                fld = node.field_name_for_named_child(node.named_children.index(c))
                if fld == "condition":
                    continue
                if c.type in ("elif_clause", "else_clause"):
                    cond = c.child_by_field_name("condition")
                    if cond is not None:
                        ev(cond, b)
                    _do_body(c, b)
                else:
                    _do(c, b)
            return b
        if t == "for_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            it = g.add("ITERVAR")
            for i, c in enumerate(node.named_children):
                fld = node.field_name_for_named_child(i)
                if fld == "value":
                    g.link(ev(c, loop), it, _DATA)
            bind(node.child_by_field_name("variable"), it)
            _do_body(node.child_by_field_name("body"), loop)
            return loop
        if t == "c_style_for_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            for c in node.named_children:
                if c.type != "do_group" and c.type != "compound_statement":
                    ev(c, loop)
            _do_body(node.child_by_field_name("body"), loop)
            return loop
        if t in ("while_statement", "until_statement"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            cond = node.child_by_field_name("condition")
            if cond is not None:
                _do(cond, loop) if cond.type in _STMT_TYPES else ev(cond, loop)
            _do_body(node.child_by_field_name("body"), loop)
            return loop
        if t == "case_statement":
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("value"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            for item in node.named_children:
                if item.type == "case_item":
                    for c in item.named_children:
                        if c.type in _STMT_TYPES or c.type in ("command", "variable_assignment",
                                                               "pipeline", "list"):
                            _do(c, b)
                        else:
                            ev(c, b)  # the pattern(s)
            return b
        if t in ("redirected_statement", "negated_command", "subshell", "compound_statement"):
            for c in node.named_children:
                if c.type in _STMT_TYPES or c.type in ("command", "pipeline", "list",
                                                       "variable_assignment"):
                    _do(c, ctrl)
                else:
                    ev(c, ctrl)
            return None
        return ev(node, ctrl)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type in ("compound_statement", "do_group", "subshell"):
            for st in node.named_children:
                _do(st, ctrl)
        else:
            _do(node, ctrl)

    body = fn.child_by_field_name("body")
    if body is not None:
        _do_body(body, None)
    return g


_STMT_TYPES = frozenset({
    "variable_assignment", "declaration_command", "command", "pipeline", "list",
    "compound_statement", "subshell", "do_group", "if_statement", "for_statement",
    "c_style_for_statement", "while_statement", "until_statement", "case_statement",
    "redirected_statement", "negated_command",
})


def _op_text(node, text) -> str:
    op = node.child_by_field_name("operator")
    if op is not None:
        return op.text.decode("utf-8", "replace")
    for c in node.children:
        if not c.is_named and c.text:
            return c.text.decode("utf-8", "replace")
    return "?"
