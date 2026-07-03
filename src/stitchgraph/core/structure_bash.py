"""Structural (body-level) fingerprints for Bash/shell functions.

The Bash frontend to the intra-procedural matrix: it builds the SAME `_VFG` value-flow graph as
`structure.py` (operations + control points, data + control edges, copy propagation) from a
tree-sitter **concrete** syntax tree, then reuses the language-neutral Weisfeiler-Lehman kernel
(`structure._wl_features` / `structure.similarity`). So Bash↔Bash bodies compare exactly the way every
other per-language frontend's do (one shared WL kernel).

Bash is the outlier of the sweep — **command-oriented, not expression-oriented**. The model:
- a `command` (`name arg…`) is a CALL — the command name is the callee, its arguments flow as data;
- `$(…)` / `` `…` `` **command substitution** carries the value of the command it runs;
- a `variable_assignment` (`x=…`, `local x=…`) binds the name to its value (copy propagation);
- `$x` / `${x}` **expansions** are variable reads; a string carries flow through its `$(…)`/`$x` holes;
- `$(( … ))` arithmetic, `[[ … ]]`/`[ … ]` tests, pipelines, and if/for/while/case are walked for
  their control + data flow.

Functions are keyed by their bare name (`compute`) — shell functions are flat, matching the extractor.
It is a structural approximation, NOT sound data flow (no word-splitting/alias analysis, exit codes
and side-effecting globals are not tracked, constants collapsed). Two known structural blind spots are
inherent and accepted: (1) a `${var#$(cmd)}`/`${var%…}` **strip pattern** is lexed by tree-sitter as a
single opaque `regex` token, so a command substitution *inside* the strip pattern is not a walkable
child; (2) a **single-quoted deferred action** like `trap '$(cmd)' EXIT` is a `raw_string` whose
expansion only happens at `eval`/trap time — the no-`eval` rule means it reads as a constant. Both are
advisory-only mis-rankings, never cardinal. The method is in `docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import collections

from .structure import _CTRL, _DATA, _VFG, _serialize_vfg, _wl_features
from .structure_common import make_parser, op_text

_EXTS = {".sh": "bash", ".bash": "bash"}

_FUNC_NODES = frozenset({"function_definition"})

# Leaf literals — one CONST node regardless of value. (A `string`/`raw_string` is handled specially
# so its `$(…)`/`$x` holes carry flow.)
_CONST = frozenset({"number", "raw_string", "word", "regex"})


def _parser():
    return make_parser("bash")


def _lang_for_ext(ext: str) -> str | None:
    return _EXTS.get(ext.lower())


def _walk(source: str, lang: str, build):
    """Shared traversal for `fingerprint_source` / `vfg_source`: apply `build(fn_node, data)` to every
    function keyed by its bare name. Returns {} on parse failure / missing extra / too-deep tree."""
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

    def visit(node) -> None:
        for child in node.named_children:
            if child.type == "function_definition":
                nm = child.child_by_field_name("name")
                if nm is not None:
                    emit(text(nm), child)
                # nested function definitions inside a body are opaque — don't recurse for keys.
            else:
                visit(child)

    try:
        visit(tree.root_node)
    except (RecursionError, ValueError):
        return out
    return out


def fingerprint_source(source: str, lang: str = "bash") -> dict[str, collections.Counter[str]]:
    """Fingerprint every function in a Bash source string, keyed by the bare function name (shell
    functions are flat). Returns {} on a parse failure, a missing tree-sitter extra, or a too-deep
    tree (advisory, never raises)."""
    return _walk(source, lang, lambda fn, data: _wl_features(_build_vfg(fn, data)))


def vfg_source(source: str, lang: str = "bash") -> dict[str, tuple[list[str], list]]:
    """Value-flow graph of every function — EXPRESSION-layer companion to fingerprint_source
    (identical keys). Advisory, on demand."""
    return _walk(source, lang, lambda fn, data: _serialize_vfg(_build_vfg(fn, data)))


def _build_vfg(fn, data: bytes) -> _VFG:
    """Symbolically evaluate one Bash function node into a value-flow graph, mirroring
    `structure._build_vfg`: operations + control points as nodes, data/control edges, copy
    propagation through assignments. Command-oriented (a command is a CALL)."""
    g = _VFG()
    env: dict[str, int] = {}
    free: dict[str, int] = {}

    def text(node) -> str:
        return node.text.decode("utf-8", "replace")

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    def _varname(node) -> str:
        # `$x` (simple_expansion) / `${x}` (expansion) -> the bare variable name `x`.
        t = text(node).lstrip("$").strip("{}")
        return t.split("[", 1)[0].split(":", 1)[0] or t

    def ev(node, ctrl: int | None) -> int | None:
        if node is None:
            return None
        t = node.type
        if t == "comment":  # trivia: a comment must never alter the value-flow fingerprint
            return None
        if t == "variable_name":
            name = text(node)
            return env[name] if name in env else freevar(name)
        if t == "simple_expansion":
            name = _varname(node)
            return env[name] if name in env else freevar(name)
        if t == "expansion":
            # ${x}, ${x:-default}, ${x[i]} — read x, and any sub-expressions (default/index) flow.
            n = g.add("ATTR")
            name = _varname(node)
            g.link(env[name] if name in env else freevar(name), n, _DATA)
            for c in node.named_children:
                if c.type == "subscript":
                    # ${arr[i]} — the base name is already read above; the index expression carries
                    # flow (e.g. ${arr[$(helper)]}).
                    idx = c.child_by_field_name("index")
                    if idx is not None:
                        g.link(ev(idx, ctrl), n, _DATA)
                elif c.type != "variable_name":
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in _CONST:
            return g.add("CONST")
        if t in ("string", "raw_string", "translated_string", "ansi_c_string"):
            n = g.add("CONST")
            for c in node.named_children:
                if c.type not in ("string_content",):
                    g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "concatenation":
            n = g.add("CONCAT")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("command_substitution", "process_substitution"):
            inner = [c for c in node.named_children]
            last = None
            for c in inner:
                last = ev(c, ctrl) if c.type not in _STMT_TYPES else _do(c, ctrl)
            return last
        if t in ("arithmetic_expansion", "arithmetic_expression"):
            n = g.add("ARITH")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "binary_expression":
            n = g.add("BINOP:" + _op_text(node, text))
            g.link(ev(node.child_by_field_name("left"), ctrl), n, _DATA)
            g.link(ev(node.child_by_field_name("right"), ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t in ("unary_expression", "postfix_expression", "parenthesized_expression"):
            n = g.add("UNARY")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t == "command":
            n = g.add("CALL")
            nm = node.child_by_field_name("name")
            if nm is not None:
                g.link(ev(nm, ctrl), n, _DATA)
            for i, c in enumerate(node.named_children):
                fld = node.field_name_for_named_child(i)
                if fld == "name":
                    continue
                if c.type == "variable_assignment":  # `FOO=bar cmd` prefix assignment
                    _do(c, ctrl)
                else:
                    g.link(ev(c, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if t == "command_name":
            # A literal command name (`echo`, `helper`) is a free callee. A *dynamic* name —
            # `$(…)`/`${…}`/concatenation — carries flow that determines the callee; walk it so a
            # command-substitution CALL in callee position is never dropped.
            parts = node.named_children
            if len(parts) == 1 and parts[0].type in ("word", "number"):
                return freevar(text(node))
            cn = g.add("CALLEE")
            for c in parts:
                g.link(ev(c, ctrl), cn, _DATA)
            return cn
        if t == "test_command":
            n = g.add("TEST")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in ("array",):
            n = g.add("COMPOSITE")
            for c in node.named_children:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if t in _STMT_TYPES:
            return _do(node, ctrl)
        if t in _FUNC_NODES:
            return g.add("NESTED")
        # generic fallback — nothing silently vanishes (the completeness oracle makes gaps visible).
        n = g.add(t.upper())
        for ch in node.named_children:
            g.link(ev(ch, ctrl), n, _DATA)
        return n

    def bind(target, val: int | None) -> None:
        if target is None:
            return
        if target.type in ("variable_name", "word"):
            name = text(target)
            if val is not None:
                env[name] = val
            else:
                env.pop(name, None)

    def _do(node, ctrl: int | None) -> int | None:
        t = node.type
        if t == "comment":  # trivia
            return None
        if t == "variable_assignment":
            val = ev(node.child_by_field_name("value"), ctrl)
            target = node.child_by_field_name("name")
            if target is not None and target.type == "subscript":
                # `arr[$(helper)]=x` — the index expression on the LHS carries flow (and there's no
                # simple name to copy-propagate into, so we only walk it, not bind).
                idx = target.child_by_field_name("index")
                if idx is not None:
                    ev(idx, ctrl)
            bind(target, val)
            return val
        if t == "declaration_command":  # local/declare/export/readonly/typeset …
            last = None
            for c in node.named_children:
                if c.type == "variable_assignment":
                    last = _do(c, ctrl)
                elif c.type in ("variable_name", "word"):
                    pass  # a bare `local x` declares without a value
                else:
                    last = ev(c, ctrl)
            return last
        if t == "command":
            return ev(node, ctrl)
        if t in ("pipeline", "list"):
            last = None
            for c in node.named_children:
                last = _do(c, ctrl) if c.type in _STMT_TYPES else ev(c, ctrl)
            return last
        if t in ("compound_statement", "subshell", "do_group"):
            _do_body(node, ctrl)
            return None
        if t == "if_statement":
            b = g.add("BRANCH")
            # `condition` is a REPEATED field (`if cmd1; cmd2; then`): child_by_field_name
            # returns only the first, and the body loop skipped every condition child, so
            # the 2nd..nth guard command vanished — a function with an extra guard
            # fingerprinted IDENTICAL to one without (review 2026-07-03, F5b; the same
            # repeated-field hazard closed for Java in R197).
            for i, c in enumerate(node.named_children):
                if node.field_name_for_named_child(i) == "condition":
                    v = _do(c, ctrl) if c.type in _STMT_TYPES else ev(c, ctrl)
                    g.link(v, b, _DATA)
            g.link(ctrl, b, _CTRL)
            for i, c in enumerate(node.named_children):
                fld = node.field_name_for_named_child(i)
                if fld == "condition":
                    continue
                if c.type in ("elif_clause", "else_clause"):
                    cond = c.child_by_field_name("condition")
                    if cond is not None:
                        ev(cond, b)
                    _do_body(c, b)
                else:
                    _do(c, b)
            return b
        if t == "for_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            it = g.add("ITERVAR")
            for i, c in enumerate(node.named_children):
                fld = node.field_name_for_named_child(i)
                if fld == "value":
                    g.link(ev(c, loop), it, _DATA)
            bind(node.child_by_field_name("variable"), it)
            _do_body(node.child_by_field_name("body"), loop)
            return loop
        if t == "c_style_for_statement":
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            for c in node.named_children:
                if c.type != "do_group" and c.type != "compound_statement":
                    ev(c, loop)
            _do_body(node.child_by_field_name("body"), loop)
            return loop
        if t in ("while_statement", "until_statement"):
            loop = g.add("LOOP")
            g.link(ctrl, loop, _CTRL)
            cond = node.child_by_field_name("condition")
            if cond is not None:
                _do(cond, loop) if cond.type in _STMT_TYPES else ev(cond, loop)
            _do_body(node.child_by_field_name("body"), loop)
            return loop
        if t == "case_statement":
            b = g.add("BRANCH")
            g.link(ev(node.child_by_field_name("value"), ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            for item in node.named_children:
                if item.type == "case_item":
                    for c in item.named_children:
                        if c.type in _STMT_TYPES or c.type in ("command", "variable_assignment",
                                                               "pipeline", "list"):
                            _do(c, b)
                        else:
                            ev(c, b)  # the pattern(s)
            return b
        if t in ("redirected_statement", "negated_command", "subshell", "compound_statement"):
            for c in node.named_children:
                if c.type in _STMT_TYPES or c.type in ("command", "pipeline", "list",
                                                       "variable_assignment"):
                    _do(c, ctrl)
                else:
                    ev(c, ctrl)
            return None
        return ev(node, ctrl)

    def _do_body(node, ctrl: int | None) -> None:
        if node is None:
            return
        if node.type in ("compound_statement", "do_group", "subshell"):
            for st in node.named_children:
                _do(st, ctrl)
        else:
            _do(node, ctrl)

    body = fn.child_by_field_name("body")
    if body is not None:
        _do_body(body, None)
    return g


_STMT_TYPES = frozenset({
    "variable_assignment", "declaration_command", "command", "pipeline", "list",
    "compound_statement", "subshell", "do_group", "if_statement", "for_statement",
    "c_style_for_statement", "while_statement", "until_statement", "case_statement",
    "redirected_statement", "negated_command",
})


def _op_text(node, text) -> str:
    return op_text(node)


# --- STATEMENT layer (PDG) — design §5c sweep, Bash (the final language) --------------------------

_PDG_STMT_LABEL = {
    "variable_assignment": "Assign", "declaration_command": "Decl", "command": "Command",
    "redirected_statement": "Redirect", "negated_command": "Negate", "test_command": "Test",
    "if_statement": "If", "for_statement": "For", "c_style_for_statement": "For",
    "while_statement": "While", "until_statement": "Until", "case_statement": "Case",
}


def _pdg_label(t: str) -> str:
    return _PDG_STMT_LABEL.get(t) or "".join(w.capitalize() for w in t.split("_")) or "Stmt"


def pdg_source(source: str, lang: str = "bash") -> dict[str, tuple[list[str], list]]:
    """Program-dependence graph of every function — the STATEMENT-layer companion to
    fingerprint_source/vfg_source (identical keys), the raw graph get_matrix(layer="statement")
    drills into. Statement nodes + control ('C') / data ('D') dependence edges via a sequential
    reaching-def approximation; nested function definitions are opaque NESTED leaves. Advisory, on
    demand.

    Bash is command-oriented and has NO declared parameter list (shell functions read positional
    `$1…` as free variables), so ENTRY carries no params — the same as the VFG, which seeds no PARAM
    nodes. Accepted layer-level under-approximations, all *symmetric* (shared by BOTH the PDG and the
    VFG, so they create no VFG/PDG divergence): nested function bodies are opaque; a LITERAL command
    name is a free callee, never a variable read (a *dynamic* `$cmd`/`$(…)` name reads its
    expansions); a `${var#$(cmd)}` strip pattern is one opaque token; a single-quoted deferred action
    (`trap '$(cmd)' EXIT`) is a constant (no-eval rule)."""
    return _walk(source, lang, build=lambda fn, data: _build_pdg(fn, data))


def _build_pdg(fn, data: bytes) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer for a Bash function — a program-dependence graph mirroring
    `structure._build_pdg` (Python) and the JS-family/Go/Rust/C++/Java/C#/Ruby/PHP PDG builders:
    statement nodes + a synthetic ENTRY (empty — Bash has no parameter list), control ('C') / data
    ('D', sequential reaching-def) edges. Bash is command-oriented (a command is a statement whose
    callee + arguments are reads). Nested function definitions are opaque NESTED leaves;
    reorder-invariant. A structural approximation (no word-splitting/alias analysis), advisory only —
    never feeds liveness. Its read/write projection (`collect`/`bind_place`) reads ONLY genuine value
    operands and records ONLY genuine bindings, matching the VFG's `ev`/`bind` node-for-node: a
    LITERAL command name and a `local x` bare declaration are never read as values."""
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

    entry = new_id("ENTRY")  # empty seed — shell functions have no declared parameters

    def _varname(node) -> str:
        t = text(node).lstrip("$").strip("{}")
        return t.split("[", 1)[0].split(":", 1)[0] or t

    def collect(n, loads: set, stores: set) -> None:
        """Reads/writes within one statement's header, mirroring the VFG's `ev`/`bind` node-for-node.
        Stops at nested function definitions (opaque NESTED, like the VFG's NESTED leaf). A LITERAL
        command name is a free callee (never a variable read); a `local x` bare declaration binds no
        value; a member/index expansion reads the base name + its index/default sub-expressions."""
        if n is None:
            return
        t = n.type
        if t == "comment" or t in _FUNC_NODES:
            return
        if t == "variable_name":
            loads.add(text(n))
            return
        if t == "simple_expansion":  # `$x`
            loads.add(_varname(n))
            return
        if t == "expansion":  # `${x}`, `${x:-default}`, `${x[i]}`
            loads.add(_varname(n))
            for c in n.named_children:
                if c.type == "subscript":
                    idx = c.child_by_field_name("index")
                    if idx is not None:
                        collect(idx, loads, stores)
                elif c.type != "variable_name":  # the base name is already read above
                    collect(c, loads, stores)
            return
        if t in _CONST:  # number / raw_string / word / regex — a literal, no read
            return
        if t in ("string", "raw_string", "translated_string", "ansi_c_string"):
            for c in n.named_children:
                if c.type not in ("string_content",):
                    collect(c, loads, stores)
            return
        if t == "command_name":
            # A literal command name (`echo`, `helper`) is a free callee — NOT a variable read
            # (the VFG routes it through `freevar`). A *dynamic* name (`$x`/`$(…)`/concatenation)
            # carries flow that determines the callee; read its expansions.
            parts = n.named_children
            if len(parts) == 1 and parts[0].type in ("word", "number"):
                return
            for c in parts:
                collect(c, loads, stores)
            return
        if t == "variable_assignment":  # `x=…` / `arr[i]=…` (incl. `FOO=bar` command prefix)
            collect(n.child_by_field_name("value"), loads, stores)
            target = n.child_by_field_name("name")
            if target is not None and target.type == "subscript":
                idx = target.child_by_field_name("index")
                if idx is not None:
                    collect(idx, loads, stores)  # LHS index reads; no simple name to bind
            elif target is not None and target.type in ("variable_name", "word"):
                stores.add(text(target))
            return
        if t == "declaration_command":  # local/declare/export/readonly/typeset …
            for c in n.named_children:
                if c.type in ("variable_name", "word"):
                    pass  # a bare `local x` declares without a value — not a read (mirrors the VFG)
                else:
                    collect(c, loads, stores)
            return
        for c in n.named_children:  # generic recursion — parity with the VFG's `ev` fallback
            collect(c, loads, stores)

    def data_from(loads: set, stores: set, sid: int) -> None:
        # sorted iteration: a string set iterates in PYTHONHASHSEED order, which would make the edge
        # list (and get_matrix cells) non-reproducible across processes (R205).
        for nm in sorted(loads):
            if nm in last_def and last_def[nm] != sid:
                edges.append((last_def[nm], sid, "D"))
        for nm in sorted(stores):
            last_def[nm] = sid

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
        if blk.type in ("compound_statement", "do_group", "subshell"):
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
        if t in ("pipeline", "list", "compound_statement", "subshell", "do_group"):
            for c in node.named_children:  # transparent grouping — each child is its own statement
                block(c, parent)
            return
        if t == "if_statement":
            sid = new_id("If")
            edges.append((parent, sid, "C"))
            # `condition` is a REPEATED field (`if cmd1; cmd2; then`) — read every
            # condition child, not just the first (review 2026-07-03, F5b; mirrors the
            # VFG fix so the two builders stay in lock-step).
            for i, c in enumerate(node.named_children):
                if node.field_name_for_named_child(i) == "condition":
                    data_edges(c, sid)
            for i, c in enumerate(node.named_children):
                if node.field_name_for_named_child(i) == "condition":
                    continue
                if c.type == "elif_clause":
                    for j, cc in enumerate(c.named_children):
                        if c.field_name_for_named_child(j) == "condition":
                            data_edges(cc, sid)
                        else:
                            block(cc, sid)
                elif c.type == "else_clause":
                    for cc in c.named_children:
                        block(cc, sid)
                else:
                    block(c, sid)
            return
        if t == "for_statement":
            sid = new_id("For")
            edges.append((parent, sid, "C"))
            loads: set = set()
            stores: set = set()
            for i, c in enumerate(node.named_children):
                if node.field_name_for_named_child(i) == "value":
                    collect(c, loads, stores)  # the iterated list carries reads
            var = node.child_by_field_name("variable")
            if var is not None and var.type in ("variable_name", "word"):
                stores.add(text(var))  # the loop variable binds
            data_from(loads, stores, sid)
            block(node.child_by_field_name("body"), sid)
            return
        if t == "c_style_for_statement":
            sid = new_id("For")
            edges.append((parent, sid, "C"))
            for c in node.named_children:
                if c.type not in ("do_group", "compound_statement"):
                    data_edges(c, sid)  # init / condition / update arithmetic
            block(node.child_by_field_name("body"), sid)
            return
        if t in ("while_statement", "until_statement"):
            sid = new_id(_pdg_label(t))
            edges.append((parent, sid, "C"))
            data_edges(node.child_by_field_name("condition"), sid)
            block(node.child_by_field_name("body"), sid)
            return
        if t == "case_statement":
            sid = new_id("Case")
            edges.append((parent, sid, "C"))
            data_edges(node.child_by_field_name("value"), sid)  # the scrutinee
            for item in node.named_children:
                if item.type != "case_item":
                    continue
                for i, c in enumerate(item.named_children):
                    if item.field_name_for_named_child(i) == "value":
                        data_edges(c, sid)  # the pattern(s) read on the header
                    elif c.type in _STMT_TYPES or c.type in ("command", "variable_assignment",
                                                             "pipeline", "list"):
                        block(c, sid)  # the case body
                    else:
                        data_edges(c, sid)
            return
        # a leaf statement (command / assignment / declaration / redirected / negated / test): the
        # whole node is its header. collect gathers every read (incl. nested command substitutions)
        # and every binding; a literal command name and a bare `local x` are correctly not read.
        sid = new_id(_pdg_label(t))
        edges.append((parent, sid, "C"))
        data_edges(node, sid)

    body = fn.child_by_field_name("body")
    block(body, entry)
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges
