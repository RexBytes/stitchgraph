"""Experiment 04 — expression-level value-flow graph (the level below the statement PDG).

Experiment 03's statement-PDG lost the temp-variable case (collect_direct ~ collect_tmp: 0.40)
because a copy like `flag = r.active` becomes its own statement node and lengthens the def->use
chain. The fix is **copy propagation at expression granularity**: model the flow of *values*
between *operations* (ATTR, CALL, BINOP, …), threading variables transparently so a pure copy
disappears. `flag = r.active; if flag:` then has the SAME value-flow as `if r.active:`.

Nodes are operations / control points; edges are value flow ('d') and control ('c'). The result
is fingerprinted with the same Weisfeiler-Lehman kernel as experiment 03, so we can compare the
three levels head-to-head on identical fixtures.

Run: python research/04-expr-dfg/expr_dfg.py
"""
from __future__ import annotations

import ast
import collections
import hashlib
import pathlib
import sys

_OPAQUE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


class VFG:
    def __init__(self):
        self.nodes: dict[int, str] = {}
        self.edges: list[tuple[int, int, str]] = []
        self._c = 0

    def add(self, label: str) -> int:
        i = self._c
        self._c += 1
        self.nodes[i] = label
        return i

    def link(self, src, dst, kind):
        if src is not None and dst is not None:
            self.edges.append((src, dst, kind))


def build_vfg(fn):
    g = VFG()
    env: dict[str, int] = {a.arg: g.add("PARAM") for a in fn.args.args}
    free: dict[str, int] = {}

    def freevar(name: str) -> int:
        if name not in free:
            free[name] = g.add("FREE")
        return free[name]

    def bind(target, val):
        if isinstance(target, ast.Name):
            env[target.id] = val           # copy propagation: var -> producing node
        elif isinstance(target, (ast.Tuple, ast.List)):
            for e in target.elts:
                bind(e, val)
        elif isinstance(target, ast.Attribute):
            n = g.add("SETATTR")
            g.link(val, n, "d")
            g.link(ev(target.value, None), n, "d")
        elif isinstance(target, ast.Subscript):
            n = g.add("SETITEM")
            g.link(val, n, "d")
            g.link(ev(target.value, None), n, "d")

    def ev(node, ctrl):
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
            g.link(ev(node.value, ctrl), n, "d")
            g.link(ctrl, n, "c")
            return n
        if isinstance(node, ast.Call):
            n = g.add("CALL")
            g.link(ev(node.func, ctrl), n, "d")
            for a in node.args:
                g.link(ev(a, ctrl), n, "d")
            for kw in node.keywords:
                g.link(ev(kw.value, ctrl), n, "d")
            g.link(ctrl, n, "c")
            return n
        if isinstance(node, ast.BinOp):
            n = g.add("BINOP")
            g.link(ev(node.left, ctrl), n, "d")
            g.link(ev(node.right, ctrl), n, "d")
            g.link(ctrl, n, "c")
            return n
        if isinstance(node, ast.BoolOp):
            n = g.add("BOOLOP")
            for v in node.values:
                g.link(ev(v, ctrl), n, "d")
            return n
        if isinstance(node, ast.Compare):
            n = g.add("CMP")
            g.link(ev(node.left, ctrl), n, "d")
            for c in node.comparators:
                g.link(ev(c, ctrl), n, "d")
            return n
        if isinstance(node, ast.UnaryOp):
            n = g.add("UNARY")
            g.link(ev(node.operand, ctrl), n, "d")
            return n
        if isinstance(node, ast.Subscript):
            n = g.add("SUBSCRIPT")
            g.link(ev(node.value, ctrl), n, "d")
            return n
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            n = g.add("SEQ")
            for e in node.elts:
                g.link(ev(e, ctrl), n, "d")
            return n
        if isinstance(node, ast.Dict):
            n = g.add("DICT")
            for v in node.values:
                g.link(ev(v, ctrl), n, "d")
            return n
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            n = g.add("COMPR")
            for gen in node.generators:
                it = g.add("ITERVAR")
                g.link(ev(gen.iter, ctrl), it, "d")
                bind(gen.target, it)
                for cond in gen.ifs:
                    g.link(ev(cond, ctrl), n, "d")
            if isinstance(node, ast.DictComp):
                g.link(ev(node.key, ctrl), n, "d")
                g.link(ev(node.value, ctrl), n, "d")
            else:
                g.link(ev(node.elt, ctrl), n, "d")
            return n
        if isinstance(node, ast.IfExp):
            n = g.add("IFEXP")
            g.link(ev(node.test, ctrl), n, "d")
            g.link(ev(node.body, ctrl), n, "d")
            g.link(ev(node.orelse, ctrl), n, "d")
            return n
        # fallback: a generic node fed by any sub-expressions
        n = g.add(type(node).__name__.upper())
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.expr):
                g.link(ev(ch, ctrl), n, "d")
        return n

    def do(s, ctrl):
        if isinstance(s, ast.Assign):
            val = ev(s.value, ctrl)
            for t in s.targets:
                bind(t, val)
        elif isinstance(s, ast.AnnAssign):
            if s.value is not None:
                bind(s.target, ev(s.value, ctrl))
        elif isinstance(s, ast.AugAssign):
            n = g.add("BINOP")
            g.link(ev(s.target, ctrl), n, "d")
            g.link(ev(s.value, ctrl), n, "d")
            g.link(ctrl, n, "c")
            bind(s.target, n)
        elif isinstance(s, ast.Return):
            n = g.add("RETURN")
            g.link(ev(s.value, ctrl), n, "d")
            g.link(ctrl, n, "c")
        elif isinstance(s, ast.Expr):
            ev(s.value, ctrl)
        elif isinstance(s, ast.If):
            b = g.add("BRANCH")
            g.link(ev(s.test, ctrl), b, "d")
            g.link(ctrl, b, "c")
            for x in s.body + s.orelse:
                do(x, b)
        elif isinstance(s, (ast.For, ast.AsyncFor)):
            lp = g.add("LOOP")
            g.link(ev(s.iter, ctrl), lp, "d")
            g.link(ctrl, lp, "c")
            it = g.add("ITERVAR")
            g.link(lp, it, "d")
            bind(s.target, it)
            for x in s.body + s.orelse:
                do(x, lp)
        elif isinstance(s, ast.While):
            lp = g.add("LOOP")
            g.link(ev(s.test, ctrl), lp, "d")
            g.link(ctrl, lp, "c")
            for x in s.body:
                do(x, lp)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                v = ev(item.context_expr, ctrl)
                if item.optional_vars:
                    bind(item.optional_vars, v)
            for x in s.body:
                do(x, ctrl)
        elif isinstance(s, ast.Try):
            for x in s.body + s.orelse + s.finalbody:
                do(x, ctrl)
            for h in s.handlers:
                for x in h.body:
                    do(x, ctrl)
        elif isinstance(s, _OPAQUE):
            g.add("NESTED")
        else:
            n = g.add(type(s).__name__.upper())
            for ch in ast.iter_child_nodes(s):
                if isinstance(ch, ast.expr):
                    g.link(ev(ch, ctrl), n, "d")
            g.link(ctrl, n, "c")

    for s in fn.body:
        do(s, None)
    return g.nodes, g.edges


