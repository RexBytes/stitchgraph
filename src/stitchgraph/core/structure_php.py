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

from .structure import _CTRL, _DATA, _VFG, _serialize_vfg, _wl_features
from .structure_common import (
    first,
    last,
    make_parser,
    nc,
    node_text,
    op_text,
    pdg_state,
    vfg_state,
)

_EXTS = {".php": "php"}

_FUNC_NODES = frozenset({"function_definition", "method_declaration", "anonymous_function",
                         "arrow_function", "anonymous_function_creation_expression"})

_TYPE_NODES = frozenset({"class_declaration", "interface_declaration", "trait_declaration",
                         "enum_declaration"})

_CONST = frozenset({
    "integer", "float", "string", "boolean", "null", "nowdoc", "shell_command_expression",
})


def _parser():
    return make_parser("php")


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _walk(source: str, lang: str, build) -> dict:
    """Shared traversal for `fingerprint_source` / `vfg_source`: apply `build(fn_node, data)` per key."""
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
            out[name] = build(fn_node, data)
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


def fingerprint_source(source: str, lang: str = "php") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a PHP source string, keyed by the extractor's scheme
    (`Calc.compute`, bare `free_fn`; the namespace is not part of the key). Returns {} on a parse
    failure, a missing tree-sitter extra, or a too-deep tree (advisory, never raises)."""
    return _walk(source, lang, lambda fn, data: _wl_features(_build_vfg(fn, data)))


def vfg_source(source: str, lang: str = "php") -> dict[str, tuple[list[str], list]]:
    """Value-flow graph of every function/method — EXPRESSION-layer companion to fingerprint_source
    (identical keys). Advisory, on demand."""
    return _walk(source, lang, lambda fn, data: _serialize_vfg(_build_vfg(fn, data)))


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one PHP function/method/closure node into a value-flow graph, mirroring
    `structure._build_vfg`: PARAM seeds, copy propagation, operations + control points, data/control
    edges. Statement-oriented (explicit returns)."""
    g, env, free, freevar = vfg_state()
    text = node_text

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
        elif t == "pair":  # `foreach ($m as $k => $v)`: bind BOTH key and value — the pair
            for c in target.named_children:   # previously bound nothing, so $k/$v read FREE in
                bind(c, val)                  # the whole body (review 2026-07-03, F5d)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t == "comment":  # trivia: a comment must never alter the value-flow fingerprint
            return None
        if t == "argument":
            return ev(_last(node), ctrl)
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
            return ev(_last(node), ctrl)
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
            arg = _first(node)
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
            return ev(node.child_by_field_name("value") or _last(node), ctrl)
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
        while node is not None and node.type == "parenthesized_expression" and _nc(node):
            node = _last(node)
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
                if c.type == "comment":  # trivia: never the collection or a loop variable
                    continue
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
        elif t in ("break_statement", "continue_statement", "global_declaration"):
            pass
        elif t == "function_static_declaration":
            # `static $x = <expr>;` — unlike break/continue/global this carries an initializer that
            # is evaluated (once) in the function body, so its value flow must be walked (bind the
            # name to the init value), parallel to a plain assignment.
            for c in node.named_children:
                if c.type == "static_variable_declaration":
                    val = ev(c.child_by_field_name("value"), ctrl)
                    bind(c.child_by_field_name("name"), val)
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


def _nc(node):
    return nc(node)


def _first(node):
    return first(node)


def _last(node):
    return last(node)


def _op_text(node, text) -> str:
    return op_text(node)


# --- STATEMENT layer (PDG) — design §5c sweep, PHP -----------------------------------------------

_PDG_STMT_LABEL = {
    "expression_statement": "Expr", "return_statement": "Return", "echo_statement": "Echo",
    "print_intrinsic": "Expr", "unset_statement": "Expr", "throw_statement": "Throw",
    "if_statement": "If", "for_statement": "For", "foreach_statement": "ForEach",
    "while_statement": "While", "do_statement": "Do", "switch_statement": "Switch",
    "try_statement": "Try", "compound_statement": "Block", "break_statement": "Break",
    "continue_statement": "Continue", "global_declaration": "Global",
    "function_static_declaration": "Static", "const_declaration": "Const",
    "namespace_use_declaration": "Use",
}


