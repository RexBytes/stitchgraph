"""Structural (body-level) fingerprints for PHP functions and methods.

The PHP frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So PHP↔PHP bodies compare exactly the way every
other per-language frontend's do (one shared WL kernel).

PHP specifics: functions/methods are keyed by the dotted chain of enclosing class/trait/interface/enum
names (the `namespace` is NOT part of the key) — `Calc.compute`, bare `free_fn` for a top-level
function — matching the extractor. Statement-oriented (explicit `return`). A call's arguments are
wrapped in `argument` nodes (unwrapped here). Assignment is an expression; compound `+=`/`.=` normalises
to the base operator + a rebind. `$"…{$x}…"` (`encapsed_string`) carries value flow through its
interpolations. `cast_expression`/`parenthesized_expression` are transparent to their operand. Closures
(`anonymous_function`, `arrow_function`) are opaque `NESTED` leaves. It is a structural approximation,
NOT sound data flow (no alias analysis, constants collapsed). The method is in
`docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _wl_features

_EXTS = {".php": "php"}

_FUNC_NODES = frozenset({"function_definition", "method_declaration", "anonymous_function",
                         "arrow_function", "anonymous_function_creation_expression"})

_TYPE_NODES = frozenset({"class_declaration", "interface_declaration", "trait_declaration",
                         "enum_declaration"})

_CONST = frozenset({
    "integer", "float", "string", "boolean", "null", "nowdoc", "shell_command_expression",
})


def _parser():
    """A tree-sitter PHP parser, or None if the extra isn't installed."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar("php"))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def fingerprint_source(source: str, lang: str = "php") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a PHP source string, keyed by the extractor's scheme
    (`Calc.compute`, bare `free_fn`; the namespace is not part of the key). Returns {} on a parse
    failure, a missing tree-sitter extra, or a too-deep tree (advisory, never raises)."""
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

    def visit(node, prefix: str) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("function_definition", "method_declaration"):
                nm = child.child_by_field_name("name")
                if nm is not None:
                    emit(prefix + text(nm), child)
            elif t in _TYPE_NODES:
                nm = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                if body is not None and nm is not None:
                    visit(body, prefix + text(nm) + ".")
                elif body is not None:
                    visit(body, prefix)
            elif t == "namespace_definition":
                body = child.child_by_field_name("body")
                visit(body if body is not None else child, prefix)  # namespace adds no segment
            else:
                visit(child, prefix)

    try:
        visit(tree.root_node, "")
    except (RecursionError, ValueError):
        return out
    return out


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one PHP function/method/closure node into a value-flow graph, mirroring
    `structure._build_vfg`: PARAM seeds, copy propagation, operations + control points, data/control
    edges. Statement-oriented (explicit returns)."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            nm = p.child_by_field_name("name")
            if nm is not None:
                env[text(nm)] = g.add("PARAM")

    def bind(target, val: int | None) -> None:
        if target is None:
            return
        t = target.type
        if t in ("variable_name", "name"):
            name = text(target)
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)
        elif t == "member_access_expression":
            n = g.add("SETATTR")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("object"), None), n, _DATA)
        elif t == "subscript_expression":
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            for c in target.named_children:
                g.link(ev(c, None), n, _DATA)
        elif t == "list_literal":  # `[$a, $b] = ...` destructuring
            for c in target.named_children:
                bind(c, val)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t == "comment":  # trivia: a comment must never alter the value-flow fingerprint
            return None
        if t == "argument":
            inner = node.named_children
            return ev(inner[-1], ctrl) if inner else None
        if t in ("variable_name", "name"):
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t in ("nowdoc",):
            return g.add("CONST")
        if t in _CONST:
            return g.add("CONST")
        if t in ("encapsed_string", "string"):
            # interpolations / embedded variables carry value flow; the literal text collapses.
            n = g.add("CONST")
            for c in node.named_children:
                if c.type not in ("string_content", "escape_sequence"):
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "heredoc":
            # A heredoc INTERPOLATES (unlike nowdoc, which stays a CONST): walk its body's holes
            # (`$var`, `{$o->m()}`, …) for flow; literal `string_content` collapses.
            n = g.add("CONST")
            for c in node.named_children:
                if c.type == "heredoc_body":
                    for cc in c.named_children:
                        if cc.type not in ("string_content", "escape_sequence"):
                            g.link(ev(cc, ctrl), n, _DATA)
            return n
        if t == "parenthesized_expression":
            inner = node.named_children
            return ev(inner[-1], ctrl) if inner else None
        if t == "member_access_expression" or t == "nullsafe_member_access_expression":
            n = g.add("ATTR")
            g.link(ev(node.child_by_field_name("object"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("scoped_property_access_expression", "class_constant_access_expression"):
            return freevar(text(node))
        if t == "subscript_expression":
            n = g.add("SUBSCRIPT")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("function_call_expression", "method_call_expression",
                 "nullsafe_method_call_expression", "scoped_call_expression"):
            n = g.add("CALL")
            g.link(ev(node.child_by_field_name("object"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("function"), ctrl), n, _DATA)
            args = node.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "object_creation_expression":
            n = g.add("CALL")
            for c in node.named_children:
                if c.type == "arguments":
                    for a in c.named_children:
                        g.link(ev(a, ctrl), n, _DATA)
                elif c.type == "anonymous_class":
                    # `new class($arg) {…}` — the constructor args live *inside* the anonymous_class
                    # node (its class body is a definition, opaque to this function's flow).
                    for cc in c.named_children:
                        if cc.type == "arguments":
                            for a in cc.named_children:
                                g.link(ev(a, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("array_creation_expression", "list_literal"):
            n = g.add("COMPOSITE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "array_element_initializer":
            n = g.add("PAIR")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("unary_op_expression", "error_suppression_expression", "clone_expression",
                 "print_intrinsic", "throw_expression", "yield_expression", "match_condition_list"):
            n = g.add("UNARY")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "update_expression":
            arg = node.named_children[0] if node.named_children else None
            n = g.add("UNARY")
            g.link(ev(arg, ctrl), n, _DATA)
            bind(arg, n)
            return n
        if t == "assignment_expression":
            val = ev(node.child_by_field_name("right"), ctrl)
            bind(node.child_by_field_name("left"), val)
            return val
        if t == "augmented_assignment_expression":
            op = _op_text(node, text)
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            n = g.add("BINOP:" + (op[:-1] if op.endswith("=") else op))
            g.link(ev(left, ctrl), n, _DATA)
            g.link(ev(right, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            bind(left, n)
            return n
        if t == "conditional_expression":
            n = g.add("IFEXP")
            for fld in ("condition", "body", "alternative"):
                g.link(ev(node.child_by_field_name(fld), ctrl), n, _DATA)
            return n
        if t == "cast_expression":
            return ev(node.child_by_field_name("value")
                      or (node.named_children[-1] if node.named_children else None), ctrl)
        if t == "match_expression":
            n = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("condition"), ctrl), n, _DATA)
            body = node.child_by_field_name("body")
            for arm in (body.named_children if body is not None else []):
                for c in arm.named_children:
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in _FUNC_NODES:
            return g.add("NESTED")
        # generic fallback: a node fed by its sub-expressions (the completeness oracle makes gaps
        # visible, so an unhandled construct can never silently vanish from the fingerprint).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def _strip(node):
        while node is not None and node.type == "parenthesized_expression" and node.named_children:
            node = node.named_children[-1]
        return node

    def do(node, ctrl: int | None) -> None:
        t = node.type
        if t == "comment":  # trivia
            return
        if t == "expression_statement":
            for c in node.named_children:
                ev(c, ctrl)
        elif t in ("return_statement", "echo_statement", "throw_statement", "print_intrinsic",
                   "unset_statement"):
            n = g.add("RETURN" if t == "return_statement" else "EXPR")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
        elif t == "if_statement":
            b = g.add("BRANCH")
            g.link(ev(_strip(node.child_by_field_name("condition")), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            _do_body(node.child_by_field_name("body"), b)
            for c in node.named_children:
                if c.type in ("else_if_clause", "else_clause"):
                    cond = c.child_by_field_name("condition")
                    if cond is not None:
                        ev(_strip(cond), b)
                    _do_body(c.child_by_field_name("body"), b)
        elif t == "for_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            for fld in ("initialize", "condition", "update"):
                sub = node.child_by_field_name(fld)
                if sub is not None:
                    ev(sub, loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "foreach_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            body = node.child_by_field_name("body")
            bspan = (body.start_byte, body.end_byte) if body is not None else None
            seen_collection = False
            it = g.add("ITERVAR")
            for c in node.named_children:
                if bspan is not None and (c.start_byte, c.end_byte) == bspan:
                    continue
                if not seen_collection:
                    g.link(ev(c, loop), it, _DATA)  # the collection being iterated
                    seen_collection = True
                else:
                    bind(c, it)  # the loop variable(s) / pair
            _do_body(body, loop)
        elif t in ("while_statement", "do_statement"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            cond = node.child_by_field_name("condition")
            if cond is not None:
                ev(_strip(cond), loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "switch_statement":
            b = g.add("BRANCH")
            g.link(ev(_strip(node.child_by_field_name("condition")), ctrl), b, _DATA)
            body = node.child_by_field_name("body")
            for sec in (body.named_children if body is not None else []):
                if sec.type in ("case_statement", "default_statement"):
                    val = sec.child_by_field_name("value")
                    g.link(ev(val, b), b, _DATA)
                    vspan = (val.start_byte, val.end_byte) if val is not None else None
                    for st in sec.named_children:
                        if (st.start_byte, st.end_byte) != vspan:
                            do(st, b)
        elif t == "try_statement":
            _do_body(node.child_by_field_name("body"), ctrl)
            for c in node.named_children:
                if c.type == "catch_clause":
                    _do_body(c.child_by_field_name("body"), ctrl)
                elif c.type == "finally_clause":
                    _do_body(c.child_by_field_name("body") or c, ctrl)
        elif t == "compound_statement":
            _do_body(node, ctrl)
        elif t in ("break_statement", "continue_statement", "global_declaration",
                   "function_static_declaration"):
            pass
        elif t.endswith("statement"):
            for c in node.named_children:
                if c.type == "compound_statement" or c.type.endswith("statement"):
                    do(c, ctrl)
                else:
                    ev(c, ctrl)
        else:
            ev(node, ctrl)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type == "compound_statement":
            for st in node.named_children:
                do(st, ctrl)
        else:
            do(node, ctrl)

    # A parameter's default-value expression carries flow (`function f($b = helper())`): walk it now
    # that `ev` is defined and link it into the parameter's PARAM node.
    if params is not None:
        for p in params.named_children:
            val = p.child_by_field_name("default_value")
            nm = p.child_by_field_name("name")
            if val is not None and nm is not None and text(nm) in env:
                g.link(ev(val, None), env[text(nm)], _DATA)

    body = fn.child_by_field_name("body")
    if body is not None:
        _do_body(body, None)
    return g


def _op_text(node, text) -> str:
    op = node.child_by_field_name("operator")
    if op is not None:
        return op.text.decode("utf-8", "replace")
    for c in node.children:
        if not c.is_named and c.text:
            return c.text.decode("utf-8", "replace")
    return "?"
