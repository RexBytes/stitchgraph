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
    return make_parser("ruby")


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _walk(source, lang, build):
    """Shared traversal for `fingerprint_source` / `vfg_source`: apply `build(fn_node, data)` per method."""
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


def fingerprint_source(source: str, lang: str = "ruby") -> dict[str, collections.Counter[str]]:
    return _walk(source, lang, lambda fn, data: _wl_features(_build_vfg(fn, data)))


def vfg_source(source: str, lang: str = "ruby") -> dict[str, tuple[list[str], list]]:
    """Value-flow graph of every method — EXPRESSION-layer companion to fingerprint_source (identical keys). Advisory, on demand."""
    return _walk(source, lang, lambda fn, data: _serialize_vfg(_build_vfg(fn, data)))


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one Ruby method/block node into a value-flow graph, mirroring
    `structure._build_vfg`: PARAM seeds, copy propagation, operations + control points as nodes,
    data/control edges. Expression-oriented — a body's trailing expression is its return value."""
    g, env, free, freevar = vfg_state()
    text = node_text

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
        if t == "comment":  # trivia: a comment must never alter the value-flow fingerprint
            return None
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
        if t == "comment":  # trivia
            return None
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
                    # `pattern` is a REPEATED field: `when 1, helper()` exposes one `pattern` child
                    # per comma-separated value, so child_by_field_name (first-only) would drop all
                    # but the first. Walk every `pattern`-named child.
                    for i in range(c.named_child_count):
                        if c.field_name_for_named_child(i) == "pattern":
                            ev(c.named_children[i], b)
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
                    if c.type not in ("rescue", "ensure", "else", "comment")]
            last = None
            for st in kids:
                last = _do(st, ctrl)
            # also walk rescue/else/ensure clauses for their flow (the `else` of a begin/rescue runs
            # when no exception fired — its body carries value flow just like the main statements).
            for c in node.named_children:
                if c.type in ("rescue", "else", "ensure"):
                    if c.type == "rescue":
                        # `rescue <expr>, <expr> => e` — the exception-class selectors are evaluated
                        # expressions (value flow), reached via the `exceptions` field, not the body.
                        exc = c.child_by_field_name("exceptions")
                        for ec in (exc.named_children if exc is not None else []):
                            ev(ec, ctrl)
                    _do_body(c.child_by_field_name("body") or c, ctrl)
            return last
        return _do(node, ctrl)

    # A parameter's default-value expression carries flow (`def f(b = helper())`): walk it now that
    # `ev` is defined and link it into the parameter's PARAM node.
    if params is not None:
        for p in params.named_children:
            val = p.child_by_field_name("value")
            nm = p.child_by_field_name("name")
            if val is not None and nm is not None and nm.type == "identifier" and text(nm) in env:
                g.link(ev(val, None), env[text(nm)], _DATA)

    body = fn.child_by_field_name("body")
    if body is not None:
        ret = _do_body(body, None)
        if ret is not None:  # expression-oriented: the trailing expression is the implicit return
            n = g.add("RETURN")
            g.link(ret, n, _DATA)
    return g


def _nc(node):
    return nc(node)


def _first(node):
    return first(node)


def _last(node):
    return last(node)


# --- STATEMENT layer (PDG) — design §5c sweep, Ruby ----------------------------------------------

_PDG_STMT_LABEL = {
    "assignment": "Assign", "operator_assignment": "Assign", "call": "Call", "method_call": "Call",
    "if": "If", "unless": "If", "if_modifier": "If", "unless_modifier": "If", "elsif": "If",
    "case": "Case", "case_match": "Case", "while": "While", "until": "While",
    "while_modifier": "While", "until_modifier": "While", "for": "For", "begin": "Begin",
    "return": "Return", "yield": "Yield", "break": "Break", "next": "Next", "redo": "Redo",
    "retry": "Retry", "binary": "Expr", "identifier": "Expr",
}
# control constructs that become their own PDG node in STATEMENT position (and fold in VALUE position)
_CONTROL = frozenset({
    "if", "unless", "if_modifier", "unless_modifier", "case", "case_match", "while", "until",
    "while_modifier", "until_modifier", "for", "begin",
})
# env-looked-up value names in the VFG's `ev` — the only ones that can BE the single free param `v`;
# `self`/`instance_variable` route through `freevar` there (never a param), so they are not reads.
_READ_NAMES = frozenset({"identifier", "constant", "global_variable", "class_variable"})


def _pdg_label(t: str) -> str:
    return _PDG_STMT_LABEL.get(t) or "".join(w.capitalize() for w in t.split("_")) or "Stmt"


