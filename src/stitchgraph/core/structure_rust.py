"""Structural (body-level) fingerprints for Rust functions and methods.

The Rust frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So Rust↔Rust bodies compare exactly the way every other per-language frontend's do (one shared WL kernel).

Advisory and read-only, like the other frontends — it never feeds `find_stale`, so the cardinal rule
does not apply. Requires the optional tree-sitter extra; with it absent every entry point returns
`{}`. Cross-language comparison is oracle-only (topology tracks the extractor); callers rank within
one language (see `similar.find_similar_structure`).

Rust specifics handled: blocks are expression-oriented — a block's **trailing expression** (no
semicolon) is its value, so `{ x }` fingerprints like `{ return x; }`. `if`/`match`/`loop`/`while`/
`for` are expressions. The `?` operator, references (`&x`), `as` casts, ranges, tuples, and struct
literals carry their operand's value flow; the asserted/cast type carries none. Macro invocations
(`vec![…]`, `println!(…)`) expose their arguments as a raw **token tree**, not parsed expressions —
we walk the tree's identifier/literal tokens best-effort so a variable passed to a macro still
threads value flow. Closures (`|x| …`) are opaque `NESTED` leaves (matching nested-function handling
in the other frontends and the Rust extractor's node granularity).

Qualname scheme: free functions are bare (`free_fn`); methods in an `impl T { … }` (or `impl Trait
for T`) block are `T.method` — the same scheme the Rust extractor produces. Nested closures are not
keys. It is a structural approximation, NOT sound data flow (no SSA/borrow/lifetime analysis,
constants collapsed). The bug taxonomy and oracle method are in `docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _serialize_vfg, _wl_features

_EXTS = {".rs": "rust"}

# Function-like nodes whose bodies are fingerprinted. A closure, when nested, is an opaque leaf.
_FUNC_NODES = frozenset({"function_item", "closure_expression"})

# Leaf literals — one CONST node regardless of value.
_CONST = frozenset({
    "integer_literal", "float_literal", "string_literal", "raw_string_literal", "char_literal",
    "boolean_literal", "unit_expression",
})

# Statement node kinds inside a block (everything else trailing is the block's value expression).
_STMT_NODES = frozenset({"let_declaration", "expression_statement", "empty_statement"})
# Item declarations that may appear in a block — opaque to the enclosing body's value flow.
_ITEM_NODES = frozenset({
    "function_item", "struct_item", "enum_item", "impl_item", "trait_item", "mod_item",
    "const_item", "static_item", "type_item", "use_declaration", "macro_definition",
})


def _parser(lang: str = "rust"):
    """A tree-sitter Rust parser, or None if the extra isn't installed (advisory degrade)."""
    try:
        from tree_sitter import Parser

        from .extract.treesitter import _load_grammar
        return Parser(_load_grammar("rust"))
    except Exception:  # noqa: BLE001 — no extra / no grammar -> the body layer adds nothing
        return None


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _walk(source: str, lang: str = "rust", *, build):
    """Shared traversal applying build(<fn_node>, data) per function."""
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
            if t == "function_item":
                nm = child.child_by_field_name("name")
                emit(prefix + (text(nm) if nm is not None else ""), child)
                # do NOT descend into the body for more keys — nested items/closures are opaque,
                # matching the Rust extractor's granularity (top-level fns + impl methods).
            elif t == "impl_item":
                typ = child.child_by_field_name("type")
                tname = text(typ) if typ is not None else ""
                body = child.child_by_field_name("body")
                if body is not None:
                    visit(body, prefix + tname + "." if tname else prefix)
            elif t in ("mod_item", "trait_item"):
                body = child.child_by_field_name("body")
                visit(body if body is not None else child, prefix)
            else:
                visit(child, prefix)

    try:
        visit(tree.root_node, "")
    except (RecursionError, ValueError):
        return out
    return out