def _pdg_label(t: str) -> str:
    return _PDG_STMT_LABEL.get(t) or "".join(w.capitalize() for w in t.split("_")) or "Stmt"


def pdg_source(source: str, lang: str = "php") -> dict[str, tuple[list[str], list]]:
    """Program-dependence graph of every function/method — the STATEMENT-layer companion to
    fingerprint_source/vfg_source (identical keys), the raw graph get_matrix(layer="statement")
    drills into. Statement nodes + control ('C') / data ('D') dependence edges via a sequential
    reaching-def approximation; nested functions/closures/arrow functions are opaque NESTED leaves.
    Advisory, on demand.

    Accepted layer-level under-approximations (cross-language consistent, mirror the Python
    reference and the sibling PDGs), all *symmetric* — shared by BOTH the PDG and the VFG so they
    create no VFG/PDG divergence: closure/arrow-function bodies are opaque (a param used only inside
    one is read by neither builder); `Foo::$x` / `Foo::CONST` are opaque freevars in both. (The
    `foreach` key/value `pair` binds both names in both builders since review 2026-07-03, F5d.)"""
    return _walk(source, lang, build=lambda fn, data: _build_pdg(fn, data))


def _build_pdg(fn, data: bytes) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer for a PHP function/method/closure — a program-dependence graph mirroring
    `structure._build_pdg` (Python) and the JS-family/Go/Rust/C++/Java/C#/Ruby PDG builders:
    statement nodes + a synthetic ENTRY carrying the parameters, control ('C') / data ('D',
    sequential reaching-def) edges. PHP is statement-oriented (like Go, C/C++, Java, C#). Nested
    functions/closures/arrow functions are opaque NESTED leaves; reorder-invariant. A structural
    approximation (no SSA/alias analysis), advisory only — never feeds liveness. Its read/write
    projection (`collect`/`bind_place`) reads ONLY genuine value operands and records ONLY genuine
    bindings, matching the VFG's `ev`/`bind` node-for-node: a member/property NAME, a call's method
    NAME and a `Foo::$x` scoped access are never read/bound as values here."""
    nodes, edges, last_def, new_id, data_from = pdg_state()
    text = node_text

    entry = new_id("ENTRY")
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            nm = p.child_by_field_name("name")
            if nm is not None:
                last_def[text(nm)] = entry

    def bind_place(target, loads: set, stores: set) -> None:
        """An assignment target, mirroring the VFG's `bind`: a plain `$x` defines a name (a STORE);
        a member/subscript place defines no name — its object/index operands are READS; `[$a,$b]`
        destructures; a `pair` (foreach key => value) binds BOTH names, mirroring the VFG's `pair`
        case (review 2026-07-03, F5d) so the two builders stay in lock-step."""
        if target is None:
            return
        t = target.type
        if t in ("variable_name", "name"):
            stores.add(text(target))
        elif t == "member_access_expression":
            collect(target.child_by_field_name("object"), loads, stores)
        elif t == "subscript_expression":
            for c in target.named_children:
                collect(c, loads, stores)
        elif t == "list_literal":
            for c in target.named_children:
                bind_place(c, loads, stores)
        elif t == "pair":  # foreach ($m as $k => $v): both names bind (F5d)
            for c in target.named_children:
                bind_place(c, loads, stores)

    def rmw_target(target, loads: set, stores: set) -> None:
        """A read-modify-write target (`$x += e`, `$x++`): a plain `$x` both READS and WRITES the
        name; a member/subscript place reads its operands (no name defined). Mirrors the VFG, which
        both `ev`-reads and `bind`-writes the left operand of an augmented assignment / update."""
        if target is None:
            return
        t = target.type
        if t in ("variable_name", "name"):
            loads.add(text(target))
            stores.add(text(target))
        elif t == "member_access_expression":
            collect(target.child_by_field_name("object"), loads, stores)
        elif t == "subscript_expression":
            for c in target.named_children:
                collect(c, loads, stores)
        elif t == "list_literal":
            for c in target.named_children:
                rmw_target(c, loads, stores)
        else:
            collect(target, loads, stores)

    def collect(n, loads: set, stores: set) -> None:
        """Reads/writes within one statement's header, mirroring the VFG's `ev` node-for-node — stops
        at nested closures (opaque NESTED, like the VFG's NESTED leaf) and compound statements (their
        own nodes). Never reads a `_CONST` literal, a member/property NAME, a call's method NAME, a
        `Foo::$x` / `Foo::CONST` scoped access (an opaque freevar in the VFG), or a cast type."""
        if n is None:
            return
        t = n.type
        if t == "comment":
            return
        if t in _FUNC_NODES or t == "compound_statement":
            return  # a closure body is opaque; a nested block is its own node
        if t == "argument":
            collect(_last(n), loads, stores)
            return
        if t in ("variable_name", "name"):
            loads.add(text(n))
            return
        if t in _CONST:  # `nowdoc` / `string` (single-quoted) / numbers / bool / null carry no read
            return
        if t == "encapsed_string":  # interpolations carry flow; literal text collapses
            for c in n.named_children:
                if c.type not in ("string_content", "escape_sequence"):
                    collect(c, loads, stores)
            return
        if t == "heredoc":  # a heredoc INTERPOLATES (unlike the nowdoc CONST) — walk its holes
            for c in n.named_children:
                if c.type == "heredoc_body":
                    for cc in c.named_children:
                        if cc.type not in ("string_content", "escape_sequence"):
                            collect(cc, loads, stores)
            return
        if t == "parenthesized_expression":
            collect(_last(n), loads, stores)
            return
        if t in ("member_access_expression", "nullsafe_member_access_expression"):
            collect(n.child_by_field_name("object"), loads, stores)  # the member NAME is not a value
            return
        if t in ("scoped_property_access_expression", "class_constant_access_expression"):
            return  # a freevar in the VFG — never a parameter read
        if t == "subscript_expression":
            for c in n.named_children:
                collect(c, loads, stores)
            return
        if t in ("function_call_expression", "method_call_expression",
                 "nullsafe_method_call_expression", "scoped_call_expression"):
            collect(n.child_by_field_name("object"), loads, stores)
            collect(n.child_by_field_name("function"), loads, stores)
            args = n.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    collect(a, loads, stores)
            return
        if t == "object_creation_expression":  # `new T(args)` — the type is not read; args are
            for c in n.named_children:
                if c.type == "arguments":
                    for a in c.named_children:
                        collect(a, loads, stores)
                elif c.type == "anonymous_class":  # `new class($arg){…}` — args live inside; body opaque
                    for cc in c.named_children:
                        if cc.type == "arguments":
                            for a in cc.named_children:
                                collect(a, loads, stores)
            return
        if t in ("array_creation_expression", "list_literal"):
            for c in n.named_children:
                collect(c, loads, stores)
            return
        if t == "array_element_initializer":
            for c in n.named_children:
                collect(c, loads, stores)
            return
        if t == "binary_expression":
            collect(n.child_by_field_name("left"), loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t in ("unary_op_expression", "error_suppression_expression", "clone_expression",
                 "print_intrinsic", "throw_expression", "yield_expression", "match_condition_list"):
            for c in n.named_children:
                collect(c, loads, stores)
            return
        if t == "update_expression":  # `$x++` reads and writes its operand
            rmw_target(_first(n), loads, stores)
            return
        if t == "assignment_expression":
            bind_place(n.child_by_field_name("left"), loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t == "augmented_assignment_expression":  # `$x .= e` reads AND writes the left operand
            rmw_target(n.child_by_field_name("left"), loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t == "conditional_expression":
            for fld in ("condition", "body", "alternative"):
                collect(n.child_by_field_name(fld), loads, stores)
            return
        if t == "cast_expression":  # `(int) $x` — the cast type carries no read; the value does
            collect(n.child_by_field_name("value") or _last(n), loads, stores)
            return
        if t == "match_expression":  # value-position branch: scrutinee + every arm read fold in
            collect(n.child_by_field_name("condition"), loads, stores)
            body = n.child_by_field_name("body")
            for arm in (body.named_children if body is not None else []):
                for c in arm.named_children:
                    collect(c, loads, stores)
            return
        for c in n.named_children:  # generic fallback — parity with the VFG's `ev` fallback
            collect(c, loads, stores)

    def _strip(node):
        while node is not None and node.type == "parenthesized_expression" and _nc(node):
            node = _last(node)
        return node

    def data_edges(hdr, sid: int) -> None:
        if hdr is None:
            return
        loads: set = set()
        stores: set = set()
        collect(hdr, loads, stores)
        data_from(loads, stores, sid)

    def block(blk, parent: int) -> None:
        if blk is None or blk.type == "comment":
            return
        if blk.type == "compound_statement":
            for st in blk.named_children:
                block(st, parent)
        else:
            process(blk, parent)

    def process(node, parent: int) -> None:
        t = node.type
        if t == "comment":
            return
        if t in _FUNC_NODES:
            edges.append((parent, new_id("NESTED"), "C"))
            return
        sid = new_id(_pdg_label(t))
        edges.append((parent, sid, "C"))
        if t == "if_statement":
            data_edges(_strip(node.child_by_field_name("condition")), sid)
            block(node.child_by_field_name("body"), sid)
            for c in node.named_children:
                if c.type == "else_if_clause":
                    data_edges(_strip(c.child_by_field_name("condition")), sid)
                    block(c.child_by_field_name("body"), sid)
                elif c.type == "else_clause":
                    block(c.child_by_field_name("body"), sid)
        elif t == "for_statement":
            for fld in ("initialize", "condition", "update"):
                data_edges(node.child_by_field_name(fld), sid)
            block(node.child_by_field_name("body"), sid)
        elif t == "foreach_statement":
            body = node.child_by_field_name("body")
            bspan = (body.start_byte, body.end_byte) if body is not None else None
            loads: set = set()
            stores: set = set()
            seen_collection = False
            for c in node.named_children:
                if c.type == "comment":
                    continue
                if bspan is not None and (c.start_byte, c.end_byte) == bspan:
                    continue
                if not seen_collection:
                    collect(c, loads, stores)  # the collection being iterated
                    seen_collection = True
                else:
                    bind_place(c, loads, stores)  # the loop variable(s); a `pair` binds both (F5d)
            data_from(loads, stores, sid)
            block(body, sid)
        elif t in ("while_statement", "do_statement"):
            data_edges(_strip(node.child_by_field_name("condition")), sid)
            block(node.child_by_field_name("body"), sid)
        elif t == "switch_statement":
            data_edges(_strip(node.child_by_field_name("condition")), sid)  # scrutinee
            body = node.child_by_field_name("body")
            for sec in (body.named_children if body is not None else []):
                if sec.type in ("case_statement", "default_statement"):
                    val = sec.child_by_field_name("value")
                    data_edges(val, sid)  # a case selector reads on the header
                    vspan = (val.start_byte, val.end_byte) if val is not None else None
                    for st in sec.named_children:
                        if (st.start_byte, st.end_byte) != vspan:
                            block(st, sid)
        elif t == "try_statement":
            block(node.child_by_field_name("body"), sid)
            for c in node.named_children:
                if c.type == "catch_clause":
                    block(c.child_by_field_name("body"), sid)
                elif c.type == "finally_clause":
                    block(c.child_by_field_name("body") or c, sid)
        elif t == "compound_statement":
            for c in node.named_children:
                block(c, sid)
        elif t in ("break_statement", "continue_statement", "global_declaration"):
            pass  # a break/continue level is a control target; `global` binds no value flow
        elif t == "function_static_declaration":
            # `static $x = <expr>;` — the initializer is evaluated once; walk it (bind the name to
            # its value), parallel to a plain assignment (mirrors the VFG).
            loads2: set = set()
            stores2: set = set()
            for c in node.named_children:
                if c.type == "static_variable_declaration":
                    collect(c.child_by_field_name("value"), loads2, stores2)
                    nm = c.child_by_field_name("name")
                    if nm is not None:
                        stores2.add(text(nm))
            data_from(loads2, stores2, sid)
        else:
            # a simple statement (expression / return / echo / throw / unset / …): the whole node is
            # its header. collect stops at nested closures/compound statements, so nothing leaks. Any
            # nested block child is still descended (parity with the sibling PDGs).
            data_edges(node, sid)
            for c in node.named_children:
                if c.type == "compound_statement":
                    block(c, sid)

    body = fn.child_by_field_name("body")
    block(body, entry)
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges
