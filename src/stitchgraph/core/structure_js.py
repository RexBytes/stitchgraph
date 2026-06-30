"""Structural (body-level) fingerprints for JavaScript / TypeScript / TSX functions.

The JS-family frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So JS↔JS bodies compare exactly the way Python↔
Python ones do.

Advisory and read-only, like the Python layer — it never feeds `find_stale`, so the cardinal rule
does not apply. Requires the optional tree-sitter extra; with it absent every entry point returns
`{}` (the body layer simply has nothing to add). Cross-language comparison (a Python fingerprint vs a
JS one) is oracle-only — topology tracks the extractor, never proof — so callers rank within one
language (see `similar.find_similar_structure`).

It is a structural approximation, NOT sound data flow: copy propagation but no SSA/alias analysis,
constants collapsed. The construct→value-flow mapping is JS-specific; the bug taxonomy and the
completeness-oracle method that drove it are in `docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _wl_features

# JS-family grammars share one tree-sitter family; one walker covers all three.
_LANGS = ("javascript", "typescript", "tsx")
_EXTS = {".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
         ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}

# Function-like nodes whose bodies are fingerprinted (and which, when NESTED, are opaque leaves —
# the same choice as Python lambdas: a nested closure contributes one NESTED node, not its body).
_FUNC_NODES = frozenset({
    "function_declaration", "generator_function_declaration", "function", "function_expression",
    "generator_function", "arrow_function", "method_definition",
})
# tree-sitter wrappers that carry no value flow of their own — descend through transparently.
_TRANSPARENT = frozenset({"parenthesized_expression", "expression_statement", "statement_block",
                          "non_null_expression", "as_expression", "satisfies_expression",
                          "type_assertion"})
# Most transparent wrappers carry the operand as their LAST named child (`(x)`, `x!`, `<T>x`), so
# descending to inner[-1] lands on the value. But `x as T` / `x satisfies T` are `operand <kw> type`
# — the value is the FIRST child and the LAST is the (no-value-flow) type. Descend to inner[0] for
# these, else inner[-1] would keep the type and DROP the operand's value flow (R174 opus).
_CAST_OPERAND_FIRST = frozenset({"as_expression", "satisfies_expression"})


def _parser(lang: str):
    """A tree-sitter parser for `lang`, or None if the extra isn't installed (advisory degrade)."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar(lang))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def fingerprint_source(source: str, lang: str = "javascript") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a JS-family source string, keyed by qualified name
    (`Class.method`, nested `outer.inner`) — the same scheme the tree-sitter extractor produces.
    Returns {} on a parse failure, a missing tree-sitter extra, or a too-deep tree (advisory, never
    raises)."""
    if lang not in _LANGS:
        lang = "javascript"
    parser = _parser(lang)
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

    def emit(name: str, fn_node, prefix: str) -> None:
        if not name:
            visit(fn_node, prefix)
            return
        try:
            out[prefix + name] = _wl_features(_build_vfg(fn_node, data))
        except RecursionError:
            pass
        visit(fn_node, prefix + name + ".")

    def visit(node, prefix: str) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("class_declaration", "class"):
                cname = name_of(child)
                visit(child, prefix + cname + "." if cname else prefix)
            elif t in _FUNC_NODES:
                emit(name_of(child) or "", child, prefix)
            elif t == "variable_declarator":
                # `const h = (y) => …` / `const f = function(){…}` — the idiomatic JS named
                # function; the name is on the declarator, not the value. Match the extractor,
                # which mints these as Function nodes.
                val = child.child_by_field_name("value")
                if val is not None and val.type in _FUNC_NODES:
                    nm = child.child_by_field_name("name")
                    emit(nm.text.decode("utf-8", "replace") if nm is not None else "", val, prefix)
                else:
                    visit(child, prefix)
            elif t in ("public_field_definition", "field_definition", "pair"):
                # class field arrow (`m = () => …`) and object-literal method (`{ m: () => … }`).
                # `pair` exposes key/value fields; `field_definition` doesn't — its name is a
                # property_identifier child and the value a sibling, so locate them positionally.
                val = child.child_by_field_name("value")
                key = child.child_by_field_name("key")
                if val is None or key is None:
                    fn_kids = [c for c in child.named_children if c.type in _FUNC_NODES]
                    name_kids = [c for c in child.named_children
                                 if c.type in ("property_identifier", "private_property_identifier")]
                    val = fn_kids[0] if fn_kids else None
                    key = name_kids[0] if name_kids else None
                if val is not None and val.type in _FUNC_NODES and key is not None:
                    emit(key.text.decode("utf-8", "replace"), val, prefix)
                else:
                    visit(child, prefix)
            else:
                visit(child, prefix)

    try:
        visit(tree.root_node, "")
    except (RecursionError, ValueError):
        return out
    return out


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one function node into a value-flow graph, mirroring
    `structure._build_vfg` for Python: PARAM seeds, copy propagation through locals, operations and
    control points as nodes, data/control edges. `fn` is a tree-sitter function-like node."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    # seed parameters
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            for name in _param_names(p, text):
                env[name] = g.add("PARAM")

    def bind(target, val: int | None) -> None:
        t = target.type
        if t == "identifier":
            name = text(target)
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)
        elif t in ("array_pattern", "object_pattern", "array", "object"):
            for el in target.named_children:
                bind(el, val)
        elif t in ("rest_pattern", "spread_element", "rest_element"):
            for el in target.named_children:
                bind(el, val)
        elif t in ("shorthand_property_identifier_pattern", "shorthand_property_identifier"):
            env[text(target)] = val if val is not None else freevar(text(target))
        elif t == "pair_pattern":
            v = target.child_by_field_name("value")
            if v is not None:
                bind(v, val)
        elif t == "assignment_pattern":
            left = target.child_by_field_name("left")
            if left is not None:
                bind(left, val)
        elif t in ("member_expression", "subscript_expression"):
            n = g.add("SETATTR" if t == "member_expression" else "SETITEM")
            g.link(val, n, _DATA)
            obj = target.child_by_field_name("object")
            if obj is not None:
                g.link(ev(obj, None), n, _DATA)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t in _TRANSPARENT:
            inner = [c for c in node.named_children]
            if not inner:
                return None
            return ev(inner[0] if t in _CAST_OPERAND_FIRST else inner[-1], ctrl)
        if t == "identifier" or t == "shorthand_property_identifier":
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t in ("this", "super"):
            return freevar(t)
        if t in ("number", "string", "true", "false", "null", "undefined", "regex", "this_type"):
            return g.add("CONST")
        if t == "template_string":
            # a template literal carries value flow through its ${…} substitutions (like an
            # f-string); a plain template with none collapses to CONST.
            n = g.add("CONST")
            for sub in node.named_children:
                if sub.type == "template_substitution":
                    for e in sub.named_children:
                        g.link(ev(e, ctrl), n, _DATA)
            return n
        if t == "member_expression":
            n = g.add("ATTR")
            g.link(ev(node.child_by_field_name("object"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "subscript_expression":
            n = g.add("SUBSCRIPT")
            g.link(ev(node.child_by_field_name("object"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("index"), ctrl), n, _DATA)
            return n
        if t in ("call_expression", "new_expression"):
            n = g.add("CALL")
            # call_expression keeps its callee under field `function`; new_expression keeps it under
            # `constructor` — read both so `new (factories[a])()` doesn't drop the constructor's
            # value flow (R169 opus).
            callee = node.child_by_field_name("function") or node.child_by_field_name("constructor")
            g.link(ev(callee, ctrl), n, _DATA)
            args = node.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("binary_expression", "augmented_assignment_expression"):
            op = _op_text(node, text)
            if t == "augmented_assignment_expression" and op.endswith("="):
                op = op[:-1]  # `+=` -> `+` so `x += e` matches `x = x + e` (Python normalizes too)
            n = g.add("BINOP:" + op)
            left = node.child_by_field_name("left")
            g.link(ev(left, ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            if t == "augmented_assignment_expression":
                bind(left, n)  # `x += e` == `x = x + e`: rebind x to the result (Python parity)
            return n
        if t in ("unary_expression", "update_expression"):
            op = _op_text(node, text)
            arg = node.child_by_field_name("argument")
            n = g.add("UNARY:" + op)
            g.link(ev(arg, ctrl), n, _DATA)
            if t == "update_expression":
                bind(arg, n)  # `x++` / `--x` rebinds x to the updated value, like x = x + 1
            return n
        if t == "ternary_expression":
            n = g.add("IFEXP")
            g.link(ev(node.child_by_field_name("condition"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("consequence"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("alternative"), ctrl), n, _DATA)
            return n
        if t == "assignment_expression":
            val = ev(node.child_by_field_name("right"), ctrl)
            bind(node.child_by_field_name("left"), val)
            return val
        if t in ("array", "array_pattern"):
            n = g.add("SEQ")
            for e in node.named_children:
                g.link(ev(e, ctrl), n, _DATA)
            return n
        if t in ("object", "object_pattern"):
            n = g.add("DICT")
            for prop in node.named_children:
                if prop.type == "pair":
                    g.link(ev(prop.child_by_field_name("key"), ctrl), n, _DATA)
                    g.link(ev(prop.child_by_field_name("value"), ctrl), n, _DATA)
                else:
                    g.link(ev(prop, ctrl), n, _DATA)
            return n
        if t in ("await_expression", "yield_expression", "spread_element"):
            inner = [c for c in node.named_children]
            return ev(inner[-1], ctrl) if inner else g.add(t.split("_")[0].upper())
        if t in _FUNC_NODES:
            return g.add("NESTED")
        # generic fallback: a node fed by its sub-expressions (so a construct not yet handled can
        # never silently vanish from the fingerprint — the completeness oracle makes gaps visible).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def do(node, ctrl: int | None) -> None:
        t = node.type
        if t in ("variable_declaration", "lexical_declaration"):
            for decl in node.named_children:
                if decl.type == "variable_declarator":
                    val = ev(decl.child_by_field_name("value"), ctrl)
                    name = decl.child_by_field_name("name")
                    if name is not None:
                        bind(name, val)
        elif t == "expression_statement":
            for c in node.named_children:
                ev(c, ctrl)
        elif t == "return_statement":
            n = g.add("RETURN")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
        elif t == "throw_statement":
            n = g.add("RAISE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
        elif t in ("if_statement",):
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("condition"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            _do_body(node.child_by_field_name("consequence"), b)
            _do_body(node.child_by_field_name("alternative"), b)
        elif t in ("for_statement", "for_in_statement", "while_statement", "do_statement"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            left = node.child_by_field_name("left")   # for-in/of binding
            right = node.child_by_field_name("right")
            if right is not None and left is not None:
                it = g.add("ITERVAR")
                g.link(ev(right, loop), it, _DATA)
                bind(left, it)
            for fld in ("condition", "initializer", "increment"):
                sub = node.child_by_field_name(fld)
                if sub is not None:
                    ev(_strip(sub), loop)
            _do_body(node.child_by_field_name("body"), loop)
        elif t == "switch_statement":
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("value"), ctrl), b, _DATA)
            body = node.child_by_field_name("body")
            if body is not None:
                for case in body.named_children:
                    c = g.add("CASE")
                    val = case.child_by_field_name("value")
                    g.link(ev(val, b), c, _DATA)
                    # the case value is also a named child; skip it so it isn't re-walked as a body
                    # statement. Compare by BYTE SPAN, not Python `is`: tree-sitter returns a fresh
                    # wrapper object per call, so `st is val` is never true and the value would be
                    # double-walked (spurious nodes for `case g():` — R172/R173 sonnet).
                    vspan = (val.start_byte, val.end_byte) if val is not None else None
                    for st in case.named_children:
                        if (st.start_byte, st.end_byte) != vspan:
                            do(st, c)
        elif t == "try_statement":
            for fld in ("body", "handler", "finalizer"):
                _do_body(node.child_by_field_name(fld), ctrl)
        elif t in _FUNC_NODES:
            g.add("NESTED")
        elif t in ("statement_block",):
            _do_body(node, ctrl)
        else:
            # generic fallback for an unhandled statement: walk sub-statements and sub-expressions.
            for ch in node.named_children:
                if ch.type.endswith("statement") or ch.type == "statement_block":
                    do(ch, ctrl)
                else:
                    ev(ch, ctrl)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type == "statement_block":
            for st in node.named_children:
                do(st, ctrl)
        else:
            do(node, ctrl)

    def _strip(node):
        # for-loop clauses wrap their expression; descend to the payload.
        while node is not None and node.type in _TRANSPARENT and node.named_children:
            node = node.named_children[0 if node.type in _CAST_OPERAND_FIRST else -1]
        return node

    body = fn.child_by_field_name("body")
    if body is not None:
        if body.type == "statement_block":
            for st in body.named_children:
                do(st, None)
        else:  # arrow function with an expression body: `(x) => x + 1`
            n = g.add("RETURN")
            g.link(ev(body, None), n, _DATA)
    return g


def _param_names(node, text) -> list[str]:
    t = node.type
    if t == "identifier":
        return [text(node)]
    if t in ("required_parameter", "optional_parameter"):  # TS-typed params wrap the pattern
        pat = node.child_by_field_name("pattern")
        return _param_names(pat, text) if pat is not None else []
    if t == "assignment_pattern":
        left = node.child_by_field_name("left")
        return _param_names(left, text) if left is not None else []
    if t in ("rest_pattern", "rest_element"):
        return [n for c in node.named_children for n in _param_names(c, text)]
    if t in ("array_pattern", "object_pattern"):
        return [n for c in node.named_children for n in _param_names(c, text)]
    if t in ("shorthand_property_identifier_pattern", "shorthand_property_identifier"):
        return [text(node)]
    if t == "pair_pattern":
        v = node.child_by_field_name("value")
        return _param_names(v, text) if v is not None else []
    return []


def _op_text(node, text) -> str:
    op = node.child_by_field_name("operator")
    if op is not None:
        return op.text.decode("utf-8", "replace")
    # operator isn't a named field on every grammar version — scan anonymous children.
    for c in node.children:
        if not c.is_named and c.text:
            return c.text.decode("utf-8", "replace")
    return "?"