def fingerprint_source(source: str, lang: str = "rust") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a Rust source string, keyed by qualified name (`free_fn`,
    `Type.method`) — the same scheme the tree-sitter Rust extractor produces. Returns {} on a parse
    failure, a missing tree-sitter extra, or a too-deep tree (advisory, never raises)."""
    return _walk(source, lang, build=lambda fn, data: _wl_features(_build_vfg(fn, data)))


def vfg_source(source: str, lang: str = "rust") -> dict[str, tuple[list[str], list]]:
    """Value-flow graph of every function/method — EXPRESSION-layer companion to fingerprint_source
    (identical keys). Advisory, on demand."""
    return _walk(source, lang, build=lambda fn, data: _serialize_vfg(_build_vfg(fn, data)))


def pdg_source(source: str, lang: str = "rust") -> dict[str, tuple[list[str], list]]:
    """Program-dependence graph of every function/method — the STATEMENT-layer companion to
    fingerprint_source/vfg_source (identical keys), the raw graph get_matrix(layer="statement")
    drills into. Statement nodes + control ('C') / data ('D') dependence edges via a sequential
    reaching-def approximation; nested functions/closures are opaque NESTED leaves. Rust is
    expression-oriented, so control-flow expressions (if/match/loop/while/for) in statement position
    become control nodes; in value position (e.g. `let y = if …`) they are folded into the enclosing
    statement's reads. Advisory, on demand."""
    return _walk(source, lang, build=lambda fn, data: _build_pdg(fn, data))


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one Rust function/closure node into a value-flow graph, mirroring
    `structure._build_vfg` for Python: PARAM seeds (incl. `self`), copy propagation through `let`
    bindings, operations and control points as nodes, data/control edges. Rust blocks return their
    trailing expression."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    # seed parameters (and `self`).
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            if p.type == "self_parameter":
                env["self"] = g.add("PARAM")
            elif p.type == "parameter":
                pat = p.child_by_field_name("pattern")
                for name in _pattern_names(pat, text):
                    env[name] = g.add("PARAM")
            else:
                for name in _pattern_names(p, text):
                    env[name] = g.add("PARAM")

    def bind(target, val: int | None) -> None:
        if target is None:
            return
        t = target.type
        if t == "identifier":
            name = text(target)
            if name == "_":
                return
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)
        elif t in ("mut_pattern", "ref_pattern", "reference_pattern"):
            for c in target.named_children:
                bind(c, val)
        elif t in ("tuple_pattern", "tuple_struct_pattern", "slice_pattern", "struct_pattern"):
            for c in target.named_children:
                bind(c, val)
        elif t == "field_pattern":
            inner = target.child_by_field_name("pattern")
            bind(inner if inner is not None else _last(target), val)
        elif t in ("field_expression", "index_expression"):
            n = g.add("SETATTR" if t == "field_expression" else "SETITEM")
            g.link(val, n, _DATA)
            kids = _nc(target)
            obj = target.child_by_field_name("value") or (kids[0] if kids else None)
            g.link(ev(obj, None), n, _DATA)
            if t == "index_expression" and len(kids) > 1:
                g.link(ev(kids[1], None), n, _DATA)  # index carries flow
        elif t in ("unary_expression", "reference_expression"):  # *p = v / deref write
            inner = target.child_by_field_name("value") or _last(target)
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(inner, None), n, _DATA)
        # identifier patterns inside e.g. a `let Some(x) = ...` bind x to the scrutinee value
        elif target.named_children:
            for c in target.named_children:
                if c.type == "identifier":
                    bind(c, val)

    def _walk_block(block, ctrl: int | None, as_value: bool):
        """Execute a block's statements; return the trailing expression's value if as_value."""
        kids = [c for c in block.named_children
                if c.type not in ("line_comment", "block_comment")]  # comments are trivia
        result = None
        for i, c in enumerate(kids):
            is_last = i == len(kids) - 1
            if c.type in _STMT_NODES:
                do(c, ctrl)
            elif c.type in ("const_item", "static_item"):
                # A function-local `const`/`static X = <expr>;` carries an initializer evaluated in
                # the enclosing body (e.g. a `const fn` call) — walk it and bind the name, unlike the
                # other opaque items below. (The type position still carries no flow.)
                val = ev(c.child_by_field_name("value"), ctrl)
                bind(c.child_by_field_name("name"), val)
            elif c.type in _ITEM_NODES:
                continue  # nested item: opaque to the enclosing body's value flow
            elif is_last:
                result = ev(c, ctrl)  # the block's trailing value expression
            else:
                ev(c, ctrl)  # a bare expression mid-block (rare without ';')
        return result if as_value else None

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t in ("line_comment", "block_comment"):  # trivia: never alters the fingerprint
            return None
        if t in ("identifier", "field_identifier", "type_identifier", "shorthand_field_identifier"):
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t in ("self", "super", "crate"):
            return freevar(t)
        if t in _CONST:
            return g.add("CONST")
        if t == "scoped_identifier":  # `Foo::bar`, `mod::CONST` — a name reference
            return freevar(text(node))
        if t == "block":
            return _walk_block(node, ctrl, as_value=True)
        if t == "parenthesized_expression":
            return ev(_last(node), ctrl)
        if t == "field_expression":
            n = g.add("ATTR")
            g.link(ev(node.child_by_field_name("value"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "index_expression":
            kids = _nc(node)
            n = g.add("SUBSCRIPT")
            if kids:
                g.link(ev(kids[0], ctrl), n, _DATA)
            if len(kids) > 1:
                g.link(ev(kids[1], ctrl), n, _DATA)
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
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("unary_expression", "reference_expression"):
            n = g.add("UNARY:" + _op_text(node, text))
            inner = node.child_by_field_name("value") or _last_expr(node)
            g.link(ev(inner, ctrl), n, _DATA)
            return n
        if t == "try_expression":  # `expr?`
            n = g.add("UNARY:?")
            g.link(ev(_last_expr(node), ctrl), n, _DATA)
            return n
        if t in ("type_cast_expression", "type_ascription_expression"):  # `x as T` — value is x
            inner = node.child_by_field_name("value") or _first(node)
            return ev(inner, ctrl)
        if t == "await_expression":
            return ev(_last_expr(node), ctrl)
        if t == "range_expression":
            n = g.add("RANGE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "assignment_expression":
            val = ev(node.child_by_field_name("right"), ctrl)
            bind(node.child_by_field_name("left"), val)
            return val
        if t == "compound_assignment_expr":
            op = _op_text(node, text)
            base = op[:-1] if op.endswith("=") else op  # `+=` -> `+`
            left = node.child_by_field_name("left")
            n = g.add("BINOP:" + base)
            g.link(ev(left, ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            bind(left, n)
            return n
        if t == "if_expression":
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("condition"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            cons = node.child_by_field_name("consequence")
            if cons is not None:
                g.link(_block_or_expr_value(cons, b), b, _DATA)
            alt = node.child_by_field_name("alternative")
            if alt is not None:
                g.link(_block_or_expr_value(alt, b), b, _DATA)
            return b
        if t == "match_expression":
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("value"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            body = node.child_by_field_name("body")
            if body is not None:
                for arm in body.named_children:
                    if arm.type == "match_arm":
                        # an arm guard `pat if <cond> => …` keeps <cond> under the match_pattern's
                        # `condition` field, NOT the arm's `value` — walk it so its flow isn't
                        # dropped (R181 opus; mirrors if_expression + Python's case.guard).
                        pat = arm.child_by_field_name("pattern")
                        if pat is not None:
                            guard = pat.child_by_field_name("condition")
                            if guard is not None:
                                g.link(ev(guard, b), b, _DATA)
                        val = arm.child_by_field_name("value")
                        if val is not None:
                            g.link(_block_or_expr_value(val, b), b, _DATA)
            return b
        if t in ("for_expression", "while_expression", "loop_expression"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            cond = node.child_by_field_name("condition")
            if cond is not None:
                g.link(ev(cond, loop), loop, _DATA)
            it = node.child_by_field_name("value")  # for-loop iterator
            pat = node.child_by_field_name("pattern")
            if it is not None and pat is not None:
                iv = g.add("ITERVAR")
                g.link(ev(it, loop), iv, _DATA)
                bind(pat, iv)
            body = node.child_by_field_name("body")
            if body is not None and body.type == "block":
                _walk_block(body, loop, as_value=False)
            return loop
        if t in ("struct_expression", "field_initializer_list"):
            n = g.add("COMPOSITE")
            body = node.child_by_field_name("body") if t == "struct_expression" else node
            if body is not None:
                for fi in body.named_children:
                    if fi.type == "field_initializer":
                        g.link(ev(fi.child_by_field_name("value"), ctrl), n, _DATA)
                    elif fi.type == "shorthand_field_initializer":
                        g.link(ev(fi, ctrl), n, _DATA)
                    elif fi.type == "base_field_initializer":
                        g.link(ev(_last_expr(fi), ctrl), n, _DATA)
            return n
        if t in ("array_expression", "tuple_expression"):
            n = g.add("SEQ")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "macro_invocation":
            n = g.add("MACRO")
            for c in node.named_children:
                if c.type == "token_tree":
                    _walk_token_tree(c, n, ctrl)
            return n
        if t == "return_expression":
            n = g.add("RETURN")
            inner = _last_expr(node)
            if inner is not None:
                g.link(ev(inner, ctrl), n, _DATA)
            return n
        if t in ("break_expression", "continue_expression", "yield_expression"):
            inner = _last_expr(node)
            return ev(inner, ctrl) if inner is not None else g.add(t.split("_")[0].upper())
        if t in _FUNC_NODES:
            return g.add("NESTED")
        # generic fallback: a node fed by its sub-expressions (the completeness oracle makes gaps
        # visible, so an unhandled construct can never silently vanish from the fingerprint).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def _walk_token_tree(tt, parent: int, ctrl: int | None) -> None:
        """Macro args are raw tokens, not parsed expressions. Best-effort: thread value flow from any
        identifier/literal token (a variable passed to a macro) and recurse into nested trees."""
        for c in tt.named_children:
            if c.type == "identifier":
                g.link(ev(c, ctrl), parent, _DATA)
            elif c.type in _CONST:
                g.link(g.add("CONST"), parent, _DATA)
            elif c.type == "token_tree":
                _walk_token_tree(c, parent, ctrl)

    def _block_or_expr_value(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        if node.type == "block":
            return _walk_block(node, ctrl, as_value=True)
        if node.type == "else_clause":
            inner = _last_expr(node)
            return _block_or_expr_value(inner, ctrl)
        return ev(node, ctrl)

    def _last_expr(node):
        kids = [c for c in node.named_children
                if c.type not in ("line_comment", "block_comment")]  # comments are trivia
        return kids[-1] if kids else None

    def do(node, ctrl: int | None) -> None:
        t = node.type
        if t in ("line_comment", "block_comment"):  # trivia
            return
        if t == "let_declaration":
            val = ev(node.child_by_field_name("value"), ctrl)
            bind(node.child_by_field_name("pattern"), val)
            alt = node.child_by_field_name("alternative")  # let-else
            if alt is not None and alt.type == "block":
                _walk_block(alt, ctrl, as_value=False)
        elif t == "expression_statement":
            for c in node.named_children:
                ev(c, ctrl)
        else:
            ev(node, ctrl)

    body = fn.child_by_field_name("body")
    if body is not None and body.type == "block":
        val = _walk_block(body, None, as_value=True)
        if val is not None:
            n = g.add("RETURN")
            g.link(val, n, _DATA)
    elif body is not None:  # closure with an expression body: `|x| x + 1`
        n = g.add("RETURN")
        g.link(ev(body, None), n, _DATA)
    return g


def _pattern_names(node, text) -> list[str]:
    """Identifier names bound by a parameter/let pattern (best-effort over the common pattern kinds)."""
    if node is None:
        return []
    t = node.type
    if t == "identifier":
        name = text(node)
        return [] if name == "_" else [name]
    if t in ("mut_pattern", "ref_pattern", "reference_pattern", "tuple_pattern", "slice_pattern",
             "tuple_struct_pattern", "struct_pattern", "or_pattern", "captured_pattern"):
        return [n for c in node.named_children for n in _pattern_names(c, text)]
    if t == "field_pattern":
        inner = node.child_by_field_name("pattern")
        return _pattern_names(inner, text) if inner is not None else (
            [text(_last(node))] if _nc(node) else [])
    return []


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


# --- STATEMENT layer (PDG) — design §5c sweep, Rust ----------------------------------------------

_PDG_STMT_LABEL = {
    "let_declaration": "Let", "expression_statement": "Expr", "empty_statement": "Empty",
    "if_expression": "If", "match_expression": "Match", "loop_expression": "Loop",
    "while_expression": "While", "for_expression": "For", "return_expression": "Return",
    "macro_invocation": "Macro", "break_expression": "Break", "continue_expression": "Continue",
}
_PDG_CONTROL = frozenset({"if_expression", "match_expression", "loop_expression",
                          "while_expression", "for_expression"})
_PDG_COMMENT = frozenset({"line_comment", "block_comment"})


def _pdg_label(t: str) -> str:
    return _PDG_STMT_LABEL.get(t) or "".join(w.capitalize() for w in t.split("_")) or "Stmt"


def _build_pdg(fn, data: bytes) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer for a Rust function — a program-dependence graph mirroring
    `structure._build_pdg` (Python) and the JS/Go builders: statement nodes + a synthetic ENTRY
    carrying params (and `self`), control ('C') / data ('D', sequential reaching-def) edges. Rust is
    expression-oriented, so control-flow *expressions* (if/match/loop/while/for) in statement position
    become control nodes; in value position they are folded into the enclosing statement's reads.
    Nested functions/closures are opaque NESTED leaves. Advisory only — never feeds liveness."""
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
            if p.type == "self_parameter":
                last_def["self"] = entry
            elif p.type == "parameter":
                pat = p.child_by_field_name("pattern")
                for nm in (_pattern_names(pat, text) if pat is not None else []):
                    last_def[nm] = entry
            else:
                for nm in _pattern_names(p, text):
                    last_def[nm] = entry

    def add_target(n, loads: set, stores: set) -> None:
        """Names bound by a `let`/assignment pattern (a `store`). Place/deref targets
        (`obj.f = …`, `a[i] = …`, `*p = …`) define no name — their object/index are READS."""
        if n is None:
            return
        t = n.type
        if t == "identifier":
            if text(n) != "_":
                stores.add(text(n))
        elif t in ("mut_pattern", "ref_pattern", "reference_pattern", "tuple_pattern",
                   "tuple_struct_pattern", "slice_pattern", "struct_pattern", "or_pattern",
                   "captured_pattern", "ref_pattern"):
            for c in n.named_children:
                add_target(c, loads, stores)
        elif t == "field_pattern":
            inner = n.child_by_field_name("pattern")
            add_target(inner if inner is not None else _last(n), loads, stores)
        elif t in ("field_expression", "index_expression", "unary_expression",
                   "reference_expression"):
            collect(n, loads, stores)
        elif n.named_children:  # e.g. `let Some(x) = …` — bind bare identifiers within
            for c in n.named_children:
                if c.type == "identifier" and text(c) != "_":
                    stores.add(text(c))

    def collect(n, loads: set, stores: set) -> None:
        """Reads/writes within one statement's header — stops at nested blocks (their own nodes),
        nested functions/closures (opaque), and TYPE positions (no runtime value flow)."""
        if n is None:
            return
        t = n.type
        if t in _FUNC_NODES or t in _PDG_COMMENT or t == "block":
            return
        if t == "type_identifier" or t == "scoped_type_identifier" or t.endswith("_type"):
            return  # a type position carries no value read
        if t == "scoped_identifier":
            return  # a module/type path (`Foo::bar`), not a local variable read
        if t == "let_declaration":
            pat = n.child_by_field_name("pattern")
            if pat is not None:
                add_target(pat, loads, stores)
            collect(n.child_by_field_name("value"), loads, stores)
            return
        if t == "assignment_expression":
            add_target(n.child_by_field_name("left"), loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t == "compound_assignment_expr":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                stores.add(text(left))
                loads.add(text(left))
            elif left is not None:
                collect(left, loads, stores)
            collect(n.child_by_field_name("right"), loads, stores)
            return
        if t == "identifier":
            if text(n) != "_":
                loads.add(text(n))
            return
        if t == "field_expression":
            collect(n.child_by_field_name("value"), loads, stores)  # read object; skip field name
            return
        for c in n.named_children:
            collect(c, loads, stores)

    def data_edges(hdr, sid: int) -> None:
        if hdr is None:
            return
        loads: set = set()
        stores: set = set()
        collect(hdr, loads, stores)
        for nm in sorted(loads):
            if nm in last_def and last_def[nm] != sid:
                edges.append((last_def[nm], sid, "D"))
        for nm in sorted(stores):
            last_def[nm] = sid

    def bind_target(node, sid: int) -> None:
        st: set = set()
        add_target(node, set(), st)
        for nm in sorted(st):
            last_def[nm] = sid

    def cond_edges(cond, sid: int) -> None:
        # `if let PAT = EXPR` / `while let …` (+ let-chains): EXPR is read, PAT binds.
        if cond is None:
            return
        if cond.type in ("let_condition", "let_chain"):
            for c in cond.named_children:
                if c.type in ("let_condition", "let_chain"):
                    cond_edges(c, sid)
            val = cond.child_by_field_name("value")
            if val is not None:
                data_edges(val, sid)
            pat = cond.child_by_field_name("pattern")
            if pat is not None:
                bind_target(pat, sid)
        else:
            data_edges(cond, sid)

    def block(blk, parent: int) -> None:
        if blk is None or blk.type in _PDG_COMMENT:
            return
        if blk.type == "block":
            for c in blk.named_children:
                process(c, parent)
        elif blk.type == "else_clause":
            for c in blk.named_children:
                block(c, parent)
        else:
            process(blk, parent)

    def process(node, parent: int) -> None:
        t = node.type
        if t in _PDG_COMMENT:
            return
        if t in _FUNC_NODES:
            edges.append((parent, new_id("NESTED"), "C"))
            return
        if t == "expression_statement":
            for c in node.named_children:
                process(c, parent)
            return
        if t == "let_declaration":
            sid = new_id("Let")
            edges.append((parent, sid, "C"))
            data_edges(node, sid)  # pattern stores + value reads (value-position control folded)
            return
        if t in _PDG_CONTROL or t == "block":
            sid = new_id(_pdg_label(t))
            edges.append((parent, sid, "C"))
            if t == "if_expression":
                cond_edges(node.child_by_field_name("condition"), sid)
                block(node.child_by_field_name("consequence"), sid)
                block(node.child_by_field_name("alternative"), sid)
            elif t == "while_expression":
                cond_edges(node.child_by_field_name("condition"), sid)
                block(node.child_by_field_name("body"), sid)
            elif t == "loop_expression":
                block(node.child_by_field_name("body"), sid)
            elif t == "for_expression":
                data_edges(node.child_by_field_name("value"), sid)  # iterator
                pat = node.child_by_field_name("pattern")
                if pat is not None:
                    bind_target(pat, sid)
                block(node.child_by_field_name("body"), sid)
            elif t == "match_expression":
                data_edges(node.child_by_field_name("value"), sid)  # scrutinee
                body = node.child_by_field_name("body")
                if body is not None:
                    for arm in body.named_children:
                        if arm.type == "match_arm":
                            block(arm.child_by_field_name("value"), sid)  # arm body
            else:  # bare block expression
                for c in node.named_children:
                    process(c, sid)
            return
        # a simple statement / trailing expression (return/macro/call/assign/break/…): the whole node
        # is its header — collect stops at nested blocks/functions, so nothing leaks. Descend any
        # nested block child (closes the class, parity with the JS/Go layers).
        sid = new_id(_pdg_label(t))
        edges.append((parent, sid, "C"))
        data_edges(node, sid)
        for c in node.named_children:
            if c.type == "block":
                block(c, sid)

    body = fn.child_by_field_name("body")
    if body is not None:
        block(body, entry)
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges
