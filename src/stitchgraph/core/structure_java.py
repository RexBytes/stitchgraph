"""Structural (body-level) fingerprints for Java methods and constructors.

The Java frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So Java↔Java bodies compare exactly the way every other per-language frontend's do (one shared WL kernel).

Advisory and read-only — it never feeds `find_stale`, so the cardinal rule does not apply. Requires
the optional tree-sitter extra; absent it every entry point returns `{}`. Cross-language comparison is
oracle-only (topology tracks the extractor); callers rank within one language
(see `similar.find_similar_structure`).

Java specifics: everything lives in a type (no free functions). Methods/constructors are keyed by the
dotted chain of enclosing type names (the package/`import`s are NOT part of the key) — `Outer.compute`,
nested `Outer.Inner.m`, interface `Shape.area`, constructor `C.C` — matching the extractor. Java is
statement-oriented (explicit `return`). Assignment is an expression; compound `+=`/`<<=` normalises to
the base operator + a rebind. `cast_expression`/`parenthesized_expression` are transparent to their
operand; the cast *type* carries no flow. Lambdas / anonymous classes are opaque `NESTED` leaves.
`switch` (statement or expression) walks each case value/label once. It is a structural approximation,
NOT sound data flow (no alias analysis, constants collapsed). The method is in
`docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _serialize_vfg, _wl_features

_EXTS = {".java": "java"}

# Function-like nodes whose bodies are fingerprinted. A lambda / anonymous class, when nested, is an
# opaque leaf.
_FUNC_NODES = frozenset({"method_declaration", "constructor_declaration",
                         "lambda_expression"})

# Type declarations that contribute a `Name.` segment to the qualname and whose bodies are walked for
# more methods.
_TYPE_NODES = frozenset({"class_declaration", "interface_declaration", "enum_declaration",
                         "record_declaration", "annotation_type_declaration"})

# Leaf literals — one CONST node regardless of value.
_CONST = frozenset({
    "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
    "binary_integer_literal", "decimal_floating_point_literal", "hex_floating_point_literal",
    "string_literal", "character_literal", "true", "false", "null_literal",
})


def _parser():
    """A tree-sitter Java parser, or None if the extra isn't installed."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar("java"))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _walk(source: str, lang: str = "java", *, build) -> dict:
    """Shared traversal for `fingerprint_source` / `vfg_source`: apply `build(fn_node, data)` per keyed method."""
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
            if t in ("method_declaration", "constructor_declaration"):
                nm = child.child_by_field_name("name")
                # Java has no free functions: only key a method/constructor that is actually inside a
                # type (non-empty `prefix`). A top-level method only appears in error-tolerant parses
                # of NON-Java source (e.g. a C/C++ free function), so refusing to key it keeps the
                # find_similar language sniff from grabbing C/C++ snippets as Java.
                if nm is not None and prefix:
                    emit(prefix + text(nm), child)
                # nested types inside a method body are opaque — don't recurse for more keys.
            elif t in _TYPE_NODES:
                nm = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                if body is not None and nm is not None:
                    visit(body, prefix + text(nm) + ".")
                elif body is not None:
                    visit(body, prefix)
            else:
                visit(child, prefix)

    try:
        visit(tree.root_node, "")
    except (RecursionError, ValueError):
        return out
    return out


def fingerprint_source(source: str, lang: str = "java") -> dict[str, collections.Counter[str]]:
    """Fingerprint every method/constructor in a Java source string, keyed by the extractor's scheme
    (`Outer.compute`, nested `Outer.Inner.m`, constructor `C.C`). Returns {} on a parse failure, a
    missing tree-sitter extra, or a too-deep tree (advisory, never raises)."""
    return _walk(source, lang, build=lambda fn, data: _wl_features(_build_vfg(fn, data)))


