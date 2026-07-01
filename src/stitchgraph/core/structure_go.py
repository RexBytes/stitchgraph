"""Structural (body-level) fingerprints for Go functions and methods.

The Go frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So Go↔Go bodies compare exactly the way every other per-language frontend's do (one shared WL kernel).

Advisory and read-only, like the other frontends — it never feeds `find_stale`, so the cardinal rule
does not apply. Requires the optional tree-sitter extra; with it absent every entry point returns
`{}` (the body layer simply has nothing to add). Cross-language comparison (a Go fingerprint vs a
Python or JS one) is oracle-only — topology tracks the extractor, never proof — so callers rank
within one language (see `similar.find_similar_structure`).

It is a structural approximation, NOT sound data flow: copy propagation but no SSA/alias/escape
analysis, constants collapsed, channel/pointer semantics flattened to value flow. The
construct→value-flow mapping is Go-specific; the bug taxonomy and the completeness-oracle method that
drove it are in `docs/BODY_MATRIX_LESSONS.md`.

Qualname scheme: bare names (`add`, `Method`) — the same the Go extractor produces (it keys a method
by its field name, not by its receiver type, and does not mint nested `func_literal` closures as
nodes). A nested closure is therefore an opaque `NESTED` leaf, never its own fingerprint key.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _serialize_vfg, _wl_features

_EXTS = {".go": "go"}

# Function-like nodes whose bodies are fingerprinted. When NESTED (a `func_literal` closure), opaque —
# the same choice as Python lambdas and JS nested functions: one NESTED node, not the body.
_FUNC_NODES = frozenset({"function_declaration", "method_declaration", "func_literal"})

# CST wrappers that carry no value flow of their own — descend through to the operand (last child).
_TRANSPARENT = frozenset({"parenthesized_expression", "parenthesized_type"})

# Leaf literals — one CONST node regardless of value (constants are collapsed by design).
_CONST = frozenset({
    "int_literal", "float_literal", "imaginary_literal", "rune_literal",
    "interpreted_string_literal", "raw_string_literal", "true", "false", "nil", "iota",
})


def _parser(lang: str = "go"):
    """A tree-sitter Go parser, or None if the extra isn't installed (advisory degrade)."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar("go"))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _walk(source: str, lang: str = "go", build=None):
    """Shared traversal for fingerprint_source / vfg_source: apply build(<fn_node>, data) per function."""
    parser = _parser()
    if parser is None:
        return {}
    try:
        data = source.encode("utf-8", "replace")
        tree = parser.parse(data)
    except (ValueError, RecursionError):
        return {}
    out: dict[str, collections.Counter[str]] = {}

    def name_of(node) -> str | None:
        n = node.child_by_field_name("name")
        return n.text.decode("utf-8", "replace") if n is not None else None

    def visit(node) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("function_declaration", "method_declaration"):
                name = name_of(child)
                if name:
                    try:
                        out[name] = build(child, data)
                    except RecursionError:
                        pass
                # a top-level func can't nest another top-level func; closures are opaque, but descend
                # anyway so the generic walk never silently drops a future nesting host.
                visit(child)
            else:
                visit(child)

    try:
        visit(tree.root_node)
    except (RecursionError, ValueError):
        return out
    return out


