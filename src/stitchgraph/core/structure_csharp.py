"""Structural (body-level) fingerprints for C# methods, constructors, and local functions.

The C# frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So C#↔C# bodies compare exactly the way every other per-language frontend's do (one shared WL kernel).

C# specifics: methods/constructors/**local functions** are keyed by the dotted chain of enclosing TYPE
names (a `namespace` does NOT contribute to the key, matching the extractor) — `Calc.Compute`,
constructor `Calc.Calc`, local function `Calc.Local.Inner`. C# is statement-oriented (explicit
`return`). A call's arguments are wrapped in `argument` nodes (unwrapped here); element access keeps its
index under a `bracketed_argument_list`. Assignment is an expression; compound `+=`/`<<=` normalises to
the base operator + a rebind. `cast_expression`/`parenthesized_expression` are transparent to their
operand; the cast *type* carries no flow. Lambdas / anonymous methods are opaque `NESTED` leaves;
`switch` (statement or expression) walks each arm. Only what the extractor keys is fingerprinted
(properties, operators, destructors are not method nodes there, so they are not keyed here). It is a
structural approximation, NOT sound data flow (no alias analysis, constants collapsed). The method is
in `docs/BODY_MATRIX_LESSONS.md`.
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
    parse_tree,
    pdg_state,
    vfg_state,
)

_EXTS = {".cs": "csharp"}

# Function-like nodes whose bodies are fingerprinted. A lambda / anonymous method, when nested, is an
# opaque leaf; a local function is ALSO keyed separately (matching the extractor).
_FUNC_NODES = frozenset({"method_declaration", "constructor_declaration",
                         "local_function_statement", "lambda_expression",
                         "anonymous_method_expression"})

# Type declarations that contribute a `Name.` segment to the qualname and whose bodies are walked.
_TYPE_NODES = frozenset({"class_declaration", "struct_declaration", "interface_declaration",
                         "record_declaration", "record_struct_declaration", "enum_declaration"})

# Namespace wrappers — recursed into WITHOUT contributing to the qualname (the extractor drops them).
_NS_NODES = frozenset({"namespace_declaration", "file_scoped_namespace_declaration"})

# Leaf literals — one CONST node regardless of value. (An interpolated string is NOT here: its
# `{...}` holes carry value flow and are walked explicitly, like a JS template literal.)
_CONST = frozenset({
    "integer_literal", "real_literal", "string_literal", "verbatim_string_literal",
    "character_literal", "boolean_literal", "null_literal", "raw_string_literal",
})


def _parser():
    return make_parser("csharp")


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _walk(source: str, lang: str = "csharp", *, build) -> dict:
    """Shared traversal for `fingerprint_source` / `vfg_source`: apply `build(fn_node, data)` per keyed method."""
    parsed = parse_tree(_parser(), source)
    if parsed is None:
        return {}
    tree, data = parsed
    out: dict[str, collections.Counter[str]] = {}

    text = node_text

    def emit(name: str, fn_node) -> None:
        if not name:
            return
        try:
            out[name] = build(fn_node, data)
        except RecursionError:
            pass

    def visit_body_locals(body, prefix: str) -> None:
        # Local functions are keyed `Enclosing.local` (the extractor keys them); descend a method/local
        # body looking for them, but do NOT cross into a nested function's own body here (that is
        # handled when we emit and recurse on that local function).
        if body is None:
            return
        for child in body.named_children:
            if child.type == "local_function_statement":
                nm = child.child_by_field_name("name")
                if nm is not None:
                    inner_prefix = prefix + text(nm)
                    emit(inner_prefix, child)
                    visit_body_locals(child.child_by_field_name("body"), inner_prefix + ".")
            elif child.type not in _FUNC_NODES and child.type not in _TYPE_NODES:
                visit_body_locals(child, prefix)

    def visit(node, prefix: str) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("method_declaration", "constructor_declaration"):
                nm = child.child_by_field_name("name")
                # C# has no free functions: only key a method/constructor inside a type (non-empty
                # `prefix`). A top-level method only appears in error-tolerant parses of non-C# source,
                # so refusing to key it keeps the find_similar sniff from grabbing other languages.
                if nm is not None and prefix:
                    name = prefix + text(nm)
                    emit(name, child)
                    visit_body_locals(child.child_by_field_name("body"), name + ".")
            elif t in _TYPE_NODES:
                nm = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                if body is not None and nm is not None:
                    visit(body, prefix + text(nm) + ".")
                elif body is not None:
                    visit(body, prefix)
            elif t in _NS_NODES:
                body = child.child_by_field_name("body")
                visit(body if body is not None else child, prefix)  # namespace adds no segment
            else:
                visit(child, prefix)

    try:
        visit(tree.root_node, "")
    except (RecursionError, ValueError):
        return out
    return out


def fingerprint_source(source: str, lang: str = "csharp") -> dict[str, collections.Counter[str]]:
    """Fingerprint every method/constructor/local-function in a C# source string, keyed by the
    extractor's scheme (`Calc.Compute`, constructor `Calc.Calc`, local function `Calc.Local.Inner`;
    the namespace is not part of the key). Returns {} on a parse failure, a missing tree-sitter extra,
    or a too-deep tree (advisory, never raises)."""
    return _walk(source, lang, build=lambda fn, data: _wl_features(_build_vfg(fn, data)))


def vfg_source(source: str, lang: str = "csharp") -> dict[str, tuple[list[str], list]]:
    """Value-flow graph of every function/method — EXPRESSION-layer companion to fingerprint_source
    (identical keys). Advisory, on demand."""
    return _walk(source, lang, build=lambda fn, data: _serialize_vfg(_build_vfg(fn, data)))


def _build_vfg(fn, _data: bytes) -> _VFG:
    """Symbolically evaluate one C# method/constructor/local-function/lambda node into a value-flow
    graph, mirroring `structure._build_vfg`: PARAM seeds, copy propagation through declarations,
    operations and control points as nodes, data/control edges. Statement-oriented (explicit returns)."""
    g, env, free, freevar = vfg_state()
    text = node_text

    # seed parameters from the parameter_list.
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            if p.type == "parameter":
                nm = p.child_by_field_name("name")
                if nm is not None:
                    env[text(nm)] = g.add("PARAM")

    def bind(target, val: int | None) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier":
            name = text(target)
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)
        elif t == "member_access_expression":
            n = g.add("SETATTR")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("expression"), None), n, _DATA)
        elif t == "element_access_expression":
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("expression"), None), n, _DATA)
            sub = target.child_by_field_name("subscript")
            if sub is not None:
                for a in sub.named_children:
                    g.link(ev(a, None), n, _DATA)
        elif t == "element_binding_expression":  # `new D { [key] = v }` indexed-initializer key
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            for a in target.named_children:  # the key expression(s) carry flow
                g.link(ev(a, None), n, _DATA)
        elif t == "tuple_expression":  # `(a, b) = ...` deconstruction
            for c in target.named_children:
                bind(c, val)
        elif t == "parenthesized_expression":
            bind(_last(target), val)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t == "comment":  # trivia: a comment must never alter the value-flow fingerprint
            return None
        if t == "argument":  # C# wraps each call/index argument in an `argument` node
            return ev(_last(node), ctrl)
        if t == "identifier":
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t in ("this_expression", "base_expression", "this", "base"):
            return freevar("this")
        if t in _CONST:
            return g.add("CONST")
        if t == "parenthesized_expression":
            return ev(_last(node), ctrl)
        if t == "member_access_expression":
            n = g.add("ATTR")
            g.link(ev(node.child_by_field_name("expression"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "element_access_expression":
            n = g.add("SUBSCRIPT")
            g.link(ev(node.child_by_field_name("expression"), ctrl), n, _DATA)
            sub = node.child_by_field_name("subscript")
            if sub is not None:
                for a in sub.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            return n
        if t == "invocation_expression":
            n = g.add("CALL")
            g.link(ev(node.child_by_field_name("function"), ctrl), n, _DATA)
            args = node.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "object_creation_expression":
            n = g.add("CALL")
            args = node.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            init = node.child_by_field_name("initializer")
            if init is not None:
                g.link(ev(init, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "array_creation_expression":
            n = g.add("CALL")
            typ = node.child_by_field_name("type")
            if typ is not None:
                for rank in typ.named_children:
                    if rank.type == "array_rank_specifier":
                        for d in rank.named_children:
                            g.link(ev(d, ctrl), n, _DATA)
            # the `{...}` element initializer is a positional `initializer_expression` child (no field
            # name), so scan children rather than `child_by_field_name("initializer")`.
            for c in node.named_children:
                if c.type == "initializer_expression":
                    g.link(ev(c, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("initializer_expression", "implicit_array_creation_expression"):
            n = g.add("COMPOSITE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "interpolated_string_expression":
            # An interpolated string `$"{e}"` carries value flow through its `{...}` holes (like a JS
            # template literal / f-string); a plain string with no holes collapses to CONST.
            n = g.add("CONST")
            for c in node.named_children:
                if c.type == "interpolation":
                    for ic in c.named_children:
                        if ic.type == "interpolation_alignment_clause":
                            # `$"{v,align}"` — the alignment after the `,` is a runtime expression
                            # (can hold a CALL); walk it. (The `:format` clause is literal text and
                            # the brace is punctuation — both stay excluded.)
                            for ac in ic.named_children:
                                g.link(ev(ac, ctrl), n, _DATA)
                        elif ic.type not in ("interpolation_brace", "interpolation_format_clause"):
                            g.link(ev(ic, ctrl), n, _DATA)
            return n
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("prefix_unary_expression", "postfix_unary_expression"):
            op = _op_text(node, text)
            operand = _first(node)
            n = g.add("UNARY:" + op)
            g.link(ev(operand, ctrl), n, _DATA)
            if op in ("++", "--"):  # rebinds
                bind(operand, n)
            return n
        if t == "assignment_expression":
            op = _op_text(node, text)
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if op and op != "=" and op.endswith("="):  # `+=`, `<<=`, … == `x = x op e`
                n = g.add("BINOP:" + op[:-1])
                g.link(ev(left, ctrl), n, _DATA)
                g.link(ev(right, ctrl), n, _DATA)
                g.link(ctrl, n, _CTRL)
                bind(left, n)
                return n
            val = ev(right, ctrl)
            bind(left, val)
            return val
        if t == "conditional_expression":
            n = g.add("IFEXP")
            g.link(ev(node.child_by_field_name("condition"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("consequence"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("alternative"), ctrl), n, _DATA)
            return n
        if t in ("cast_expression",):  # `(T) x` — value flows, type doesn't
            return ev(node.child_by_field_name("value"), ctrl)
        if t in ("is_pattern_expression", "is_expression"):
            n = g.add("BINOP:is")
            g.link(ev(node.child_by_field_name("expression"), ctrl)
                   or ev(_first(node), ctrl), n, _DATA)
            return n
        if t == "switch_expression":
            n = g.add("BRANCH")
            kids = _nc(node)
            if kids:
                g.link(ev(kids[0], ctrl), n, _DATA)  # the governing expression
            for arm in kids[1:]:
                if arm.type == "switch_expression_arm":
                    for c in arm.named_children:
                        g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("await_expression", "checked_expression", "ref_expression"):
            return ev(_last(node), ctrl)
        if t == "range_expression":
            n = g.add("RANGE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in _FUNC_NODES:  # lambda / anonymous method / (declarations handled in visit) — opaque
            return g.add("NESTED")
        # generic fallback: a node fed by its sub-expressions (the completeness oracle makes gaps
        # visible, so an unhandled construct can never silently vanish from the fingerprint).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def _strip_cond(node):
        while node is not None and node.type == "parenthesized_expression" and _nc(node):
            node = _last(node)
        return node

    def do(node, ctrl: int | None) -> None:
        t = node.type
        if t == "comment":  # trivia
            return
        if t == "local_declaration_statement":
            for decl in node.named_children:
                if decl.type == "variable_declaration":
                    _do_var_declaration(decl, ctrl)
        elif t == "expression_statement":
            for c in node.named_children:
                ev(c, ctrl)
        elif t == "return_statement":
            n = g.add("RETURN")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
        elif t in ("yield_statement", "throw_statement"):
            n = g.add("RETURN" if t == "yield_statement" else "RAISE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
        elif t == "if_statement":
            b = g.add("BRANCH")
            g.link(ev(_strip_cond(node.child_by_field_name("condition")), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            _do_body(node.child_by_field_name("consequence"), b)
            _do_body(node.child_by_field_name("alternative"), b)
        elif t == "for_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            # `initializer` and `update` are REPEATED field children for comma forms
            # (`i = 0, j = sink()` / `i++, j--`), so iterate every named child by field name —
            # `child_by_field_name` would return only the first and drop the rest (R197 opus).
            for i in range(node.named_child_count):
                sub = node.named_children[i]
                fld = node.field_name_for_named_child(i)
                if fld == "initializer":
                    if sub.type == "variable_declaration":
                        _do_var_declaration(sub, loop)
                    else:
                        ev(sub, loop)
                elif fld in ("condition", "update"):
                    ev(_strip_cond(sub), loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "foreach_statement":  # `foreach (var x in iterable)`
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            it = g.add("ITERVAR")
            g.link(ev(node.child_by_field_name("right"), loop), it, _DATA)
            bind(node.child_by_field_name("left"), it)
            _do_body(node.child_by_field_name("body"), loop)
        elif t in ("while_statement", "do_statement"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            cond = node.child_by_field_name("condition")
            if cond is not None:
                ev(_strip_cond(cond), loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "switch_statement":
            b = g.add("BRANCH")
            g.link(ev(_strip_cond(node.child_by_field_name("value")
                                   or node.child_by_field_name("condition")), ctrl), b, _DATA)
            body = node.child_by_field_name("body")
            for sec in (body.named_children if body is not None else node.named_children):
                if sec.type == "switch_section":
                    for st in sec.named_children:
                        if st.type.endswith("_label") or st.type == "case_pattern_switch_label":
                            for lbl in st.named_children:
                                ev(lbl, b)
                        else:
                            do(st, b)
        elif t == "using_statement":
            # `using (...) { ... }`: the grammar exposes only a `body` field; the resource (a
            # `variable_declaration` for `using (var r = e)` or a bare expression for `using (e)`) is a
            # positional, unnamed-field child — so scan children, skipping the body (R193 opus).
            body = node.child_by_field_name("body")
            bspan = (body.start_byte, body.end_byte) if body is not None else None
            for c in node.named_children:
                if bspan is not None and (c.start_byte, c.end_byte) == bspan:
                    continue
                if c.type == "variable_declaration":
                    _do_var_declaration(c, ctrl)
                else:
                    ev(c, ctrl)
            _do_body(body, ctrl)
        elif t == "try_statement":
            _do_body(node.child_by_field_name("body"), ctrl)
            for ch in node.named_children:
                if ch.type == "catch_clause":
                    # `catch (E e) when (predicate)` — the exception filter is an executed predicate
                    # carrying value flow; walk it before the catch body (R197 opus).
                    for cc in ch.named_children:
                        if cc.type == "catch_filter_clause":
                            for fc in cc.named_children:
                                ev(fc, ctrl)
                    _do_body(ch.child_by_field_name("body"), ctrl)
                elif ch.type == "finally_clause":
                    for c in ch.named_children:
                        if c.type == "block":
                            _do_body(c, ctrl)
        elif t in ("lock_statement", "checked_statement", "unsafe_statement", "fixed_statement",
                   "labeled_statement"):
            for c in node.named_children:
                if c.type == "block":
                    _do_body(c, ctrl)
                elif c.type.endswith("statement"):
                    do(c, ctrl)
                else:
                    ev(c, ctrl)
        elif t == "block":
            _do_body(node, ctrl)
        elif t in ("break_statement", "continue_statement", "goto_statement",
                   "local_function_statement", "empty_statement"):
            pass  # local functions are keyed separately and opaque here
        elif t.endswith("statement"):
            for ch in node.named_children:
                if ch.type == "block" or ch.type.endswith("statement"):
                    do(ch, ctrl)
                else:
                    ev(ch, ctrl)
        else:
            ev(node, ctrl)

    def _do_var_declaration(decl, ctrl: int | None) -> None:
        for d in decl.named_children:
            if d.type == "variable_declarator":
                nm = d.child_by_field_name("name")
                # The initializer is the declarator child that is NOT the `name` and NOT the
                # `bracketed_argument_list` (array-size/indexer syntax). Identifying the name by its
                # FIELD (not by node type) is essential: a bare-identifier initializer `int r = v;`
                # is itself an `identifier`, so a blanket identifier-skip would drop the copy (R-C#).
                nspan = (nm.start_byte, nm.end_byte) if nm is not None else None
                val = None
                for c in d.named_children:
                    if nspan is not None and (c.start_byte, c.end_byte) == nspan:
                        continue
                    if c.type == "equals_value_clause":
                        val = ev(_last(c), ctrl)
                    elif c.type != "bracketed_argument_list":
                        val = ev(c, ctrl)
                bind(nm if nm is not None else _first(d), val)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type == "block":
            for st in node.named_children:
                do(st, ctrl)
        else:
            do(node, ctrl)

    # A parameter's default-value expression carries flow (`int F(int b = helper())`): walk it now
    # that `ev` is defined and link it into the parameter's PARAM node.
    if params is not None:
        for p in params.named_children:
            if p.type != "parameter":
                continue
            pn = p.child_by_field_name("name")
            if pn is None or text(pn) not in env:
                continue
            for i, c in enumerate(p.named_children):
                fld = p.field_name_for_named_child(i)
                if fld in ("type", "name") or c.type == "attribute_list":
                    continue
                g.link(ev(c, None), env[text(pn)], _DATA)

    # A constructor initializer (`: this(args)` / `: base(args)`) runs before the body and its
    # arguments carry value flow. It is an unnamed sibling of `body`, so walk it explicitly (the C#
    # analogue of the C++ member-initializer-list handling).
    for c in fn.named_children:
        if c.type == "constructor_initializer":
            for sub in c.named_children:
                if sub.type == "argument_list":
                    for a in sub.named_children:
                        g.link(ev(a, None), g.add("CALL"), _DATA)

    body = fn.child_by_field_name("body")
    if body is None:  # expression-bodied member: `int M() => expr;`
        arrow = fn.child_by_field_name("expression_body") or fn.child_by_field_name("value")
        if arrow is not None:
            n = g.add("RETURN")
            g.link(ev(_last(arrow) or arrow, None), n, _DATA)
        return g
    _do_body(body, None)
    return g


def _nc(node):
    return nc(node)


def _first(node):
    return first(node)


def _last(node):
    return last(node)


def _op_text(node, _text) -> str:
    return op_text(node)


# --- STATEMENT layer (PDG) — design §5c sweep, C# ------------------------------------------------

_PDG_STMT_LABEL = {
    "local_declaration_statement": "Decl", "expression_statement": "Expr",
    "return_statement": "Return", "if_statement": "If", "for_statement": "For",
    "foreach_statement": "ForEach", "while_statement": "While", "do_statement": "Do",
    "switch_statement": "Switch", "try_statement": "Try", "using_statement": "Using",
    "throw_statement": "Throw", "yield_statement": "Yield", "lock_statement": "Lock",
    "labeled_statement": "Labeled", "block": "Block", "break_statement": "Break",
    "continue_statement": "Continue", "goto_statement": "Goto", "checked_statement": "Checked",
    "unsafe_statement": "Unsafe", "fixed_statement": "Fixed", "empty_statement": "Empty",
    "arrow_expression_clause": "Return",
}


def _pdg_label(t: str) -> str:
    return _PDG_STMT_LABEL.get(t) or "".join(w.capitalize() for w in t.split("_")) or "Stmt"


def pdg_source(source: str, lang: str = "csharp") -> dict[str, tuple[list[str], list]]:
    """Program-dependence graph of every method/constructor — the STATEMENT-layer companion to
    fingerprint_source/vfg_source (identical keys), the raw graph get_matrix(layer="statement")
    drills into. Statement nodes + control ('C') / data ('D') dependence edges via a sequential
    reaching-def approximation; nested lambdas / anonymous methods / local functions are opaque
    NESTED leaves. Advisory, on demand.

    Accepted layer-level under-approximations (cross-language consistent, mirror the sibling PDGs),
    all *symmetric* — shared by BOTH the PDG and the VFG so they create no VFG/PDG divergence:
    lambda / anonymous-method / local-function bodies are opaque, a caught-exception name is not
    modelled as flow, and an `is`/`switch` pattern binding is not modelled as flow (both builders
    read the scrutinee only). Method-parameter default-value expressions are read by the VFG but not
    seeded here (the one accepted asymmetry, shared by every sibling PDG)."""
    return _walk(source, lang, build=lambda fn, data: _build_pdg(fn, data))


def _build_pdg(fn, _data: bytes) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer for a C# method/constructor — a program-dependence graph mirroring
    `structure._build_pdg` (Python) and the JS-family/Go/Rust/C++/Java PDG builders: statement nodes
    + a synthetic ENTRY carrying the parameters, control ('C') / data ('D', sequential reaching-def)
    edges. C# is statement-oriented (like Java and C/C++). Nested lambdas/anonymous methods/local
    functions are opaque NESTED leaves; reorder-invariant. A structural approximation (no SSA/alias
    analysis), advisory only — never feeds liveness. Its read/write projection (`collect`/`bind_place`)
    reads ONLY genuine value operands and records ONLY genuine bindings, matching the VFG's `ev`/`bind`:
    TYPE positions, the member NAME in a `.` access, and pattern/label names are never read as values."""
    nodes, edges, last_def, new_id, data_from = pdg_state()
    text = node_text

    entry = new_id("ENTRY")
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            if p.type == "parameter":
                nm = p.child_by_field_name("name")
                if nm is not None:
                    last_def[text(nm)] = entry

    def bind_place(target, loads: set, stores: set) -> None:
        """An assignment target. A plain identifier defines a name (a STORE); a member/element place
        (`s.f = …`, `a[i] = …`) defines no name — its object/index are READS; a tuple `(a, b) = …`
        deconstructs (each element binds). Mirrors the VFG's `bind`."""
        if target is None:
            return
        t = target.type
        if t == "identifier":
            stores.add(text(target))
        elif t == "tuple_expression":
            for c in target.named_children:
                bind_place(c, loads, stores)
        elif t == "parenthesized_expression":
            bind_place(_last(target), loads, stores)
        else:  # member_access / element_access / element_binding: no name — read object/index operands
            collect(target, loads, stores)

    def rmw_target(target, loads: set, stores: set) -> None:
        """A read-modify-write target (`x += e`, `x++`): a plain lvalue both READS and WRITES the
        name; a member/element place reads its operands. Unwraps parentheses."""
        while target is not None and target.type == "parenthesized_expression":
            target = _last(target)
        if target is None:
            return
        if target.type == "identifier":
            stores.add(text(target))
            loads.add(text(target))
        else:
            collect(target, loads, stores)

    def _var_declaration(decl, loads: set, stores: set) -> None:
        for d in decl.named_children:
            if d.type == "variable_declarator":
                nm = d.child_by_field_name("name")
                stores.add(text(nm if nm is not None else _first(d)))
                # The initializer is every declarator child EXCEPT the `name` (identified by its
                # FIELD, not its node type) and the `bracketed_argument_list`. A bare-identifier
                # initializer `int r = v;` is an `identifier`, so a blanket identifier-skip would
                # drop the copy — mirror the VFG's `_do_var_declaration` name-field exclusion.
                nspan = (nm.start_byte, nm.end_byte) if nm is not None else None
                for c in d.named_children:
                    if nspan is not None and (c.start_byte, c.end_byte) == nspan:
                        continue
                    if c.type == "equals_value_clause":
                        collect(_last(c), loads, stores)
                    elif c.type != "bracketed_argument_list":
                        collect(c, loads, stores)

    def collect(n, loads: set, stores: set) -> None:
        """Reads/writes within one statement's header — stops at nested blocks (their own nodes) and
        nested lambdas/anonymous methods/local functions (opaque), mirroring the sibling PDGs. Never
        reads a TYPE position, a member NAME, or a pattern/label name."""
        if n is None:
            return
        t = n.type
        if t in _FUNC_NODES or t == "comment":
            return  # a lambda / nested body is opaque (matches the VFG's NESTED leaf)
        if t == "argument":  # C# wraps each call/index argument in an `argument` node
            collect(_last(n), loads, stores)
            return
        if t == "identifier":
            loads.add(text(n))
            return
        if t in ("this_expression", "base_expression", "this", "base") or t in _CONST:
            return  # the VFG routes these through `freevar` / CONST — not a parameter read
        if t == "parenthesized_expression":
            collect(_last(n), loads, stores)
            return
        if t == "member_access_expression":  # read the object; the member name is not a value
            collect(n.child_by_field_name("expression"), loads, stores)
            return
        if t == "element_access_expression":
            collect(n.child_by_field_name("expression"), loads, stores)
            sub = n.child_by_field_name("subscript")
            if sub is not None:
                for a in sub.named_children:
                    collect(a, loads, stores)
            return
        if t == "invocation_expression":
            collect(n.child_by_field_name("function"), loads, stores)
            args = n.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    collect(a, loads, stores)
            return
        if t == "object_creation_expression":  # `new T(args){init}` — the type is not a read
            args = n.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    collect(a, loads, stores)
            collect(n.child_by_field_name("initializer"), loads, stores)
            return
        if t == "array_creation_expression":  # `new T[n]{init}` — type not read; dims/init are
            typ = n.child_by_field_name("type")
            if typ is not None:
                for rank in typ.named_children:
                    if rank.type == "array_rank_specifier":
                        for d in rank.named_children:
                            collect(d, loads, stores)
            for c in n.named_children:
                if c.type == "initializer_expression":
                    collect(c, loads, stores)
            return
        if t in ("initializer_expression", "implicit_array_creation_expression"):
            for c in n.named_children:
                collect(c, loads, stores)
            return
        if t == "interpolated_string_expression":  # value flows through the `{...}` holes only
            for c in n.named_children:
                if c.type == "interpolation":
                    for ic in c.named_children:
                        if ic.type == "interpolation_alignment_clause":
                            for ac in ic.named_children:
                                collect(ac, loads, stores)
                        elif ic.type not in ("interpolation_brace", "interpolation_format_clause"):
                            collect(ic, loads, stores)
            return
        if t == "assignment_expression":
            op = _op_text(n, text)
            left = n.child_by_field_name("left")
            if op and op != "=" and op.endswith("="):  # `x += e` reads AND writes the left operand
                rmw_target(left, loads, stores)
            else:
                bind_place(left, loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t in ("prefix_unary_expression", "postfix_unary_expression"):
            if _op_text(n, text) in ("++", "--"):
                rmw_target(_first(n), loads, stores)
            else:
                collect(_first(n), loads, stores)
            return
        if t == "cast_expression":  # `(T) x` — the cast type carries no read; the value does
            collect(n.child_by_field_name("value"), loads, stores)
            return
        if t in ("is_pattern_expression", "is_expression"):  # read the operand; type/pattern are not
            collect(n.child_by_field_name("expression") or _first(n), loads, stores)
            return
        if t in ("await_expression", "checked_expression", "ref_expression"):
            collect(_last(n), loads, stores)
            return
        if t == "switch_expression":  # value-position switch: fold governing expr + arm reads
            kids = _nc(n)
            if kids:
                collect(kids[0], loads, stores)
                for arm in kids[1:]:
                    if arm.type == "switch_expression_arm":
                        for c in arm.named_children:
                            collect(c, loads, stores)
            return
        if t == "variable_declaration":
            _var_declaration(n, loads, stores)
            return
        if t == "local_declaration_statement":
            for decl in n.named_children:
                if decl.type == "variable_declaration":
                    _var_declaration(decl, loads, stores)
            return
        for c in n.named_children:
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
        if blk.type == "block":
            for st in blk.named_children:
                block(st, parent)
        else:
            process(blk, parent)

    def process(node, parent: int) -> None:
        t = node.type
        if t == "comment":
            return
        if t in _FUNC_NODES:  # local function / lambda / anonymous method — opaque leaf
            edges.append((parent, new_id("NESTED"), "C"))
            return
        sid = new_id(_pdg_label(t))
        edges.append((parent, sid, "C"))
        if t == "if_statement":
            data_edges(_strip(node.child_by_field_name("condition")), sid)
            block(node.child_by_field_name("consequence"), sid)
            block(node.child_by_field_name("alternative"), sid)
        elif t == "for_statement":
            # `initializer`/`update` are REPEATED field children for comma forms; iterate all.
            for i in range(node.named_child_count):
                fld = node.field_name_for_named_child(i)
                if fld in ("initializer", "condition", "update"):
                    data_edges(node.named_children[i], sid)
            block(node.child_by_field_name("body"), sid)
        elif t == "foreach_statement":  # `foreach (var x in iterable)`
            data_edges(node.child_by_field_name("right"), sid)  # the iterated value is a read
            left = node.child_by_field_name("left")
            if left is not None:  # the loop var binds (may be a tuple deconstruction)
                binds: set = set()
                bind_place(left, set(), binds)
                for nm in sorted(binds):
                    last_def[nm] = sid
            block(node.child_by_field_name("body"), sid)
        elif t in ("while_statement", "do_statement"):
            data_edges(_strip(node.child_by_field_name("condition")), sid)
            block(node.child_by_field_name("body"), sid)
        elif t == "switch_statement":
            data_edges(_strip(node.child_by_field_name("value")
                              or node.child_by_field_name("condition")), sid)
            body = node.child_by_field_name("body")
            for sec in (body.named_children if body is not None else node.named_children):
                if sec.type == "switch_section":
                    for st in sec.named_children:
                        if st.type.endswith("_label"):  # older grammar: labels wrap the selector
                            for lbl in st.named_children:
                                data_edges(lbl, sid)
                        elif st.type.endswith("_pattern") or st.type == "when_clause":
                            # this grammar exposes `case <pattern> [when guard]:` as bare pattern /
                            # when_clause children of the section — a case SELECTOR reads on the
                            # header (mirrors the VFG's `do`→`ev` on these), never a statement node.
                            data_edges(st, sid)
                        elif st.type != "comment":
                            block(st, sid)
        elif t == "using_statement":
            body = node.child_by_field_name("body")
            bspan = (body.start_byte, body.end_byte) if body is not None else None
            for c in node.named_children:
                if bspan is not None and (c.start_byte, c.end_byte) == bspan:
                    continue
                data_edges(c, sid)  # collect handles a resource variable_declaration or a bare expr
            block(body, sid)
        elif t == "try_statement":
            block(node.child_by_field_name("body"), sid)
            for ch in node.named_children:
                if ch.type == "catch_clause":
                    for cc in ch.named_children:
                        if cc.type == "catch_filter_clause":  # `when (predicate)` is executed
                            for fc in cc.named_children:
                                data_edges(fc, sid)
                    block(ch.child_by_field_name("body"), sid)
                elif ch.type == "finally_clause":
                    for c in ch.named_children:
                        if c.type == "block":
                            block(c, sid)
        elif t in ("lock_statement", "checked_statement", "unsafe_statement", "fixed_statement",
                   "labeled_statement"):
            for c in node.named_children:
                if c.type == "block":
                    block(c, sid)
                elif c.type.endswith("statement"):
                    block(c, sid)
                elif c.type not in ("comment", "identifier"):  # skip the label identifier
                    data_edges(c, sid)  # the lock/fixed resource expression is a read
        elif t == "block":
            for c in node.named_children:
                block(c, sid)
        elif t in ("break_statement", "continue_statement", "goto_statement", "empty_statement"):
            pass  # a break/continue/goto LABEL is a control target, not a value read
        else:
            # a simple statement (declaration / expression / return / throw / yield / …): the whole
            # node is its header. collect stops at nested blocks/functions. Any nested block child is
            # still descended (parity with the sibling PDGs).
            data_edges(node, sid)
            for c in node.named_children:
                if c.type == "block":
                    block(c, sid)

    # A constructor initializer (`: this(args)` / `: base(args)`) runs before the body; its args read.
    for c in fn.named_children:
        if c.type == "constructor_initializer":
            sid = new_id("CtorInit")
            edges.append((entry, sid, "C"))
            for sub in c.named_children:
                if sub.type == "argument_list":
                    for a in sub.named_children:
                        data_edges(a, sid)

    body = fn.child_by_field_name("body")
    if body is not None:
        block(body, entry)
    else:  # expression-bodied member: `int M() => expr;`
        arrow = fn.child_by_field_name("expression_body") or fn.child_by_field_name("value")
        if arrow is not None:
            sid = new_id("Return")
            edges.append((entry, sid, "C"))
            data_edges(_last(arrow) or arrow, sid)
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges
