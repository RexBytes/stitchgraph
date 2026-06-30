"""Structural (body-level) fingerprints for C and C++ functions and methods.

The C/C++ frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So C↔C and C++↔C++ bodies compare exactly the way every other per-language frontend's do (one shared WL kernel).

One walker covers both C and C++: the `cpp` tree-sitter grammar is a superset that parses C cleanly,
and the extractor already unifies the two. Advisory and read-only — it never feeds `find_stale`, so
the cardinal rule does not apply. Requires the optional tree-sitter extra; absent it every entry
point returns `{}`. Cross-language comparison is oracle-only (topology tracks the extractor); callers
rank within one language (see `similar.find_similar_structure`).

C/C++ specifics handled: the function NAME lives inside the declarator (`function_declarator`,
possibly wrapped in `pointer_declarator`/`reference_declarator` for `int* f()` / `int& f()`); an
out-of-line method `int Foo::m()` uses a `qualified_identifier` — keyed by the bare last component
(`m`), matching the extractor. C/C++ is statement-oriented (explicit `return`, no trailing-expression
value like Rust). Assignment is an expression; compound `+=`/`<<=` normalises to the base operator +
a rebind. `cast_expression` and `parenthesized_expression` are transparent to their operand; the cast
*type* carries no flow. Lambdas (C++) are opaque `NESTED` leaves. Function-like `#define` macros are
preprocessor constructs, not `function_definition`s, so they're out of scope; a *call* to a macro
parses as a `call_expression` and is fingerprinted like any call.

Qualname scheme matches the extractor: free / namespace / template functions are bare (`free_fn`,
`nsfn`, `gen`), inline class methods are `Class.method`, out-of-line `Foo::m` definitions are bare
`m`. It is a structural approximation, NOT sound data flow (no pointer/alias analysis, constants
collapsed, the preprocessor is not expanded). The method is in `docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _wl_features

# One grammar (cpp) parses both; map every C/C++ source/header extension to it.
_EXTS = {ext: "cpp" for ext in
         (".c", ".h", ".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".ipp", ".inl")}

# Function-like nodes whose bodies are fingerprinted. A lambda, when nested, is an opaque leaf.
_FUNC_NODES = frozenset({"function_definition", "lambda_expression"})

# Leaf literals — one CONST node regardless of value.
_CONST = frozenset({
    "number_literal", "string_literal", "char_literal", "concatenated_string", "raw_string_literal",
    "true", "false", "null", "nullptr", "user_defined_literal",
})

# Declarator wrappers to unwrap when digging out a function/variable name.
_DECL_WRAP = frozenset({"pointer_declarator", "reference_declarator", "parenthesized_declarator",
                        "array_declarator", "init_declarator"})


def _parser(lang: str = "cpp"):
    """A tree-sitter C++ parser (parses C too), or None if the extra isn't installed."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar("cpp"))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _decl_child(node):
    """The inner declarator of a wrapper. Most wrappers (`pointer_declarator`, `init_declarator`, …)
    name it under the `declarator` field, but `reference_declarator` does NOT field-name its child —
    so fall back to the last named child that is itself a declarator/name (else `T& f()` reference-
    return functions are silently dropped from the fingerprint map, R185 opus)."""
    inner = node.child_by_field_name("declarator")
    if inner is not None:
        return inner
    for c in reversed(node.named_children):
        if c.type.endswith("declarator") or c.type in (
                "identifier", "field_identifier", "qualified_identifier", "destructor_name",
                "operator_name", "operator_cast"):
            return c
    return None


def _name_of_declarator(decl, text):
    """The bare name a function/variable declarator binds — unwrapping pointer/reference/array
    wrappers, reading a function_declarator's inner declarator, and taking the last component of a
    qualified_identifier (`Foo::m` -> `m`, matching the extractor's out-of-line scheme)."""
    node = decl
    seen = 0
    while node is not None and seen < 32:
        seen += 1
        t = node.type
        if t == "identifier" or t == "field_identifier":
            return text(node)
        if t == "qualified_identifier":
            nm = node.child_by_field_name("name")
            return _name_of_declarator(nm, text) if nm is not None else None
        if t in ("destructor_name", "operator_name", "operator_cast"):
            return text(node)
        if t in _DECL_WRAP or t == "function_declarator":
            node = _decl_child(node)
            continue
        # template_function / template_type wrapping a name
        nm = node.child_by_field_name("name") or node.child_by_field_name("declarator")
        node = nm
    return None