def wl_labels(nodes, edges, iters: int = 3) -> collections.Counter:
    inc = collections.defaultdict(list)
    for s, d, k in edges:
        inc[d].append(("<" + k, s))
        inc[s].append((">" + k, d))
    labels = dict(nodes)
    feats = collections.Counter(f"0:{lab}" for lab in labels.values())
    for it in range(1, iters + 1):
        nxt = {}
        for n in nodes:
            sig = sorted((tag, labels[m]) for tag, m in inc.get(n, []))
            nxt[n] = hashlib.md5((labels[n] + "|" + repr(sig)).encode()).hexdigest()[:8]
        labels = nxt
        feats.update(f"{it}:{lab}" for lab in labels.values())
    return feats


def cosine(a: collections.Counter, b: collections.Counter) -> float:
    keys = set(a) | set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def fingerprint(fn) -> collections.Counter:
    return wl_labels(*build_vfg(fn))


def _demo():
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "03-pdg"))
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "02-body-matrix"))
    import body_matrix
    import pdg

    fx = pathlib.Path(__file__).parent.parent / "03-pdg" / "fixtures" / "pdg_clones.py"
    tree = ast.parse(fx.read_text())
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    pairs = [("collect_direct", "collect_tmp"),
             ("interleave_a", "interleave_b"),
             ("collect_direct", "scale_list")]
    print("Three levels of body matrix, head-to-head (cosine similarity):\n")
    print(f"  {'pair':<32}  {'AST-token':>9}  {'stmt-PDG':>9}  {'expr-DFG':>9}")
    for a, b in pairs:
        ast_tok = body_matrix._cosine(
            collections.Counter(body_matrix._tokens(fns[a].body)),
            collections.Counter(body_matrix._tokens(fns[b].body)))
        spdg = cosine(pdg.wl_labels(*pdg.build_pdg(fns[a])),
                      pdg.wl_labels(*pdg.build_pdg(fns[b])))
        edfg = cosine(fingerprint(fns[a]), fingerprint(fns[b]))
        print(f"  {a+' ~ '+b:<32}  {ast_tok:>9.2f}  {spdg:>9.2f}  {edfg:>9.2f}")

    print("\nExpected: expr-DFG RECOVERS collect_direct~collect_tmp (temp-var folded) where stmt-PDG "
          "dropped it, KEEPS interleave high (order-invariant), and rates scale_list low.")


if __name__ == "__main__":
    _demo()
