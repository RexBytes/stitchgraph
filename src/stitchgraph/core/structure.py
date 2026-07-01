"""Structural (body-level) fingerprints for Python functions — the intra-procedural matrix.

stitchgraph's primary graph is *inter*-procedural (defs ↔ defs via CALLS/REFERENCES/…). This
module adds the level *below* it: a per-function **value-flow graph** built from the body AST, and
an order- and name-invariant fingerprint over it. Two functions with the same computation shape —
even with renamed locals, reordered independent statements, or temp-variable factoring — get a
similar fingerprint.

It powers **advisory, read-only** features only (structural `find_similar`, a body-aware
`graph_diff`). It never feeds `find_stale` / liveness rooting, so the cardinal rule does not apply
here: a fingerprint that is too coarse or too fine can only mis-rank an advisory suggestion, never
flag live code dead.

Python-only by design (deep stdlib `ast`); stdlib-only so it runs in the core (no-extras) install.
It is a structural approximation, NOT semantic equivalence or sound data flow: variable copies are
propagated, but there are no SSA φ-nodes, no loop fixpoint, and no alias analysis. Constants are
collapsed to a single leaf, so value-equal-but-differently-written code (Type-4 clones) is not
detected. See `research/04-expr-dfg/FINDINGS.md` for the validation behind these choices.
"""
from __future__ import annotations

import ast
import collections
import hashlib

_OPAQUE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

# Node labels in the value-flow graph. Operations + control points; deliberately coarse so the
# fingerprint tracks shape, not identifiers.
_DATA = "d"
_CTRL = "c"


class _VFG:
    """A value-flow graph: nodes are operations/control points, edges are data ('d') and control
    ('c') flow. Built by symbolic evaluation that threads variables transparently (copy
    propagation), so `x = a.b; use(x)` has the same shape as `use(a.b)`."""

    __slots__ = ("nodes", "edges", "_c")

    def __init__(self) -> None:
        self.nodes: dict[int, str] = {}
        self.edges: list[tuple[int, int, str]] = []
        self._c = 0

    def add(self, label: str) -> int:
        i = self._c
        self._c += 1
        self.nodes[i] = label
        return i

    def link(self, src: int | None, dst: int | None, kind: str) -> None:
        if src is not None and dst is not None:
            self.edges.append((src, dst, kind))