def vfg_source(source: str, lang: str = "java") -> dict[str, tuple[list[str], list]]:
    """Value-flow graph of every function/method — EXPRESSION-layer companion to fingerprint_source
    (identical keys). Advisory, on demand."""
    return _walk(source, lang, build=lambda fn, data: _serialize_vfg(_build_vfg(fn, data)))


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one Java method/constructor/lambda node into a value-flow graph,
    mirroring `structure._build_vfg`: PARAM seeds, copy propagation through declarations, operations
    and control points as nodes, data/control edges. Statement-oriented (explicit returns)."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    # seed parameters from the formal_parameters list.
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            if p.type in ("formal_parameter", "spread_parameter"):
                nm = p.child_by_field_name("name")
                if nm is None:  # spread_parameter wraps a variable_declarator
                    for c in p.named_children:
                        if c.type == "variable_declarator":
                            nm = c.child_by_field_name("name")
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
        elif t == "field_access":
            n = g.add("SETATTR")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("object"), None), n, _DATA)
        elif t == "array_access":
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("array"), None), n, _DATA)
            g.link(ev(target.child_by_field_name("index"), None), n, _DATA)
        elif t == "parenthesized_expression":
            bind(_last(target), val)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t in ("line_comment", "block_comment"):  # trivia: never alters the fingerprint
            return None
        if t == "identifier":
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t == "this" or t == "super":
            return freevar(t)
        if t in _CONST:
            return g.add("CONST")
        if t == "parenthesized_expression":
            return ev(_last(node), ctrl)
        if t == "field_access":
            n = g.add("ATTR")
            g.link(ev(node.child_by_field_name("object"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "array_access":
            n = g.add("SUBSCRIPT")
            g.link(ev(node.child_by_field_name("array"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("index"), ctrl), n, _DATA)
            return n
        if t == "method_invocation":
            n = g.add("CALL")
            g.link(ev(node.child_by_field_name("object"), ctrl), n, _DATA)
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
            g.link(ctrl, n, _CTRL)
            return n
        if t == "array_creation_expression":
            n = g.add("CALL")
            for c in node.named_children:
                if c.type == "dimensions_expr":
                    for d in c.named_children:
                        g.link(ev(d, ctrl), n, _DATA)
                elif c.type == "array_initializer":
                    g.link(ev(c, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "array_initializer":
            n = g.add("COMPOSITE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "unary_expression":
            n = g.add("UNARY:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("operand"), ctrl), n, _DATA)
            return n
        if t == "update_expression":  # x++ / --x
            operand = _first(node)
            n = g.add("UNARY:" + _op_text(node, text))
            g.link(ev(operand, ctrl), n, _DATA)
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
        if t == "ternary_expression":
            n = g.add("IFEXP")
            g.link(ev(node.child_by_field_name("condition"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("consequence"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("alternative"), ctrl), n, _DATA)
            return n
        if t == "cast_expression":  # `(T) x` — value flows, type doesn't
            return ev(node.child_by_field_name("value"), ctrl)
        if t == "instanceof_expression":
            n = g.add("BINOP:instanceof")
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            return n
        if t == "switch_expression":
            return _switch(node, ctrl, as_value=True)
        if t in _FUNC_NODES:  # lambda / (object_creation handled above) — opaque
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

    def _switch(node, ctrl: int | None, as_value: bool) -> int:
        b = g.add("BRANCH")
        g.link(ev(_strip_cond(node.child_by_field_name("condition")), ctrl), b, _DATA)
        g.link(ctrl, b, _CTRL)
        body = node.child_by_field_name("body")
        if body is not None:
            for grp in body.named_children:
                if grp.type == "switch_block_statement_group":  # classic `case L:` + stmts
                    for st in grp.named_children:
                        if st.type == "switch_label":
                            for lbl in st.named_children:
                                ev(lbl, b)
                        else:
                            do(st, b)
                elif grp.type == "switch_rule":  # arrow `case L -> expr/block/throw;`
                    for c in grp.named_children:
                        if c.type == "switch_label":
                            for lbl in c.named_children:
                                ev(lbl, b)
                        elif c.type in ("block", "expression_statement", "throw_statement"):
                            do(c, b)
                        else:
                            ev(c, b)
        return b

    def do(node, ctrl: int | None) -> None:
        t = node.type
        if t in ("line_comment", "block_comment"):  # trivia
            return
        if t == "local_variable_declaration":
            for decl in node.named_children:
                if decl.type == "variable_declarator":
                    val = ev(decl.child_by_field_name("value"), ctrl)
                    bind(decl.child_by_field_name("name"), val)
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
            # `init` and `update` are REPEATED field children for comma forms (`i++, j--` /
            # `i = 0, j = n`), so iterate every named child by field name — `child_by_field_name`
            # would return only the first and drop the rest (R197 opus).
            for i in range(node.named_child_count):
                sub = node.named_children[i]
                fld = node.field_name_for_named_child(i)
                if fld in ("init", "condition", "update"):
                    if sub.type == "local_variable_declaration":
                        do(sub, loop)
                    else:
                        ev(_strip_cond(sub), loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "enhanced_for_statement":  # `for (T x : iterable)`
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            it = g.add("ITERVAR")
            g.link(ev(node.child_by_field_name("value"), loop), it, _DATA)
            bind(node.child_by_field_name("name"), it)
            _do_body(node.child_by_field_name("body"), loop)
        elif t in ("while_statement", "do_statement"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            cond = node.child_by_field_name("condition")
            if cond is not None:
                ev(_strip_cond(cond), loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t in ("switch_statement", "switch_expression"):
            _switch(node, ctrl, as_value=False)
        elif t == "try_statement":
            # try-with-resources: the resource initializers carry value flow.
            for ch in node.named_children:
                if ch.type == "resource_specification":
                    for r in ch.named_children:
                        if r.type == "resource":
                            do_resource(r, ctrl)
            _do_body(node.child_by_field_name("body"), ctrl)
            for ch in node.named_children:
                if ch.type == "catch_clause":
                    _do_body(ch.child_by_field_name("body"), ctrl)
                elif ch.type == "finally_clause":
                    for c in ch.named_children:
                        if c.type == "block":
                            _do_body(c, ctrl)
        elif t in ("synchronized_statement", "labeled_statement"):
            for c in node.named_children:
                if c.type == "block":
                    _do_body(c, ctrl)
                elif c.type.endswith("statement"):
                    do(c, ctrl)
                else:
                    ev(c, ctrl)
        elif t == "block":
            _do_body(node, ctrl)
        elif t in ("break_statement", "continue_statement"):
            pass
        elif t.endswith("statement"):
            for ch in node.named_children:
                if ch.type == "block" or ch.type.endswith("statement"):
                    do(ch, ctrl)
                else:
                    ev(ch, ctrl)
        else:
            ev(node, ctrl)

    def do_resource(res, ctrl: int | None) -> None:
        # `try (T x = expr)` — a resource binds a name to an initializer's value.
        val = ev(res.child_by_field_name("value"), ctrl)
        nm = res.child_by_field_name("name")
        if nm is not None:
            bind(nm, val)
        elif val is None:
            for c in res.named_children:
                ev(c, ctrl)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type == "block":
            for st in node.named_children:
                do(st, ctrl)
        else:
            do(node, ctrl)

    body = fn.child_by_field_name("body")
    if body is not None:
        _do_body(body, None)
    return g


def _nc(node):
    """Named children minus comment trivia. Tree-sitter exposes comments as named nodes, so any
    positional pick over ``named_children`` (``[0]`` / ``[-1]`` / ``[i]``) can be silently displaced
    by a leading/trailing comment — filter them out before selecting a child by position."""
    return [c for c in node.named_children if c.type not in ("line_comment", "block_comment")]


def _first(node):
    k = _nc(node)
    return k[0] if k else None


def _last(node):
    k = _nc(node)
    return k[-1] if k else None


def _op_text(node, text) -> str:
    op = node.child_by_field_name("operator")
    if op is not None:
        return op.text.decode("utf-8", "replace")
    for c in node.children:
        if not c.is_named and c.text:
            return c.text.decode("utf-8", "replace")
    return "?"


# --- STATEMENT layer (PDG) — design §5c sweep, Java ----------------------------------------------

_PDG_STMT_LABEL = {
    "local_variable_declaration": "Decl", "expression_statement": "Expr",
    "return_statement": "Return", "if_statement": "If", "for_statement": "For",
    "enhanced_for_statement": "ForEach", "while_statement": "While", "do_statement": "Do",
    "switch_statement": "Switch", "switch_expression": "Switch", "try_statement": "Try",
    "try_with_resources_statement": "Try", "throw_statement": "Throw", "yield_statement": "Yield",
    "labeled_statement": "Labeled", "block": "Block", "break_statement": "Break",
    "continue_statement": "Continue", "synchronized_statement": "Sync",
    "assert_statement": "Assert", "explicit_constructor_invocation": "Expr",
}
# TYPE positions — a name here is structural, never a value read (the typed-language hazard that
# recurred across TS/Rust/C++). Mirrors the VFG, which routes type names through `freevar` / drops
# the cast type. A `type_identifier` in a value expression (a bare class-name reference) is genuinely
# not a value read in either builder.
_PDG_TYPE_NODES = frozenset({
    "type_identifier", "scoped_type_identifier", "generic_type", "integral_type",
    "floating_point_type", "boolean_type", "void_type", "array_type", "annotated_type",
    "wildcard", "type_parameter", "type_arguments",
})


def _pdg_label(t: str) -> str:
    return _PDG_STMT_LABEL.get(t) or "".join(w.capitalize() for w in t.split("_")) or "Stmt"


def pdg_source(source: str, lang: str = "java") -> dict[str, tuple[list[str], list]]:
    """Program-dependence graph of every method/constructor — the STATEMENT-layer companion to
    fingerprint_source/vfg_source (identical keys), the raw graph get_matrix(layer="statement")
    drills into. Statement nodes + control ('C') / data ('D') dependence edges via a sequential
    reaching-def approximation; nested methods/lambdas/anonymous classes are opaque NESTED leaves.
    Advisory, on demand.

    Accepted layer-level under-approximations (cross-language consistent, mirror the Python
    reference and the sibling PDGs), all *symmetric* — shared by BOTH the PDG and the VFG so they
    create no VFG/PDG divergence: lambda / anonymous-class bodies are opaque (a param used only
    inside one is read by neither builder), and an `instanceof`/`switch` pattern binding is not
    modelled as flow by either builder."""
    return _walk(source, lang, build=lambda fn, data: _build_pdg(fn, data))


def _build_pdg(fn, data: bytes) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer for a Java method/constructor — a program-dependence graph mirroring
    `structure._build_pdg` (Python) and the JS-family/Go/Rust/C++ PDG builders: statement nodes + a
    synthetic ENTRY carrying the parameters, control ('C') / data ('D', sequential reaching-def)
    edges. Java is statement-oriented (like Go and C/C++). Nested methods/lambdas/anonymous classes
    are opaque NESTED leaves; reorder-invariant. A structural approximation (no SSA/alias analysis),
    advisory only — never feeds liveness. Its read/write projection (`collect`/`bind_place`) reads
    ONLY genuine value operands and records ONLY genuine bindings, matching the VFG's `ev`/`bind`:
    TYPE positions, statement LABELs, the method NAME in a call, and field/member names are never
    read as values."""
    nodes: dict[int, str] = {}
    edges: list[tuple[int, int, str]] = []
    counter = 0
    last_def: dict[str, int] = {}

    def text(n) -> str:
        return n.text.decode("utf-8", "replace")

    def new_id(label: str) -> int:
        nonlocal counter
        i = counter
        counter += 1
        nodes[i] = label
        return i

    entry = new_id("ENTRY")
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            if p.type in ("formal_parameter", "spread_parameter"):
                nm = p.child_by_field_name("name")
                if nm is None:  # spread_parameter wraps a variable_declarator
                    for c in p.named_children:
                        if c.type == "variable_declarator":
                            nm = c.child_by_field_name("name")
                if nm is not None:
                    last_def[text(nm)] = entry

    def bind_place(target, loads: set, stores: set) -> None:
        """An assignment target. A plain identifier defines a name (a STORE); a field/array place
        (`s.f = …`, `a[i] = …`) defines no name — its object/index are READS (mirrors the VFG's
        `bind`)."""
        if target is None:
            return
        t = target.type
        if t == "identifier":
            stores.add(text(target))
        elif t == "parenthesized_expression":
            bind_place(_last(target), loads, stores)
        else:  # field_access / array_access: no name defined — read the object/index operands
            collect(target, loads, stores)

    def rmw_target(target, loads: set, stores: set) -> None:
        """A read-modify-write target (`x += e`, `x++`): a plain lvalue both READS and WRITES the
        name; a field/array place reads its operands (no name defined). Unwraps parentheses."""
        while target is not None and target.type == "parenthesized_expression":
            target = _last(target)
        if target is None:
            return
        if target.type == "identifier":
            stores.add(text(target))
            loads.add(text(target))
        else:
            collect(target, loads, stores)

    def collect(n, loads: set, stores: set) -> None:
        """Reads/writes within one statement's header — stops at nested blocks (their own nodes) and
        nested functions/lambdas/anonymous classes (opaque), mirroring the sibling PDGs. Never reads
        a TYPE position, a statement LABEL, a call's method NAME, or a field/member name."""
        if n is None:
            return
        t = n.type
        if t in _FUNC_NODES or t in ("line_comment", "block_comment"):
            return  # a lambda / nested body is opaque (matches the VFG's NESTED leaf)
        if t in _PDG_TYPE_NODES or t in _CONST:
            return  # a type name / literal carries no value read
        if t in ("this", "super"):
            return  # the VFG routes these through `freevar` — not a parameter read
        if t == "identifier":
            loads.add(text(n))
            return
        if t == "field_access":  # read the object; the field name is not a value
            collect(n.child_by_field_name("object"), loads, stores)
            return
        if t == "method_invocation":  # read the receiver + args; the method NAME is not a value
            collect(n.child_by_field_name("object"), loads, stores)
            args = n.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    collect(a, loads, stores)
            return
        if t == "array_access":
            collect(n.child_by_field_name("array"), loads, stores)
            collect(n.child_by_field_name("index"), loads, stores)
            return
        if t == "object_creation_expression":  # `new T(args)` — the type is not a read; args are
            args = n.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    collect(a, loads, stores)
            return  # an anonymous-class body (if any) is opaque
        if t == "array_creation_expression":  # `new T[n]{init}` — type not read; dims/init are
            for c in n.named_children:
                if c.type == "dimensions_expr":
                    for d in c.named_children:
                        collect(d, loads, stores)
                elif c.type == "array_initializer":
                    collect(c, loads, stores)
            return
        if t == "cast_expression":  # `(T) x` — the cast type carries no read; the value does
            collect(n.child_by_field_name("value"), loads, stores)
            return
        if t == "instanceof_expression":  # read the left operand; the type + pattern var are not reads
            collect(n.child_by_field_name("left"), loads, stores)
            return
        if t == "local_variable_declaration":
            for decl in n.named_children:
                if decl.type == "variable_declarator":
                    nm = decl.child_by_field_name("name")
                    if nm is not None:
                        stores.add(text(nm))
                    collect(decl.child_by_field_name("value"), loads, stores)
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
        if t == "update_expression":  # `x++` / `--x` reads and writes its operand
            rmw_target(_first(n), loads, stores)
            return
        if t == "switch_expression":  # value-position switch: fold scrutinee + labels + arm reads
            collect(_strip(n.child_by_field_name("condition")), loads, stores)
            body = n.child_by_field_name("body")
            if body is not None:
                for grp in body.named_children:
                    if grp.type in ("switch_block_statement_group", "switch_rule"):
                        for c in grp.named_children:
                            if c.type == "switch_label":
                                for lbl in c.named_children:
                                    collect(lbl, loads, stores)
                            else:
                                collect(c, loads, stores)
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
        # sorted iteration: a string set iterates in PYTHONHASHSEED order, which would make the edge
        # list (and get_matrix cells) non-reproducible across processes (R205).
        for nm in sorted(loads):
            if nm in last_def and last_def[nm] != sid:
                edges.append((last_def[nm], sid, "D"))
        for nm in sorted(stores):
            last_def[nm] = sid

    def block(blk, parent: int) -> None:
        if blk is None or blk.type in ("line_comment", "block_comment"):
            return
        if blk.type in ("block", "constructor_body"):
            for st in blk.named_children:
                block(st, parent)
        else:
            process(blk, parent)

    def process(node, parent: int) -> None:
        t = node.type
        if t in ("line_comment", "block_comment"):
            return
        if t in _FUNC_NODES:
            edges.append((parent, new_id("NESTED"), "C"))
            return
        sid = new_id(_pdg_label(t))
        edges.append((parent, sid, "C"))
        if t == "if_statement":
            data_edges(_strip(node.child_by_field_name("condition")), sid)
            block(node.child_by_field_name("consequence"), sid)
            block(node.child_by_field_name("alternative"), sid)
        elif t == "for_statement":
            # `init`/`update` are REPEATED field children for comma forms (`i++, j--`); iterate every
            # named child by field name — child_by_field_name returns only the first (R197 parity).
            for i in range(node.named_child_count):
                fld = node.field_name_for_named_child(i)
                if fld in ("init", "condition", "update"):
                    data_edges(node.named_children[i], sid)
            block(node.child_by_field_name("body"), sid)
        elif t == "enhanced_for_statement":  # `for (T x : iterable)`
            data_edges(node.child_by_field_name("value"), sid)  # the iterated value is a read
            nm = node.child_by_field_name("name")
            if nm is not None:
                last_def[text(nm)] = sid  # the loop var binds
            block(node.child_by_field_name("body"), sid)
        elif t in ("while_statement", "do_statement"):
            data_edges(_strip(node.child_by_field_name("condition")), sid)
            block(node.child_by_field_name("body"), sid)
        elif t in ("switch_statement", "switch_expression"):
            data_edges(_strip(node.child_by_field_name("condition")), sid)  # scrutinee
            body = node.child_by_field_name("body")
            if body is not None:
                for grp in body.named_children:
                    if grp.type == "switch_block_statement_group":  # classic `case L:` + stmts
                        for st in grp.named_children:
                            if st.type == "switch_label":
                                for lbl in st.named_children:
                                    data_edges(lbl, sid)  # a case selector reads on the header
                            elif st.type not in ("line_comment", "block_comment"):
                                block(st, sid)
                    elif grp.type == "switch_rule":  # arrow `case L -> expr/block/throw;`
                        for c in grp.named_children:
                            if c.type == "switch_label":
                                for lbl in c.named_children:
                                    data_edges(lbl, sid)
                            elif c.type in ("block", "expression_statement", "throw_statement"):
                                block(c, sid)
                            elif c.type not in ("line_comment", "block_comment"):
                                data_edges(c, sid)  # arrow value expression reads on the header
        elif t in ("try_statement", "try_with_resources_statement"):
            for ch in node.named_children:
                if ch.type == "resource_specification":
                    for r in ch.named_children:
                        if r.type == "resource":
                            val = r.child_by_field_name("value")
                            data_edges(val if val is not None else r, sid)
                            rnm = r.child_by_field_name("name")
                            if rnm is not None:
                                last_def[text(rnm)] = sid
            block(node.child_by_field_name("body"), sid)
            for ch in node.named_children:
                if ch.type == "catch_clause":
                    for c in ch.named_children:
                        if c.type == "catch_formal_parameter":
                            cnm = c.child_by_field_name("name")
                            if cnm is not None:
                                last_def[text(cnm)] = sid  # the caught exception binds
                    block(ch.child_by_field_name("body"), sid)
                elif ch.type == "finally_clause":
                    for c in ch.named_children:
                        if c.type == "block":
                            block(c, sid)
        elif t == "synchronized_statement":
            for c in node.named_children:
                if c.type == "block":
                    block(c, sid)
                elif c.type not in ("line_comment", "block_comment"):
                    data_edges(c, sid)  # the lock expression is a read
        elif t == "labeled_statement":
            # descend the inner statement; the LABEL (an identifier) is NOT a value.
            for c in node.named_children:
                if c.type != "identifier" and c.type not in ("line_comment", "block_comment"):
                    block(c, sid)
        elif t == "block":
            for c in node.named_children:
                block(c, sid)
        elif t in ("break_statement", "continue_statement"):
            pass  # a break/continue LABEL is a control target, not a value read
        else:
            # a simple statement (declaration / expression / return / throw / yield / assert / …):
            # the whole node is its header. collect stops at nested blocks/functions, so nothing
            # leaks. Any nested block child is still descended (parity with the sibling PDGs).
            data_edges(node, sid)
            for c in node.named_children:
                if c.type == "block":
                    block(c, sid)

    body = fn.child_by_field_name("body")
    block(body, entry)
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges
