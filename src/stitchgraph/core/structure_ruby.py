"""Structural (body-level) fingerprints for Ruby methods.

The Ruby frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So Ruby↔Ruby bodies compare exactly the way every
other per-language frontend's do (one shared WL kernel).

Ruby specifics: methods are keyed by the dotted chain of enclosing module/class names + the method
name — `M.Calc.compute`, `M.top` (a module-level `def self.top`), bare `free_fn` for a top-level def —
matching the extractor (modules/classes ARE part of the key; a `def self.x` singleton method keys by
its bare name like an instance method). Ruby is **expression-oriented**: a method body's trailing
expression is its value (`{ x }` ≡ an explicit `return x`), like Rust. Assignment is an expression;
compound `+=`/`<<=` normalises to the base operator + a rebind. String interpolation `"#{e}"` carries
value flow through its holes (like an f-string). A block / `do…end` passed to a call is an opaque
`NESTED` leaf (a closure, like lambdas in the other frontends). It is a structural approximation, NOT
sound data flow (no alias analysis, constants collapsed). The method is in
`docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _wl_features

_EXTS = {".rb": "ruby"}

# Function-like nodes whose bodies are fingerprinted. A block, when attached to a call, is opaque.
_FUNC_NODES = frozenset({"method", "singleton_method", "block", "do_block", "lambda"})

# Type/namespace declarations that contribute a `Name.` segment to the qualname.
_TYPE_NODES = frozenset({"module", "class", "singleton_class"})

# Leaf literals — one CONST node regardless of value. (A `string` is NOT here: its `#{…}` holes carry
# value flow and are walked explicitly.)
_CONST = frozenset({
    "integer", "float", "complex", "rational", "true", "false", "nil",
    "simple_symbol", "hash_key_symbol", "character", "bare_symbol",
})


def _parser():
    """A tree-sitter Ruby parser, or None if the extra isn't installed."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar("ruby"))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def fingerprint_source(source: str, lang: str = "ruby") -> dict[str, collections.Counter[str]]:
    """Fingerprint every method in a Ruby source string, keyed by the extractor's scheme (`M.Calc.m`,
    module-level `M.top`, bare `free_fn`). Returns {} on a parse failure, a missing tree-sitter extra,
    or a too-deep tree (advisory, never raises)."""
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
            if t in ("method", "singleton_method"):
                nm = child.child_by_field_name("name")
                if nm is not None:
                    emit(prefix + text(nm), child)
                # nested defs inside a method body are opaque — don't recurse for more keys.
            elif t in _TYPE_NODES:
                nm = child.child_by_field_name("name")
                body = child.child_by_field_name("body")
                seg = (text(nm) + ".") if nm is not None else ""
                visit(body if body is not None else child, prefix + seg)
            else:
                visit(child, prefix)

    try:
        visit(tree.root_node, "")
    except (RecursionError, ValueError):
        return out
    return out


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one Ruby method/block node into a value-flow graph, mirroring
    `structure._build_vfg`: PARAM seeds, copy propagation, operations + control points as nodes,
    data/control edges. Expression-oriented — a body's trailing expression is its return value."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    # seed parameters.
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            nm = p if p.type == "identifier" else p.child_by_field_name("name")
            if nm is None and p.named_children:
                nm = p.named_children[0]
            if nm is not None and nm.type == "identifier":
                env[text(nm)] = g.add("PARAM")

    def bind(target, val: int | None) -> None:
        if target is None:
            return
        t = target.type
        if t in ("identifier", "instance_variable", "class_variable", "global_variable", "constant"):
            name = text(target)
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)
        elif t == "element_reference":
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("object"), None), n, _DATA)
            for c in target.named_children:
                if (target.field_name_for_named_child(target.named_children.index(c)) != "object"):
                    g.link(ev(c, None), n, _DATA)
        elif t == "call":  # attribute write `obj.attr = v`
            n = g.add("SETATTR")
            g.link(val, n, _DATA)
            g.link(ev(target.child_by_field_name("receiver"), None), n, _DATA)

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t in ("identifier", "constant", "global_variable", "class_variable"):
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t in ("self", "instance_variable"):
            return freevar(text(node))
        if t in _CONST:
            return g.add("CONST")
        if t == "string" or t == "subshell" or t == "heredoc_beginning":
            # a string carries flow through its `#{…}` interpolation holes; plain text collapses.
            n = g.add("CONST")
            for c in node.named_children:
                if c.type == "interpolation":
                    for ic in c.named_children:
                        g.link(ev(ic, ctrl), n, _DATA)
            return n
        if t in ("array", "hash", "subshell"):
            n = g.add("COMPOSITE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "pair":
            n = g.add("PAIR")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "parenthesized_statements" or t == "begin":
            # A multi-statement group in value position: walk every statement for its flow (not just
            # the trailing one) and walk any rescue/else/ensure clauses — `_do_body` does both and
            # returns the trailing main value, which is this group's value.
            return _do_body(node, ctrl)
        if t == "element_reference":
            n = g.add("SUBSCRIPT")
            objf = node.child_by_field_name("object")
            g.link(ev(objf, ctrl), n, _DATA)
            for i, c in enumerate(node.named_children):
                if node.field_name_for_named_child(i) != "object":
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "call" or t == "method_call":
            blk = node.child_by_field_name("block")
            if blk is None:
                for c in node.named_children:
                    if c.type in ("block", "do_block"):
                        blk = c
            recv = node.child_by_field_name("receiver")
            meth = node.child_by_field_name("method")
            args = node.child_by_field_name("arguments")
            if args is None and meth is None and recv is not None:
                # `obj.attr` with no args/parens — an attribute read.
                n = g.add("ATTR")
                g.link(ev(recv, ctrl), n, _DATA)
                g.link(ctrl, n, _CTRL)
                return n
            n = g.add("CALL")
            g.link(ev(recv, ctrl), n, _DATA)
            if args is not None:
                for a in args.named_children:
                    g.link(ev(a, ctrl), n, _DATA)
            if blk is not None:
                g.link(g.add("NESTED"), n, _DATA)  # a block/do-end is an opaque closure
            g.link(ctrl, n, _CTRL)
            return n
        if t == "binary":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("unary", "splat_argument", "block_argument", "hash_splat_argument"):
            n = g.add("UNARY")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "range":
            n = g.add("RANGE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "assignment":
            val = ev(node.child_by_field_name("right"), ctrl)
            bind(node.child_by_field_name("left"), val)
            return val
        if t == "operator_assignment":
            op = _op_text(node, text)
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            base = op[:-1] if op.endswith("=") else op
            n = g.add("BINOP:" + base)
            g.link(ev(left, ctrl), n, _DATA)
            g.link(ev(right, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            bind(left, n)
            return n
        if t == "conditional":  # ternary `a ? b : c`
            n = g.add("IFEXP")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("if", "unless", "case", "while", "until", "for", "if_modifier",
                 "unless_modifier", "while_modifier", "until_modifier", "begin", "return", "yield",
                 "break", "next"):
            return _do(node, ctrl)  # statement-ish in expression position; value via the handler
        if t in _FUNC_NODES:
            return g.add("NESTED")
        # generic fallback: a node fed by its sub-expressions (the completeness oracle makes gaps
        # visible, so an unhandled construct can never silently vanish from the fingerprint).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def _do(node, ctrl: int | None) -> int | None:
        t = node.type
        if t in ("if", "unless", "elsif", "if_modifier", "unless_modifier"):
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("condition"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            _do_body(node.child_by_field_name("consequence"), b)
            _do_body(node.child_by_field_name("body"), b)
            _do_body(node.child_by_field_name("alternative"), b)
            return b
        if t in ("then", "else", "ensure"):
            _do_body(node, ctrl)
            return None
        if t == "case":
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("value"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            for c in node.named_children:
                if c.type in ("when", "in_clause"):
                    pat = c.child_by_field_name("pattern")
                    if pat is not None:
                        ev(pat, b)
                    _do_body(c.child_by_field_name("body"), b)
                elif c.type == "else":
                    _do_body(c, b)
            return b
        if t in ("while", "until", "for", "while_modifier", "until_modifier"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            cond = node.child_by_field_name("condition")
            if cond is not None:
                ev(cond, loop)
            if t == "for":
                it = g.add("ITERVAR")
                g.link(ev(node.child_by_field_name("value"), loop), it, _DATA)
                bind(node.child_by_field_name("pattern"), it)
            _do_body(node.child_by_field_name("body"), loop)
            return loop
        if t in ("return", "yield", "break", "next"):
            n = g.add("RETURN" if t in ("return", "next") else "YIELD")
            for c in node.named_children:
                if c.type == "argument_list":
                    for a in c.named_children:
                        g.link(ev(a, ctrl), n, _DATA)
                else:
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("begin",):
            _do_body(node, ctrl)
            return None
        return ev(node, ctrl)

    def _do_body(node, ctrl: int | None, *, as_value: bool = False) -> int | None:
        """Walk a body_statement / then / else block; return the trailing expression's value."""
        if node is None:
            return None
        if node.type in ("body_statement", "then", "else", "do", "block_body", "ensure",
                         "begin", "parenthesized_statements"):
            kids = [c for c in node.named_children
                    if c.type not in ("rescue", "ensure", "else")]
            last = None
            for st in kids:
                last = _do(st, ctrl)
            # also walk rescue/else/ensure clauses for their flow (the `else` of a begin/rescue runs
            # when no exception fired — its body carries value flow just like the main statements).
            for c in node.named_children:
                if c.type in ("rescue", "else", "ensure"):
                    _do_body(c.child_by_field_name("body") or c, ctrl)
            return last
        return _do(node, ctrl)

    body = fn.child_by_field_name("body")
    if body is not None:
        ret = _do_body(body, None)
        if ret is not None:  # expression-oriented: the trailing expression is the implicit return
            n = g.add("RETURN")
            g.link(ret, n, _DATA)
    return g


def _op_text(node, text) -> str:
    op = node.child_by_field_name("operator")
    if op is not None:
        return op.text.decode("utf-8", "replace")
    for c in node.children:
        if not c.is_named and c.text:
            return c.text.decode("utf-8", "replace")
    return "?"