def _build_vfg(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> _VFG:
    g = _VFG()
    env: dict[str, int] = {a.arg: g.add("PARAM") for a in _all_args(fn)}
    free: dict[str, int] = {}

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    def bind(target: ast.AST, val: int | None) -> None:
        if isinstance(target, ast.Name):
            if val is not None:
                env[target.id] = val
            else:
                env.pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                bind(e, val)
        elif isinstance(target, ast.Starred):
            bind(target.value, val)
        elif isinstance(target, ast.Attribute):
            n = g.add("SETATTR")
            g.link(val, n, _DATA)
            g.link(ev(target.value, None), n, _DATA)
        elif isinstance(target, ast.Subscript):
            n = g.add("SETITEM")
            g.link(val, n, _DATA)
            g.link(ev(target.value, None), n, _DATA)
            g.link(ev(target.slice, None), n, _DATA)  # the index expression carries flow

    def ev(node: ast.AST | None, ctrl: int | None) -> int | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                return env[node.id] if node.id in env else freevar(node.id)
            return None
        if isinstance(node, ast.Constant):
            return g.add("CONST")
        if isinstance(node, ast.Attribute):
            n = g.add("ATTR")
            g.link(ev(node.value, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if isinstance(node, ast.Call):
            n = g.add("CALL")
            g.link(ev(node.func, ctrl), n, _DATA)
            for a in node.args:
                g.link(ev(a, ctrl), n, _DATA)
            for kw in node.keywords:
                g.link(ev(kw.value, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if isinstance(node, ast.BinOp):
            # tag the operator: a+b, a-b, a*b are different shapes (a `+`->`-` refactor is a real
            # change graph_diff should catch, not collapse to one "BINOP").
            n = g.add("BINOP:" + type(node.op).__name__)
            g.link(ev(node.left, ctrl), n, _DATA)
            g.link(ev(node.right, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if isinstance(node, ast.BoolOp):
            n = g.add("BOOLOP:" + type(node.op).__name__)
            for v in node.values:
                g.link(ev(v, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.Compare):
            n = g.add("CMP:" + "/".join(type(o).__name__ for o in node.ops))
            g.link(ev(node.left, ctrl), n, _DATA)
            for c in node.comparators:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.UnaryOp):
            n = g.add("UNARY:" + type(node.op).__name__)
            g.link(ev(node.operand, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.Subscript):
            n = g.add("SUBSCRIPT")
            g.link(ev(node.value, ctrl), n, _DATA)
            g.link(ev(node.slice, ctrl), n, _DATA)  # the index carries value flow too (d[compute()])
            return n
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            n = g.add("SEQ")
            for e in node.elts:
                g.link(ev(e, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.Dict):
            n = g.add("DICT")
            for k in node.keys:
                if k is not None:  # None key = `**d` unpacking (no key expression)
                    g.link(ev(k, ctrl), n, _DATA)
            for v in node.values:
                g.link(ev(v, ctrl), n, _DATA)
            return n
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            n = g.add("COMPR")
            for gen in node.generators:
                it = g.add("ITERVAR")
                g.link(ev(gen.iter, ctrl), it, _DATA)
                bind(gen.target, it)
                for cond in gen.ifs:
                    g.link(ev(cond, ctrl), n, _DATA)
            if isinstance(node, ast.DictComp):
                g.link(ev(node.key, ctrl), n, _DATA)
                g.link(ev(node.value, ctrl), n, _DATA)
            else:
                g.link(ev(node.elt, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.IfExp):
            n = g.add("IFEXP")
            g.link(ev(node.test, ctrl), n, _DATA)
            g.link(ev(node.body, ctrl), n, _DATA)
            g.link(ev(node.orelse, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.Await):
            return ev(node.value, ctrl)
        if isinstance(node, ast.Starred):
            return ev(node.value, ctrl)
        if isinstance(node, ast.Lambda):
            # A lambda is an opaque closure — one NESTED leaf, matching nested `def` statements (via
            # `_OPAQUE` in `do`) and every tree-sitter frontend. Its body must NOT leak into the
            # enclosing fingerprint; its default-arg values still carry flow, so walk those.
            n = g.add("NESTED")
            for d in (*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)):
                g.link(ev(d, ctrl), n, _DATA)
            return n
        # fallback: a generic node fed by any sub-expressions
        n = g.add(type(node).__name__.upper())
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.expr):
                g.link(ev(ch, ctrl), n, _DATA)
        return n

    def read_target(t: ast.AST, ctrl: int | None) -> int | None:
        # Read a target's CURRENT value-producer with LOAD semantics. An AugAssign
        # (`x += e`) semantically reads x, combines with e, writes back — so the read
        # edge must reach the BINOP. But a bare Name target has ctx=Store, and ev()
        # gates Name on Load, returning None and silently dropping that edge — which
        # made `x += e` diverge from `x = x + e` (the documented temp-var-factoring
        # invariance). Attribute/Subscript targets already read correctly via ev (their
        # ev branches aren't ctx-gated), so only the bare Name needs the load lookup.
        if isinstance(t, ast.Name):
            return env[t.id] if t.id in env else freevar(t.id)
        return ev(t, ctrl)

    def do(s: ast.stmt, ctrl: int | None) -> None:
        if isinstance(s, ast.Assign):
            val = ev(s.value, ctrl)
            for t in s.targets:
                bind(t, val)
        elif isinstance(s, ast.AnnAssign):
            if s.value is not None:
                bind(s.target, ev(s.value, ctrl))
        elif isinstance(s, ast.AugAssign):
            n = g.add("BINOP:" + type(s.op).__name__)
            g.link(read_target(s.target, ctrl), n, _DATA)
            g.link(ev(s.value, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            bind(s.target, n)
        elif isinstance(s, ast.Return):
            n = g.add("RETURN")
            g.link(ev(s.value, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
        elif isinstance(s, (ast.Expr, ast.Assert, ast.Delete)):
            for ch in ast.iter_child_nodes(s):
                if isinstance(ch, ast.expr):
                    ev(ch, ctrl)
        elif isinstance(s, ast.If):
            b = g.add("BRANCH")
            g.link(ev(s.test, ctrl), b, _DATA)
            g.link(ctrl, b, _CTRL)
            for x in s.body + s.orelse:
                do(x, b)
        elif isinstance(s, (ast.For, ast.AsyncFor)):
            lp = g.add("LOOP")
            g.link(ev(s.iter, ctrl), lp, _DATA)
            g.link(ctrl, lp, _CTRL)
            it = g.add("ITERVAR")
            g.link(lp, it, _DATA)
            bind(s.target, it)
            for x in s.body + s.orelse:
                do(x, lp)
        elif isinstance(s, ast.While):
            lp = g.add("LOOP")
            g.link(ev(s.test, ctrl), lp, _DATA)
            g.link(ctrl, lp, _CTRL)
            for x in s.body + s.orelse:
                do(x, lp)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                v = ev(item.context_expr, ctrl)
                if item.optional_vars is not None:
                    bind(item.optional_vars, v)
            for x in s.body:
                do(x, ctrl)
        elif isinstance(s, (ast.Try, ast.TryStar)):  # TryStar = except* (PEP 654, 3.11+)
            for x in s.body + s.orelse + s.finalbody:
                do(x, ctrl)
            for h in s.handlers:
                if h.type is not None:  # `except <expr>:` — the selector is evaluated (value flow)
                    eh = g.add("EXCEPT")
                    g.link(ev(h.type, ctrl), eh, _DATA)
                    g.link(ctrl, eh, _CTRL)
                for x in h.body:
                    do(x, ctrl)
        elif isinstance(s, ast.Raise):
            n = g.add("RAISE")
            for ch in ast.iter_child_nodes(s):
                if isinstance(ch, ast.expr):
                    g.link(ev(ch, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
        elif isinstance(s, ast.Match):  # match/case (PEP 634, 3.10+)
            subj = ev(s.subject, ctrl)
            for case in s.cases:
                b = g.add("CASE")
                g.link(subj, b, _DATA)
                g.link(ctrl, b, _CTRL)
                for cap in _match_captures(case.pattern):  # bind capture names to the subject
                    env[cap] = b
                if case.guard is not None:
                    g.link(ev(case.guard, ctrl), b, _DATA)
                for x in case.body:
                    do(x, b)
        elif isinstance(s, _OPAQUE):
            n = g.add("NESTED")
            # The body is an opaque closure/class leaf, but parts evaluated in THIS (enclosing) scope
            # carry value flow: a nested def's default-arg values, a nested class's base-class /
            # keyword (e.g. metaclass=) expressions, and decorator-CALL arguments (evaluated eagerly
            # at definition time in this scope — the decorator *binding* is metadata, but `@deco(expr)`
            # arguments are a live computation).
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = s.args
                for d in (*a.defaults, *[k for k in a.kw_defaults if k is not None]):
                    g.link(ev(d, None), n, _DATA)
            elif isinstance(s, ast.ClassDef):
                for base in s.bases:
                    g.link(ev(base, None), n, _DATA)
                for kw in s.keywords:
                    g.link(ev(kw.value, None), n, _DATA)
            for dec in s.decorator_list:  # @deco(arg) — walk the call arguments (enclosing-scope flow)
                if isinstance(dec, ast.Call):
                    for darg in dec.args:
                        g.link(ev(darg, None), n, _DATA)
                    for dkw in dec.keywords:
                        g.link(ev(dkw.value, None), n, _DATA)
        elif not isinstance(s, (ast.Pass, ast.Break, ast.Continue, ast.Global,
                                ast.Nonlocal, ast.Import, ast.ImportFrom)):
            # Generic fallback: capture value flow from any statement type not explicitly handled
            # above, so a future syntax addition can never silently vanish from the fingerprint the
            # way `match` once did (panel R158). The named no-value-flow statements are skipped.
            for ch in ast.iter_child_nodes(s):
                if isinstance(ch, ast.expr):
                    ev(ch, ctrl)
                elif isinstance(ch, ast.stmt):
                    do(ch, ctrl)
        # pass / break / continue / global / nonlocal / import: no value-flow contribution

    # A parameter's default value carries flow (`def f(a=helper())`): walk each default and link it
    # into the corresponding PARAM node. Positional defaults align to the END of posonly+args; the
    # kw-only defaults align to kwonlyargs (and may be None for a required kw-only param).
    a = fn.args
    positional = list(a.posonlyargs) + list(a.args)
    for arg, default in zip(positional[len(positional) - len(a.defaults):], a.defaults,
                            strict=False):
        if arg.arg in env:
            g.link(ev(default, None), env[arg.arg], _DATA)
    for kwarg, kwd in zip(a.kwonlyargs, a.kw_defaults, strict=False):
        if kwd is not None and kwarg.arg in env:
            g.link(ev(kwd, None), env[kwarg.arg], _DATA)

    for s in fn.body:
        do(s, None)
    return g


def _match_captures(pattern: ast.AST) -> list[str]:
    """Capture names bound by a match `case` pattern (`MatchAs`/`MatchStar`/`MatchMapping.rest`),
    so a use of a captured name in the case body flows from the subject rather than reading FREE."""
    names: list[str] = []
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.append(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.append(node.rest)
    return names


def _all_args(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    a = fn.args
    out = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
    if a.vararg:
        out.append(a.vararg)
    if a.kwarg:
        out.append(a.kwarg)
    return out


def _wl_features(g: _VFG, iters: int = 3) -> collections.Counter[str]:
    """Weisfeiler-Lehman *kernel* feature bag: accumulate node labels across all refinement
    iterations (h=0 raw kind, h=1 +immediate neighbours, …) so similarity is graded — coarse
    iterations let partially-similar graphs overlap, refined ones reward identical structure."""
    inc: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    for s, d, k in g.edges:
        inc[d].append(("<" + k, s))
        inc[s].append((">" + k, d))
    labels = dict(g.nodes)
    feats: collections.Counter[str] = collections.Counter(f"0:{lab}" for lab in labels.values())
    for it in range(1, iters + 1):
        nxt: dict[int, str] = {}
        for n in g.nodes:
            sig = sorted((tag, labels[m]) for tag, m in inc.get(n, []))
            nxt[n] = hashlib.md5((labels[n] + "|" + repr(sig)).encode()).hexdigest()[:8]
        labels = nxt
        feats.update(f"{it}:{lab}" for lab in labels.values())
    return feats


def fingerprint(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> collections.Counter[str]:
    """Structural fingerprint of a function body: a Weisfeiler-Lehman feature bag over its
    value-flow graph. Order- and name-invariant; compare two with `similarity`."""
    return _wl_features(_build_vfg(fn))


def similarity(a: collections.Counter[str], b: collections.Counter[str]) -> float:
    """Cosine similarity of two fingerprints, in [0.0, 1.0]. 1.0 = identical structure.
    An empty fingerprint has norm 0, so the `na and nb` guard returns 0.0 for it — no separate
    empty check needed (and that guard prevents the division-by-zero a one-sided norm would cause).
    """
    keys = set(a) | set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    # clamp: float rounding can push an identical-pair cosine to 1.0000000002.
    return min(1.0, dot / (na * nb)) if na and nb else 0.0


def _serialize_vfg(g: _VFG) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The value-flow graph as consumer-facing primitives: node labels indexed 0..n-1 (VFG ids are
    assigned sequentially by `_VFG.add`, so list index == node id) and edges as (src, dst, kind)
    with kind `_DATA` ('d') or `_CTRL` ('c'). Shared by every frontend's `vfg_source` — they all
    build the same `_VFG`."""
    labels = [g.nodes[i] for i in range(len(g.nodes))]
    return labels, list(g.edges)


def vfg(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[tuple[int, int, str]]]:
    """A function body's value-flow graph — the EXPRESSION layer of the code-property graph (design
    §5c), the level below the call graph: node labels + data/control edges. Advisory, computed on
    demand; `fingerprint` is the order/name-invariant digest of this same graph."""
    return _serialize_vfg(_build_vfg(fn))


def _walk_functions(source: str, build):
    """Shared traversal for `fingerprint_source` / `vfg_source`: apply `build(fn_node)` to every
    function/method keyed by qualified name (`Class.method`, nested `outer.inner`). Returns {} (or
    partial) on a syntax error or too-deep AST — advisory, never raises. A deep-but-valid expression
    (a long `a + a + …` chain in a generated builder) parses fine but can overflow the recursive
    walk; the extractor indexes such files (extract/python.py guards `ast.parse`), so the body layer
    must degrade, not crash (R156)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {}
    out: dict = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                try:
                    out[prefix + child.name] = build(child)
                except RecursionError:
                    pass  # deep-but-valid body — skip this function, keep the rest (advisory)
                visit(child, prefix + child.name + ".")
            else:
                # descend through control-flow / expression nodes at the SAME qual level — control
                # flow adds no qualname level, matching the extractor (python.py). Without this, a
                # def nested in if/for/while/with/try (e.g. `if TYPE_CHECKING:`, lazy imports) is
                # skipped and becomes invisible to find_similar(structure) / graph_diff (panel R155).
                visit(child, prefix)

    try:
        visit(tree, "")
    except RecursionError:
        return out  # a too-deep AST overflowed the walk itself — return what we have, never raise
    return out


def fingerprint_source(source: str) -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a Python source string, keyed by qualified name
    (`Class.method`, nested `outer.inner`). Returns {} (or partial results) on a syntax error or a
    too-deep AST — advisory, never raises."""
    return _walk_functions(source, fingerprint)


def vfg_source(source: str) -> dict[str, tuple[list[str], list[tuple[int, int, str]]]]:
    """Value-flow graph of every function/method in a Python source string, keyed by qualified name
    — the EXPRESSION-layer companion to `fingerprint_source` (identical keys). Computed on demand,
    never persisted, advisory; the raw graph `get_matrix(layer="expression")` drills into."""
    return _walk_functions(source, vfg)


_PDG_BLOCK_FIELDS = ("body", "orelse", "finalbody")


def _build_pdg(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[tuple[int, int, str]]]:
    """The STATEMENT layer (design §5c) — a program-dependence graph of the function body: nodes are
    statements (+ a synthetic ENTRY carrying the parameters), CONTROL edges ('C') link a statement to
    the header (if/for/while/try/with) it nests under, DATA edges ('D') link a def to a later use (a
    sequential reaching-def approximation — no SSA/alias analysis; see research/03-pdg/FINDINGS.md).
    Nested defs/classes are opaque NESTED leaves. Order-invariant once WL-fingerprinted."""
    nodes: dict[int, str] = {}
    edges: list[tuple[int, int, str]] = []
    flat: list[tuple[int, ast.AST, int | None]] = []
    counter = 0

    def new_id(label: str) -> int:
        nonlocal counter
        i = counter
        counter += 1
        nodes[i] = label
        return i

    entry = new_id("ENTRY")

    def walk_block(stmts: list[ast.stmt], parent: int) -> None:
        for s in stmts:
            if isinstance(s, _OPAQUE):
                flat.append((new_id("NESTED"), s, parent))
                continue
            sid = new_id(type(s).__name__)
            flat.append((sid, s, parent))
            for field in _PDG_BLOCK_FIELDS:
                block = getattr(s, field, None)
                if isinstance(block, list):
                    walk_block([x for x in block if isinstance(x, ast.stmt)], sid)
            for handler in getattr(s, "handlers", []) or []:
                walk_block([x for x in handler.body if isinstance(x, ast.stmt)], sid)

    walk_block(fn.body, entry)

    def header_names(node: ast.AST) -> tuple[set[str], set[str]]:
        loads: set[str] = set()
        stores: set[str] = set()
        for field, value in ast.iter_fields(node):
            if field in _PDG_BLOCK_FIELDS or field == "handlers":
                continue  # a nested block is its own node, not part of this statement's header
            for v in (value if isinstance(value, list) else [value]):
                if isinstance(v, ast.AST):
                    for sub in ast.walk(v):
                        if isinstance(sub, ast.Name):
                            (stores if isinstance(sub.ctx, ast.Store) else loads).add(sub.id)
        return loads, stores

    last_def: dict[str, int] = {a.arg: entry for a in _all_args(fn)}
    for sid, node, parent in sorted(flat, key=lambda t: t[0]):
        if parent is not None:
            edges.append((parent, sid, "C"))
        loads, stores = header_names(node)
        for name in loads:
            if name in last_def and last_def[name] != sid:
                edges.append((last_def[name], sid, "D"))
        for name in stores:
            last_def[name] = sid
    labels = [nodes[i] for i in range(len(nodes))]
    return labels, edges


def pdg(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], list[tuple[int, int, str]]]:
    """A function body's program-dependence graph — the STATEMENT layer of the code-property graph
    (design §5c): statement nodes + control ('C') / data ('D') dependence edges. Advisory, computed
    on demand; Python-only so far (deep stdlib ast)."""
    return _build_pdg(fn)


def pdg_source(source: str) -> dict[str, tuple[list[str], list[tuple[int, int, str]]]]:
    """Program-dependence graph of every function/method in a Python source string, keyed by
    qualified name — the STATEMENT-layer companion to `fingerprint_source`/`vfg_source` (identical
    keys). Computed on demand, never persisted, advisory; the graph `get_matrix(layer="statement")`
    drills into."""
    return _walk_functions(source, pdg)