def fingerprint_source(source: str, lang: str = "go") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a Go source string, keyed by bare name (`add`, `Method`)
    — the same scheme the tree-sitter Go extractor produces. Returns {} on a parse failure, a missing
    tree-sitter extra, or a too-deep tree (advisory, never raises)."""
    return _walk(source, lang, lambda fn, data: _wl_features(_build_vfg(fn, data)))


def vfg_source(source: str, lang: str = "go") -> dict[str, tuple[list[str], list]]:
    """Value-flow graph of every function/method — the EXPRESSION-layer companion to fingerprint_source (identical keys). Advisory, on demand."""
    return _walk(source, lang, lambda fn, data: _serialize_vfg(_build_vfg(fn, data)))


def pdg_source(source: str, lang: str = "go") -> dict[str, tuple[list[str], list]]:
    """Program-dependence graph of every function/method — the STATEMENT-layer companion to
    fingerprint_source/vfg_source (identical keys), the raw graph get_matrix(layer="statement")
    drills into. Statement nodes + control ('C') / data ('D') dependence edges via a sequential
    reaching-def approximation; nested functions are opaque NESTED leaves. Advisory, on demand."""
    return _walk(source, lang, lambda fn, data: _build_pdg(fn, data))


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one Go function/method node into a value-flow graph, mirroring
    `structure._build_vfg` for Python: PARAM seeds (receiver + params + named results), copy
    propagation through locals, operations and control points as nodes, data/control edges."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    # seed the receiver (`func (r *T) M()` — r is like self), the parameters, and any named results
    # (`func f() (n int)` — n is an in-scope zero-valued local).
    for fld in ("receiver", "parameters", "result"):
        plist = fn.child_by_field_name(fld)
        if plist is not None and plist.type == "parameter_list":
            for p in plist.named_children:
                for name in _param_names(p, text):
                    if name and name != "_":
                        env[name] = g.add("PARAM")

    def bind(target, val: int | None) -> None:
        t = target.type
        if t == "identifier":
            name = text(target)
            if name == "_":
                return
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)
        elif t in ("selector_expression", "index_expression"):
            n = g.add("SETATTR" if t == "selector_expression" else "SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("operand"), None), n, _DATA)
            if t == "index_expression":  # the index expression carries flow (`d[helper()] = v`)
                g.link(ev(target.child_by_field_name("index"), None), n, _DATA)
        elif t == "unary_expression":  # pointer write `*p = v`
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("operand"), None), n, _DATA)
        elif t == "parenthesized_expression":
            bind(_last(target), val)

    def _exprs(node):
        """The element expressions of an expression_list (or the node itself if it isn't one)."""
        if node is None:
            return []
        if node.type == "expression_list":
            return list(node.named_children)
        return [node]

    def _assign(left_list, right_list, ctrl) -> None:
        lefts = _exprs(left_list)
        rights = _exprs(right_list)
        rvals = [ev(r, ctrl) for r in rights]
        if len(rvals) == 1 and len(lefts) > 1:
            for left in lefts:  # tuple unpack from one multi-valued call: every target gets it
                bind(left, rvals[0])
        else:
            for i, left in enumerate(lefts):
                bind(left, rvals[i] if i < len(rvals) else None)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t == "comment":  # trivia: a comment must never alter the value-flow fingerprint
            return None
        if t in _TRANSPARENT:
            return ev(_last(node), ctrl)
        if t in ("identifier", "field_identifier", "package_identifier", "type_identifier"):
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t in _CONST:
            return g.add("CONST")
        if t == "selector_expression":
            n = g.add("ATTR")
            g.link(ev(node.child_by_field_name("operand"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "index_expression":
            n = g.add("SUBSCRIPT")
            g.link(ev(node.child_by_field_name("operand"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("index"), ctrl), n, _DATA)
            return n
        if t == "slice_expression":
            n = g.add("SUBSCRIPT")
            g.link(ev(node.child_by_field_name("operand"), ctrl), n, _DATA)
            for fld in ("start", "end", "capacity"):
                sub = node.child_by_field_name(fld)
                if sub is not None:
                    g.link(ev(sub, ctrl), n, _DATA)
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
        if t == "type_conversion_expression":
            # `T(x)` — a conversion; the value flows from the operand, the type carries none.
            n = g.add("CONVERT")
            g.link(ev(node.child_by_field_name("operand"), ctrl), n, _DATA)
            return n
        if t == "type_assertion_expression":
            # `x.(T)` — the value is the operand; the asserted type carries no flow (like a JS cast).
            return ev(node.child_by_field_name("operand"), ctrl)
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "unary_expression":
            # covers -a, !b, ^x, &x (address-of), *p (deref), <-ch (channel receive).
            n = g.add("UNARY:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("operand"), ctrl), n, _DATA)
            return n
        if t == "composite_literal":
            # struct / slice / array / map literal: the type carries no value flow, the elements do.
            n = g.add("COMPOSITE")
            body = node.child_by_field_name("body")
            if body is not None:
                for el in body.named_children:
                    if el.type == "keyed_element":
                        g.link(ev(el.child_by_field_name("key"), ctrl), n, _DATA)
                        g.link(ev(el.child_by_field_name("value"), ctrl), n, _DATA)
                    else:
                        g.link(ev(el, ctrl), n, _DATA)
            return n
        if t in ("literal_element", "keyed_element"):
            # a composite-literal element wrapper: descend to its payload expression(s).
            n = g.add("ELEM")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "func_literal":
            return g.add("NESTED")
        if t == "expression_list":
            n = g.add("SEQ")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        # generic fallback: a node fed by its sub-expressions (so a construct not yet handled can
        # never silently vanish from the fingerprint — the completeness oracle makes gaps visible).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def do(node, ctrl: int | None) -> None:
        t = node.type
        if t == "comment":  # trivia
            return
        if t == "short_var_declaration":
            _assign(node.child_by_field_name("left"), node.child_by_field_name("right"), ctrl)
        elif t in ("var_declaration", "const_declaration"):
            for spec in node.named_children:
                if spec.type in ("var_spec", "const_spec"):
                    names = [c for c in spec.named_children if c.type == "identifier"]
                    vals = spec.child_by_field_name("value")
                    rvals = [ev(v, ctrl) for v in _exprs(vals)] if vals is not None else []
                    if len(rvals) == 1 and len(names) > 1:
                        for nm in names:
                            bind(nm, rvals[0])
                    else:
                        for i, nm in enumerate(names):
                            bind(nm, rvals[i] if i < len(rvals) else None)
        elif t == "assignment_statement":
            op = _op_text(node, text)
            left_list = node.child_by_field_name("left")
            right_list = node.child_by_field_name("right")
            if op and op != "=" and op.endswith("="):
                # compound assignment `x op= e` (single operand each side) == `x = x op e`.
                base = op[:-1]
                left = (_exprs(left_list) or [None])[0]
                right = (_exprs(right_list) or [None])[0]
                n = g.add("BINOP:" + base)
                g.link(ev(left, ctrl), n, _DATA)
                g.link(ev(right, ctrl), n, _DATA)
                g.link(ctrl, n, _CTRL)
                if left is not None:
                    bind(left, n)
            else:
                _assign(left_list, right_list, ctrl)
        elif t in ("inc_statement", "dec_statement"):
            arg = _first(node)
            n = g.add("UNARY:" + ("++" if t == "inc_statement" else "--"))
            g.link(ev(arg, ctrl), n, _DATA)
            if arg is not None:
                bind(arg, n)
        elif t == "return_statement":
            n = g.add("RETURN")
            for c in node.named_children:
                for e in _exprs(c):
                    g.link(ev(e, ctrl), n, _DATA)
        elif t == "send_statement":
            n = g.add("SEND")
            g.link(ev(node.child_by_field_name("channel"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("value"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
        elif t in ("go_statement", "defer_statement", "labeled_statement"):
            for c in node.named_children:
                _stmt_or_expr(c, ctrl)
        elif t == "if_statement":
            init = node.child_by_field_name("initializer")
            if init is not None:
                do(init, ctrl)
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("condition"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            _do_body(node.child_by_field_name("consequence"), b)
            _do_body(node.child_by_field_name("alternative"), b)
        elif t == "for_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            for child in node.named_children:
                ct = child.type
                if ct == "for_clause":
                    for fld in ("initializer", "update"):
                        sub = child.child_by_field_name(fld)
                        if sub is not None:
                            do(sub, loop)
                    cond = child.child_by_field_name("condition")
                    if cond is not None:
                        ev(cond, loop)
                elif ct == "range_clause":
                    it = g.add("ITERVAR")
                    g.link(ev(child.child_by_field_name("right"), loop), it, _DATA)
                    left = child.child_by_field_name("left")
                    if left is not None:
                        for tgt in _exprs(left):
                            bind(tgt, it)
                elif ct == "block":
                    _do_body(child, loop)
                else:  # a bare condition expression: `for x < n {}`
                    ev(child, loop)
        elif t in ("expression_switch_statement", "type_switch_statement"):
            init = node.child_by_field_name("initializer")
            if init is not None:
                do(init, ctrl)
            b = g.add("BRANCH")
            val = node.child_by_field_name("value")
            if val is not None:
                g.link(ev(val, ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            for case in node.named_children:
                if case.type in ("expression_case", "type_case", "default_case"):
                    c = g.add("CASE")
                    cval = case.child_by_field_name("value")
                    if cval is not None:
                        for e in _exprs(cval):
                            g.link(ev(e, b), c, _DATA)
                    _do_case_body(case, c, skip=cval)
        elif t == "select_statement":
            b = g.add("BRANCH")
            g.link(ctrl, b, _CTRL)
            for case in node.named_children:
                if case.type in ("communication_case", "default_case"):
                    c = g.add("CASE")
                    comm = case.child_by_field_name("communication")
                    if comm is not None:
                        _stmt_or_expr(comm, c)
                    _do_case_body(case, c, skip=comm)
        elif t in ("go", "block"):
            _do_body(node, ctrl)
        elif t in ("break_statement", "continue_statement", "goto_statement",
                   "fallthrough_statement", "empty_statement"):
            pass
        elif t.endswith("_statement") or t in ("statement_list",):
            for ch in node.named_children:
                _stmt_or_expr(ch, ctrl)
        else:
            # an expression used as a statement (a bare `f()` call), or an unhandled node.
            ev(node, ctrl)

    def _stmt_or_expr(node, ctrl: int | None) -> None:
        if node is None:
            return
        t = node.type
        if t.endswith("statement") or t in ("statement_list", "block", "short_var_declaration",
                                            "var_declaration", "const_declaration"):
            do(node, ctrl)
        else:
            ev(node, ctrl)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type == "block":
            for ch in node.named_children:
                _do_body(ch, ctrl)
        elif node.type == "statement_list":
            for st in node.named_children:
                _stmt_or_expr(st, ctrl)
        else:
            _stmt_or_expr(node, ctrl)

    def _do_case_body(case, ctrl: int | None, skip) -> None:
        skip_span = (skip.start_byte, skip.end_byte) if skip is not None else None
        for st in case.named_children:
            if (st.start_byte, st.end_byte) == skip_span:
                continue
            if st.type == "statement_list":
                for s in st.named_children:
                    _stmt_or_expr(s, ctrl)
            else:
                _stmt_or_expr(st, ctrl)

    body = fn.child_by_field_name("body")
    if body is not None:
        _do_body(body, None)
    return g


def _param_names(node, text) -> list[str]:
    """Identifier names bound by one parameter_declaration / receiver / named-result entry.
    `a, b int` binds a and b; `r *T` binds r; an unnamed `int` binds nothing."""
    t = node.type
    if t == "identifier":
        return [text(node)]
    if t in ("parameter_declaration", "variadic_parameter_declaration"):
        return [text(c) for c in node.named_children if c.type == "identifier"]
    return []


def _nc(node):
    """Named children minus comment trivia. Tree-sitter exposes comments as named nodes, so any
    positional pick over ``named_children`` (``[0]`` / ``[-1]`` / ``[i]``) can be silently displaced
    by a leading/trailing comment — filter them out before selecting a child by position."""
    return [c for c in node.named_children if c.type != "comment"]


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
    # operator isn't a named field on every grammar version — scan anonymous children.
    for c in node.children:
        if not c.is_named and c.text:
            return c.text.decode("utf-8", "replace")
    return "?"


# --- STATEMENT layer (PDG) — design §5c sweep, Go ------------------------------------------------

_PDG_STMT_LABEL = {
    "if_statement": "If",
    "for_statement": "For",
    "expression_switch_statement": "Switch", "type_switch_statement": "Switch",
    "select_statement": "Select",
    "return_statement": "Return",
    "short_var_declaration": "Assign", "assignment_statement": "Assign",
    "var_declaration": "Assign", "const_declaration": "Assign",
    "expression_statement": "Expr", "send_statement": "Send",
    "inc_statement": "IncDec", "dec_statement": "IncDec",
    "go_statement": "Go", "defer_statement": "Defer", "labeled_statement": "Labeled",
    "break_statement": "Break", "continue_statement": "Continue",
    "goto_statement": "Goto", "fallthrough_statement": "Fallthrough", "empty_statement": "Empty",
}


def _pdg_label(t: str) -> str:
    return _PDG_STMT_LABEL.get(t) or "".join(w.capitalize() for w in t.split("_")) or "Stmt"


def _build_pdg(fn, data: bytes) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer for a Go function — a program-dependence graph mirroring
    `structure._build_pdg` (Python) and `structure_js._build_pdg`: statement nodes + a synthetic
    ENTRY carrying the parameters (and receiver), control ('C') / data ('D', sequential reaching-def)
    edges. Nested functions (`func_literal`) are opaque NESTED leaves; reorder-invariant. A structural
    approximation (no SSA/alias analysis), advisory only — never feeds liveness."""
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
    for fld in ("receiver", "parameters"):
        plist = fn.child_by_field_name(fld)
        if plist is not None:
            for p in plist.named_children:
                for nm in _param_names(p, text):
                    last_def[nm] = entry

    def add_target(n, loads: set, stores: set) -> None:
        """Names bound by an assignment/declaration target (a `store`). A selector/index target
        (`obj.f = …`, `a[i] = …`) defines no name — its object/index are READS."""
        if n is None:
            return
        t = n.type
        if t == "identifier":
            stores.add(text(n))
        elif t in ("expression_list", "parenthesized_expression"):
            for c in n.named_children:
                add_target(c, loads, stores)
        elif t in ("selector_expression", "index_expression", "unary_expression"):
            collect(n, loads, stores)  # pointer-deref `*p = …`, `obj.f = …`, `a[i] = …` → reads

    def collect(n, loads: set, stores: set) -> None:
        """Reads/writes within one statement's header — stops at nested blocks (their own nodes) and
        nested functions (opaque), mirroring Python's header_names."""
        if n is None:
            return
        t = n.type
        if t in _FUNC_NODES or t in ("block", "comment"):
            return
        if t == "short_var_declaration":
            add_target(n.child_by_field_name("left"), loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t in ("var_spec", "const_spec"):
            for c in n.named_children:
                if c.type == "identifier":
                    stores.add(text(c))
            collect(n.child_by_field_name("value"), loads, stores)
            return
        if t == "assignment_statement":
            op = _op_text(n, text)
            left = n.child_by_field_name("left")
            if op and op != "=" and op.endswith("="):
                # `x op= e` reads and writes each left operand.
                for tgt in (left.named_children if left is not None else []):
                    if tgt.type == "identifier":
                        stores.add(text(tgt))
                        loads.add(text(tgt))
                    else:
                        collect(tgt, loads, stores)
            else:
                add_target(left, loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t in ("inc_statement", "dec_statement"):
            arg = _first(n)
            if arg is not None and arg.type == "identifier":
                stores.add(text(arg))
                loads.add(text(arg))
            elif arg is not None:
                collect(arg, loads, stores)
            return
        if t == "identifier":
            loads.add(text(n))
            return
        if t == "selector_expression":
            collect(n.child_by_field_name("operand"), loads, stores)  # skip the field name
            return
        for c in n.named_children:
            collect(c, loads, stores)

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

    def bind_target(node, sid: int) -> None:
        # a `range` binding (`for k, v := range m`) STORES its loop vars.
        st: set = set()
        add_target(node, set(), st)
        for nm in sorted(st):
            last_def[nm] = sid

    def block(blk, parent: int) -> None:
        if blk is None or blk.type == "comment":
            return
        if blk.type in ("block", "statement_list"):
            for st in blk.named_children:
                block(st, parent)
        else:
            process(blk, parent)

    def _case_body(case, sid: int, skip) -> None:
        skip_span = (skip.start_byte, skip.end_byte) if skip is not None else None
        for st in case.named_children:
            if st.type == "comment" or (st.start_byte, st.end_byte) == skip_span:
                continue
            if st.type == "statement_list":
                for s in st.named_children:
                    block(s, sid)
            elif st.type not in ("expression_list",) or skip is None:
                block(st, sid)

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
            data_edges(node.child_by_field_name("initializer"), sid)
            data_edges(node.child_by_field_name("condition"), sid)
            block(node.child_by_field_name("consequence"), sid)
            block(node.child_by_field_name("alternative"), sid)
        elif t == "for_statement":
            for child in node.named_children:
                ct = child.type
                if ct == "for_clause":
                    data_edges(child, sid)
                elif ct == "range_clause":
                    data_edges(child.child_by_field_name("right"), sid)
                    left = child.child_by_field_name("left")
                    if left is not None:
                        bind_target(left, sid)
                elif ct == "block":
                    block(child, sid)
                elif ct != "comment":
                    data_edges(child, sid)  # a bare condition expression
        elif t in ("expression_switch_statement", "type_switch_statement"):
            data_edges(node.child_by_field_name("initializer"), sid)
            data_edges(node.child_by_field_name("value"), sid)
            for case in node.named_children:
                if case.type in ("expression_case", "type_case", "default_case"):
                    cval = case.child_by_field_name("value")
                    if cval is not None:
                        data_edges(cval, sid)
                    _case_body(case, sid, cval)
        elif t == "select_statement":
            for case in node.named_children:
                if case.type in ("communication_case", "default_case"):
                    comm = case.child_by_field_name("communication")
                    if comm is not None:
                        data_edges(comm, sid)
                    _case_body(case, sid, comm)
        elif t == "labeled_statement":
            for c in node.named_children:
                if c.type not in ("label_name", "comment"):
                    block(c, sid)
        else:
            # a simple statement (return/send/inc-dec/expr/assign/var/const/go/defer/break/…): the
            # whole node is its header — collect stops at nested blocks/functions, so nothing leaks.
            # Any nested block child is still descended (parity with the JS layer / Python walk_block).
            data_edges(node, sid)
            for c in node.named_children:
                if c.type == "block":
                    block(c, sid)

    body = fn.child_by_field_name("body")
    if body is not None:
        block(body, entry)
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges
