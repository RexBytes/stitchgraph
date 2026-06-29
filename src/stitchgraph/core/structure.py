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
            n = g.add("BINOP")
            g.link(ev(node.left, ctrl), n, _DATA)
            g.link(ev(node.right, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
            return n
        if isinstance(node, ast.BoolOp):
            n = g.add("BOOLOP")
            for v in node.values:
                g.link(ev(v, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.Compare):
            n = g.add("CMP")
            g.link(ev(node.left, ctrl), n, _DATA)
            for c in node.comparators:
                g.link(ev(c, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.UnaryOp):
            n = g.add("UNARY")
            g.link(ev(node.operand, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.Subscript):
            n = g.add("SUBSCRIPT")
            g.link(ev(node.value, ctrl), n, _DATA)
            return n
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            n = g.add("SEQ")
            for e in node.elts:
                g.link(ev(e, ctrl), n, _DATA)
            return n
        if isinstance(node, ast.Dict):
            n = g.add("DICT")
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
        # fallback: a generic node fed by any sub-expressions
        n = g.add(type(node).__name__.upper())
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.expr):
                g.link(ev(ch, ctrl), n, _DATA)
        return n

    def do(s: ast.stmt, ctrl: int | None) -> None:
        if isinstance(s, ast.Assign):
            val = ev(s.value, ctrl)
            for t in s.targets:
                bind(t, val)
        elif isinstance(s, ast.AnnAssign):
            if s.value is not None:
                bind(s.target, ev(s.value, ctrl))
        elif isinstance(s, ast.AugAssign):
            n = g.add("BINOP")
            g.link(ev(s.target, ctrl), n, _DATA)
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
        elif isinstance(s, ast.Try):
            for x in s.body + s.orelse + s.finalbody:
                do(x, ctrl)
            for h in s.handlers:
                for x in h.body:
                    do(x, ctrl)
        elif isinstance(s, ast.Raise):
            n = g.add("RAISE")
            for ch in ast.iter_child_nodes(s):
                if isinstance(ch, ast.expr):
                    g.link(ev(ch, ctrl), n, _DATA)
            g.link(ctrl, n, _CTRL)
        elif isinstance(s, _OPAQUE):
            g.add("NESTED")
        # pass / break / continue / global / nonlocal / import: no value-flow contribution

    for s in fn.body:
        do(s, None)
    return g


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
    """Cosine similarity of two fingerprints, in [0.0, 1.0]. 1.0 = identical structure."""
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def fingerprint_source(source: str) -> dict[str, collections.Counter[str]]:
    """Fingerprint every function/method in a Python source string, keyed by qualified name
    (`Class.method`, nested `outer.inner`). Returns {} on a syntax error — advisory, never raises.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, collections.Counter[str]] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[prefix + child.name] = fingerprint(child)
                visit(child, prefix + child.name + ".")

    visit(tree, "")
    return out