def _func_declarator(decl):
    """Find the function_declarator inside a (possibly pointer/reference-wrapped) declarator."""
    node = decl
    seen = 0
    while node is not None and seen < 32:
        seen += 1
        if node.type == "function_declarator":
            return node
        if node.type in _DECL_WRAP:
            node = _decl_child(node)
            continue
        return None
    return None


def fingerprint_source(source: str, lang: str = "cpp") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a C/C++ source string, keyed by the extractor's scheme
    (bare `free_fn`, `Class.method` for inline methods, bare `m` for out-of-line `Foo::m`). Returns
    {} on a parse failure, a missing tree-sitter extra, or a too-deep tree (advisory, never raises)."""
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
            if t == "function_definition":
                decl = child.child_by_field_name("declarator")
                name = _name_of_declarator(decl, text) if decl is not None else None
                if name:
                    emit(prefix + name, child)
                # nested function definitions / lambdas inside the body are opaque — don't recurse
                # into the body for more keys (matches the extractor's granularity).
            elif t in ("class_specifier", "struct_specifier"):
                nm = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                if body is not None:
                    visit(body, prefix + text(nm) + "." if nm is not None else prefix)
            elif t in ("namespace_definition", "template_declaration", "linkage_specification",
                       "declaration_list", "preproc_if", "preproc_ifdef", "extern"):
                body = child.child_by_field_name("body")
                visit(body if body is not None else child, prefix)
            else:
                visit(child, prefix)

    try:
        visit(tree.root_node, "")
    except (RecursionError, ValueError):
        return out
    return out


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one C/C++ function/lambda node into a value-flow graph, mirroring
    `structure._build_vfg`: PARAM seeds, copy propagation through declarations, operations and control
    points as nodes, data/control edges. Statement-oriented (explicit returns)."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    # seed parameters from the function_declarator's parameter_list.
    fdecl = _func_declarator(fn.child_by_field_name("declarator")) if fn.type == "function_definition" else None
    if fdecl is None:  # lambda: declarator is an abstract_function_declarator / lambda has parameters
        fdecl = fn.child_by_field_name("declarator")
    params = fdecl.child_by_field_name("parameters") if fdecl is not None else None
    if params is not None:
        for p in params.named_children:
            if p.type in ("parameter_declaration", "optional_parameter_declaration"):
                nm = _name_of_declarator(p.child_by_field_name("declarator"), text)
                if nm:
                    env[nm] = g.add("PARAM")

    def bind(target, val: int | None) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier" or t == "field_identifier":
            name = text(target)
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)
        elif t in _DECL_WRAP:
            if t == "array_declarator":  # `int arr[helper()]` — a VLA size is runtime value flow
                sz = target.child_by_field_name("size")
                if sz is not None:
                    ev(sz, None)
            bind(target.child_by_field_name("declarator"), val)
        elif t in ("field_expression", "subscript_expression"):
            n = g.add("SETATTR" if t == "field_expression" else "SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("argument"), None), n, _DATA)
        elif t == "pointer_expression":  # `*p = v` deref write
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("argument"), None), n, _DATA)
        elif t == "parenthesized_expression":
            inner = target.named_children
            if inner:
                bind(inner[-1], val)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t == "comment":  # trivia: a comment must never alter the value-flow fingerprint
            return None
        if t in ("identifier", "field_identifier", "type_identifier", "namespace_identifier"):
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t in ("this", "true", "false", "nullptr", "null"):
            return g.add("CONST") if t in ("true", "false", "nullptr", "null") else freevar("this")
        if t in _CONST:
            return g.add("CONST")
        if t == "qualified_identifier":
            return freevar(text(node))
        if t == "parenthesized_expression":
            inner = node.named_children
            return ev(inner[-1], ctrl) if inner else None
        if t == "field_expression":
            n = g.add("ATTR")
            g.link(ev(node.child_by_field_name("argument"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "subscript_expression":
            n = g.add("SUBSCRIPT")
            g.link(ev(node.child_by_field_name("argument"), ctrl), n, _DATA)
            # the cpp grammar keeps the index under `indices` -> subscript_argument_list (C uses a
            # plain `index` field); read both so `a[helper()]` doesn't drop the index's value flow.
            idx = node.child_by_field_name("indices") or node.child_by_field_name("index")
            if idx is not None and idx.type == "subscript_argument_list":
                for c in idx.named_children:
                    g.link(ev(c, ctrl), n, _DATA)
            elif idx is not None:
                g.link(ev(idx, ctrl), n, _DATA)
            return n
        if t == "call_expression":
            n = g.add("CALL")
            g.link(ev(node.child_by_field_name("function"), ctrl), n, _DATA)
            args = node.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "new_expression":
            n = g.add("CALL")
            args = node.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            # placement-new `new (addr) T(...)` keeps the placement address under a `placement` field
            # (an argument_list), separate from constructor `arguments` — walk it so a computed
            # placement address isn't dropped (R187-redo opus).
            placement = node.child_by_field_name("placement")
            if placement is not None:
                for a in placement.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            # array-new `new T[size]` keeps the size under the `new_declarator`'s `length` field
            # (possibly nested for `new T[i][j]`), NOT under `arguments` — walk every length so the
            # size expression's value flow isn't dropped (R187 opus).
            nd = node.child_by_field_name("declarator")
            while nd is not None and nd.type == "new_declarator":
                ln = nd.child_by_field_name("length")
                if ln is not None:
                    g.link(ev(ln, ctrl), n, _DATA)
                nxt = None
                for c in nd.named_children:
                    if c.type == "new_declarator":
                        nxt = c
                        break
                nd = nxt
            g.link(ctrl, n, _CTRL)
            return n
        if t == "delete_expression":
            inner = node.named_children
            return ev(inner[-1], ctrl) if inner else g.add("DELETE")
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("unary_expression", "pointer_expression"):
            n = g.add("UNARY:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("argument"), ctrl), n, _DATA)
            return n
        if t == "update_expression":
            arg = node.child_by_field_name("argument")
            n = g.add("UNARY:" + _op_text(node, text))
            g.link(ev(arg, ctrl), n, _DATA)
            bind(arg, n)  # x++ / --x rebinds
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
        if t in ("cast_expression",):  # `(T)x` / functional cast — value flows, type doesn't
            return ev(node.child_by_field_name("value"), ctrl)
        if t == "comma_expression":
            n = g.add("SEQ")
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            return n
        if t in ("initializer_list", "compound_literal_expression"):
            n = g.add("COMPOSITE")
            for c in node.named_children:
                if c.type == "initializer_pair":
                    g.link(ev(c.child_by_field_name("value"), ctrl), n, _DATA)
                elif c.type not in ("type_descriptor",):
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("sizeof_expression", "alignof_expression"):
            return g.add("CONST")
        if t in _FUNC_NODES:
            n = g.add("NESTED")
            # A C++14 lambda init-capture `[z = helper()]` is evaluated in the ENCLOSING scope when
            # the closure is built — that's enclosing-scope value flow, not lambda-body flow (the body
            # stays an opaque NESTED leaf). Walk each capture initializer's value (R188 opus).
            if t == "lambda_expression":
                caps = node.child_by_field_name("captures")
                if caps is not None:
                    for c in caps.named_children:
                        if c.type == "lambda_capture_initializer":
                            g.link(ev(c.child_by_field_name("right"), ctrl), n, _DATA)
            return n
        # generic fallback: a node fed by its sub-expressions (the completeness oracle makes gaps
        # visible, so an unhandled construct can never silently vanish from the fingerprint).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def _strip_cond(node):
        # if/while conditions wrap in a parenthesized_expression or condition_clause; descend.
        while node is not None and node.type in ("parenthesized_expression", "condition_clause") \
                and node.named_children:
            node = node.named_children[-1]
        return node

    def _cond_init(node, ctrl):
        # C++17 `if (init; cond)` / `switch (init; cond)`: the condition_clause carries an
        # `initializer` field (an init_statement wrapping a declaration) whose value flow must be
        # evaluated, else `if (int x = helper(); x)` and `if (int x = 0; x)` collapse (R187-redo opus).
        if node is not None and node.type == "condition_clause":
            init = node.child_by_field_name("initializer")
            if init is not None:
                for c in init.named_children:
                    do(c, ctrl)

    def do(node, ctrl: int | None) -> None:
        t = node.type
        if t == "comment":  # trivia
            return
        if t == "declaration":
            for decl in node.named_children:
                if decl.type == "init_declarator":
                    val = ev(decl.child_by_field_name("value"), ctrl)
                    bind(decl.child_by_field_name("declarator"), val)
                elif decl.type in _DECL_WRAP or decl.type == "identifier":
                    bind(decl, None)  # uninitialised declaration: declares the name, no value
        elif t == "expression_statement":
            for c in node.named_children:
                ev(c, ctrl)
        elif t == "return_statement":
            n = g.add("RETURN")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
        elif t == "if_statement":
            cond = node.child_by_field_name("condition")
            _cond_init(cond, ctrl)
            b = g.add("BRANCH")
            g.link(ev(_strip_cond(cond), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            _do_body(node.child_by_field_name("consequence"), b)
            _do_body(node.child_by_field_name("alternative"), b)
        elif t in ("for_statement", "while_statement", "do_statement"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            for fld in ("initializer", "condition", "update"):
                sub = node.child_by_field_name(fld)
                if sub is not None:
                    if sub.type == "declaration":
                        do(sub, loop)
                    else:
                        ev(_strip_cond(sub), loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "for_range_loop":  # C++11 `for (auto x : container)`
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            init = node.child_by_field_name("initializer")  # C++20 `for (init; auto x : c)`
            if init is not None:
                for c in init.named_children:
                    do(c, loop)
            it = g.add("ITERVAR")
            g.link(ev(node.child_by_field_name("right"), loop), it, _DATA)
            bind(node.child_by_field_name("declarator"), it)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "switch_statement":
            cond = node.child_by_field_name("condition")
            _cond_init(cond, ctrl)
            b = g.add("BRANCH")
            g.link(ev(_strip_cond(cond), ctrl), b, _DATA)
            body = node.child_by_field_name("body")
            if body is not None:
                for case in body.named_children:
                    if case.type == "case_statement":
                        c = g.add("CASE")
                        val = case.child_by_field_name("value")
                        g.link(ev(val, b), c, _DATA)
                        vspan = (val.start_byte, val.end_byte) if val is not None else None
                        for st in case.named_children:
                            if (st.start_byte, st.end_byte) != vspan:
                                do(st, c)
        elif t == "try_statement":
            _do_body(node.child_by_field_name("body"), ctrl)
            for ch in node.named_children:
                if ch.type == "catch_clause":
                    _do_body(ch.child_by_field_name("body"), ctrl)
        elif t == "labeled_statement":
            for c in node.named_children:
                if c.type.endswith("statement") or c.type == "compound_statement":
                    do(c, ctrl)
                else:
                    ev(c, ctrl)
        elif t == "compound_statement":
            _do_body(node, ctrl)
        elif t in ("break_statement", "continue_statement", "goto_statement"):
            pass
        elif t.endswith("statement"):
            for ch in node.named_children:
                if ch.type.endswith("statement") or ch.type == "compound_statement":
                    do(ch, ctrl)
                else:
                    ev(ch, ctrl)
        else:
            ev(node, ctrl)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type == "compound_statement":
            for st in node.named_children:
                do(st, ctrl)
        elif node.type == "else_clause":
            for st in node.named_children:
                _do_body(st, ctrl)
        else:
            do(node, ctrl)

    # A constructor/destructor function-try-block (`S() try : init {...} catch(...) {}`) has NO `body`
    # field — the grammar gives an unnamed `try_statement` child, inside which live the member-init
    # list, the compound_statement body, and the catch clauses. Without this, both the body and the
    # member-inits of such a special member are silently dropped (R190 opus).
    func_try = None
    if fn.child_by_field_name("body") is None:
        for ch in fn.named_children:
            if ch.type == "try_statement":
                func_try = ch
                break

    # C++ constructor member-initializer-list (`S(int x): n(compute(x)), m(0) {}`) is a SIBLING of the
    # body — a `field_initializer_list`, not inside the compound_statement — so it is never reached by
    # walking `body`. Evaluate each member's init expression as a member write so its value flow is
    # captured; else `n(compute(x))` and `n(0)` fingerprint identically (R186 opus). For a function-
    # try-block the list lives inside the try_statement instead.
    for ch in (func_try if func_try is not None else fn).named_children:
        if ch.type != "field_initializer_list":
            continue
        for fi in ch.named_children:
            if fi.type != "field_initializer":
                continue
            n = g.add("SETATTR")  # the member being initialised is the write target
            for c in fi.named_children:
                if c.type in ("field_identifier", "qualified_identifier", "type_identifier"):
                    continue
                if c.type == "argument_list":
                    for a in c.named_children:
                        g.link(ev(a, None), n, _DATA)
                else:
                    g.link(ev(c, None), n, _DATA)

    # A parameter's default-value expression carries flow (`int f(int b = helper())`): walk it now
    # that `ev` is defined and link it into the parameter's PARAM node.
    if params is not None:
        for p in params.named_children:
            if p.type == "optional_parameter_declaration":
                nm = _name_of_declarator(p.child_by_field_name("declarator"), text)
                val = p.child_by_field_name("default_value")
                if nm and val is not None and nm in env:
                    g.link(ev(val, None), env[nm], _DATA)

    if func_try is not None:
        do(func_try, None)  # the try_statement handler walks the body + every catch clause
    else:
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