def pdg_source(source: str, lang: str = "ruby") -> dict[str, tuple[list[str], list]]:
    """Program-dependence graph of every method — the STATEMENT-layer companion to
    fingerprint_source/vfg_source (identical keys), the raw graph get_matrix(layer="statement")
    drills into. Statement nodes + control ('C') / data ('D') dependence edges via a sequential
    reaching-def approximation; blocks / do-end / lambdas are opaque NESTED leaves. Advisory, on
    demand.

    Ruby is EXPRESSION-oriented (like Rust): control constructs (`if`/`case`/`while`/`for`) become
    control nodes in STATEMENT position but FOLD their reads into the enclosing statement in VALUE
    position (`x = if c then a else b end`). Accepted layer-level under-approximations, all symmetric
    (shared by BOTH the PDG and the VFG, so no VFG/PDG divergence): block/do-end/lambda bodies are
    opaque, and a `for`/rescue binding is modelled as a store but its pattern is not deep-destructured.
    Method-parameter default-value expressions are read by the VFG but not seeded here (the one
    accepted asymmetry, shared by every sibling PDG)."""
    return _walk(source, lang, lambda fn, data: _build_pdg(fn, data))


def _build_pdg(fn, data: bytes) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer for a Ruby method — a program-dependence graph mirroring
    `structure._build_pdg` (Python) and the JS-family/Go/Rust/C++/Java/C# PDG builders: statement
    nodes + a synthetic ENTRY carrying the parameters, control ('C') / data ('D', sequential
    reaching-def) edges. Ruby is expression-oriented, so the read/write projection (`collect`) folds
    value-position control; blocks/lambdas are opaque NESTED leaves; reorder-invariant. A structural
    approximation (no SSA/alias analysis), advisory only — never feeds liveness. The projection reads
    ONLY genuine value operands and records ONLY genuine bindings, matching the VFG's `ev`/`bind`: a
    call's method NAME, `self`/`@ivar`, and rescue/for pattern names are never read as values."""
    nodes, edges, last_def, new_id, data_from = pdg_state()
    text = node_text

    entry = new_id("ENTRY")
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            nm = p if p.type == "identifier" else p.child_by_field_name("name")
            if nm is None and p.named_children:
                nm = p.named_children[0]
            if nm is not None and nm.type == "identifier":
                last_def[text(nm)] = entry

    def bind_place(target, loads: set, stores: set) -> None:
        """An assignment target. A plain name defines it (a STORE); an index/attribute place
        (`a[i] = …`, `obj.attr = …`) defines no name — its object/index are READS (mirrors `bind`).
        A `left_assignment_list` (`a, b = …`) deconstructs — each element binds."""
        if target is None:
            return
        t = target.type
        if t in _READ_NAMES or t == "instance_variable":
            stores.add(text(target))
        elif t in ("left_assignment_list", "destructured_left_assignment"):
            for c in target.named_children:
                bind_place(c, loads, stores)
        elif t == "rest_assignment":
            for c in target.named_children:
                bind_place(c, loads, stores)
        else:  # element_reference / call attribute-write: no name — read the object/index operands
            collect(target, loads, stores)

    def collect(n, loads: set, stores: set) -> None:
        """Reads/writes within one statement — mirrors the VFG's `ev`. Value-position control folds
        (its condition + branch bodies' reads accumulate here). Stops at blocks/lambdas (opaque)."""
        if n is None:
            return
        t = n.type
        if t in _FUNC_NODES or t == "comment":
            return  # a block / do-end / lambda is an opaque closure (matches the VFG's NESTED leaf)
        if t in _READ_NAMES:
            loads.add(text(n))
            return
        if t in ("self", "instance_variable") or t in _CONST:
            return  # self/@ivar route through freevar in the VFG — never the single free param
        if t in ("string", "subshell", "heredoc_beginning"):
            for c in n.named_children:
                if c.type == "interpolation":
                    for ic in c.named_children:
                        collect(ic, loads, stores)
            return
        if t == "call" or t == "method_call":
            collect(n.child_by_field_name("receiver"), loads, stores)  # method NAME + block: not reads
            args = n.child_by_field_name("arguments")
            if args is not None:
                for a in args.named_children:
                    collect(a, loads, stores)
            return
        if t == "element_reference":
            for c in n.named_children:
                collect(c, loads, stores)  # object + index operands are all reads
            return
        if t == "assignment":
            bind_place(n.child_by_field_name("left"), loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t == "operator_assignment":  # `x += e` reads AND writes the left operand
            left = n.child_by_field_name("left")
            if left is not None and left.type in _READ_NAMES:
                stores.add(text(left))
                loads.add(text(left))
            else:
                collect(left, loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t in ("parenthesized_statements", "begin"):
            _collect_body(n, loads, stores)
            return
        if t in ("if", "unless", "if_modifier", "unless_modifier"):
            collect(n.child_by_field_name("condition"), loads, stores)
            _collect_body(n.child_by_field_name("consequence"), loads, stores)
            _collect_body(n.child_by_field_name("body"), loads, stores)
            _collect_body(n.child_by_field_name("alternative"), loads, stores)
            return
        if t in ("case", "case_match"):
            collect(n.child_by_field_name("value"), loads, stores)
            for c in n.named_children:
                if c.type in ("when", "in_clause"):
                    for i in range(c.named_child_count):
                        if c.field_name_for_named_child(i) == "pattern":
                            collect(c.named_children[i], loads, stores)
                    collect(c.child_by_field_name("guard"), loads, stores)  # `in P if <cond>` guard
                    _collect_body(c.child_by_field_name("body"), loads, stores)
                elif c.type == "else":
                    _collect_body(c, loads, stores)
            return
        if t in ("while", "until", "while_modifier", "until_modifier"):
            collect(n.child_by_field_name("condition"), loads, stores)
            _collect_body(n.child_by_field_name("body"), loads, stores)
            return
        if t == "for":
            collect(n.child_by_field_name("value"), loads, stores)  # iterated collection is a read
            bind_place(n.child_by_field_name("pattern"), loads, stores)  # loop var binds
            _collect_body(n.child_by_field_name("body"), loads, stores)
            return
        if t in ("return", "yield", "break", "next"):
            for c in n.named_children:
                if c.type == "argument_list":
                    for a in c.named_children:
                        collect(a, loads, stores)
                else:
                    collect(c, loads, stores)
            return
        for c in n.named_children:
            collect(c, loads, stores)

    def _collect_body(node, loads: set, stores: set) -> None:
        """Fold a value-position body's statement reads into the enclosing header."""
        if node is None:
            return
        if node.type in ("body_statement", "then", "else", "do", "block_body", "ensure",
                         "begin", "parenthesized_statements"):
            for c in node.named_children:
                if c.type == "rescue":
                    exc = c.child_by_field_name("exceptions")
                    for ec in (exc.named_children if exc is not None else []):
                        collect(ec, loads, stores)
                    _collect_body(c.child_by_field_name("body"), loads, stores)
                elif c.type in ("else", "ensure"):
                    _collect_body(c.child_by_field_name("body") or c, loads, stores)
                elif c.type != "comment":
                    collect(c, loads, stores)
        else:
            collect(node, loads, stores)

    def data_edges(hdr, sid: int) -> None:
        if hdr is None:
            return
        loads: set = set()
        stores: set = set()
        collect(hdr, loads, stores)
        data_from(loads, stores, sid)

    def bind_target(target, sid: int) -> None:
        st: set = set()
        bind_place(target, set(), st)
        for nm in sorted(st):
            last_def[nm] = sid

    def walk_body(node, parent: int) -> None:
        if node is None or node.type == "comment":
            return
        if node.type in ("body_statement", "then", "else", "do", "block_body", "ensure", "begin",
                         "parenthesized_statements"):
            for st in node.named_children:
                if st.type in ("rescue", "ensure", "else", "comment"):
                    continue
                process(st, parent)
            for c in node.named_children:
                if c.type == "rescue":
                    sid = new_id("Rescue")
                    edges.append((parent, sid, "C"))
                    exc = c.child_by_field_name("exceptions")
                    for ec in (exc.named_children if exc is not None else []):
                        data_edges(ec, sid)
                    walk_body(c.child_by_field_name("body"), parent)
                elif c.type in ("else", "ensure"):
                    walk_body(c.child_by_field_name("body") or c, parent)
        else:
            process(node, parent)

    def process(node, parent: int) -> None:
        t = node.type
        if t == "comment":
            return
        if t in _FUNC_NODES:
            edges.append((parent, new_id("NESTED"), "C"))
            return
        if t not in _CONTROL:
            # a simple statement (assignment / call / trailing expression / return / …): the whole
            # node is its header; `collect` folds any value-position control and stops at blocks.
            sid = new_id(_pdg_label(t))
            edges.append((parent, sid, "C"))
            data_edges(node, sid)
            return
        sid = new_id(_pdg_label(t))
        edges.append((parent, sid, "C"))
        if t in ("if", "unless", "if_modifier", "unless_modifier"):
            data_edges(node.child_by_field_name("condition"), sid)
            walk_body(node.child_by_field_name("consequence"), sid)
            walk_body(node.child_by_field_name("body"), sid)
            walk_body(node.child_by_field_name("alternative"), sid)
        elif t in ("case", "case_match"):
            data_edges(node.child_by_field_name("value"), sid)
            for c in node.named_children:
                if c.type in ("when", "in_clause"):
                    for i in range(c.named_child_count):
                        if c.field_name_for_named_child(i) == "pattern":
                            data_edges(c.named_children[i], sid)  # selectors read on the header
                    # `in <pattern> if/unless <cond>` — the guard condition is an executed read.
                    data_edges(c.child_by_field_name("guard"), sid)
                    walk_body(c.child_by_field_name("body"), sid)
                elif c.type == "else":
                    walk_body(c, sid)
        elif t in ("while", "until", "while_modifier", "until_modifier"):
            data_edges(node.child_by_field_name("condition"), sid)
            walk_body(node.child_by_field_name("body"), sid)
        elif t == "for":
            data_edges(node.child_by_field_name("value"), sid)  # the iterated collection is a read
            bind_target(node.child_by_field_name("pattern"), sid)  # the loop var binds
            walk_body(node.child_by_field_name("body"), sid)
        elif t == "begin":
            walk_body(node, sid)

    body = fn.child_by_field_name("body")
    walk_body(body, entry)
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges


def _op_text(node, text) -> str:
    return op_text(node)
