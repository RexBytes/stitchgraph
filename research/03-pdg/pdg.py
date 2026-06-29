"""Experiment 03 — PDG (program-dependence-graph) body matrix; the level below experiment 02.

experiment 02 fingerprints a function by its *normalised AST token sequence* — order-sensitive,
and fooled by temp-variable refactors that reorder/split statements. This spike builds the actual
DEPENDENCE matrix of a function body:

  * nodes  = statements (+ a synthetic ENTRY carrying the parameters), labelled by AST kind;
  * CONTROL edges = a statement nested in an if/for/while/try/with body depends on its header;
  * DATA edges    = a variable defined in statement A and used in statement B (sequential
                    reaching-def approximation — honest limit, see FINDINGS).

This is literally "the matrix of the implementation code": an adjacency matrix over statements.
We then fingerprint it order-invariantly with a Weisfeiler-Lehman label refinement (iteratively
hash each node's label together with its neighbours' labels), so two functions with the same
dependence shape but different statement order / temp-var factoring get a SIMILAR fingerprint
even when their AST token sequences diverge.

Run: python research/03-pdg/pdg.py [PATH]      (default: demo on fixtures, then src/stitchgraph)
"""
from __future__ import annotations

import ast
import collections
import difflib
import hashlib
import itertools
import pathlib
import sys

_BLOCK_FIELDS = ("body", "orelse", "finalbody")
_ANON = {ast.Name: "VAR", ast.Constant: "CONST", ast.arg: "ARG"}
_OPAQUE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


# --- the body matrix: build a PDG (statements + control/data edges) ----------------

def build_pdg(fn):
    nodes: dict[int, str] = {}
    edges: list[tuple[int, int, str]] = []
    flat: list[tuple[int, ast.AST, int | None]] = []
    counter = [0]

    def new_id(label: str) -> int:
        i = counter[0]
        counter[0] += 1
        nodes[i] = label
        return i

    entry = new_id("ENTRY")

    def walk_block(stmts, parent):
        for s in stmts:
            if isinstance(s, _OPAQUE):
                sid = new_id("NESTED")
                flat.append((sid, s, parent))
                continue
            sid = new_id(type(s).__name__)
            flat.append((sid, s, parent))
            for field in _BLOCK_FIELDS:
                block = getattr(s, field, None)
                if isinstance(block, list):
                    walk_block([x for x in block if isinstance(x, ast.stmt)], sid)
            for handler in getattr(s, "handlers", []) or []:
                walk_block([x for x in handler.body if isinstance(x, ast.stmt)], sid)

    walk_block(fn.body, entry)

    def header_names(node):
        loads, stores = set(), set()
        for field, value in ast.iter_fields(node):
            if field in _BLOCK_FIELDS or field == "handlers":
                continue
            for v in (value if isinstance(value, list) else [value]):
                if isinstance(v, ast.AST):
                    for sub in ast.walk(v):
                        if isinstance(sub, ast.Name):
                            (stores if isinstance(sub.ctx, ast.Store) else loads).add(sub.id)
        return loads, stores

    # sequential reaching-def approximation, in source order
    last_def: dict[str, int] = {a.arg: entry for a in fn.args.args}
    for sid, node, parent in sorted(flat, key=lambda t: t[0]):
        if parent is not None:
            edges.append((parent, sid, "C"))
        loads, stores = header_names(node)
        for name in loads:
            if name in last_def and last_def[name] != sid:
                edges.append((last_def[name], sid, "D"))
        for name in stores:
            last_def[name] = sid
    return nodes, edges


# --- order-invariant fingerprint: Weisfeiler-Lehman label refinement ---------------

def wl_labels(nodes, edges, iters: int = 3) -> collections.Counter:
    """Weisfeiler-Lehman *kernel* feature bag: accumulate node labels across ALL refinement
    iterations (h=0 raw kind, h=1 +immediate deps, ...), so similarity is GRADED — coarse
    iterations let partially-similar graphs overlap, refined ones reward identical structure.
    (Using only the final iteration is an isomorphism test: 0 overlap for any difference.)"""
    inc = collections.defaultdict(list)   # node -> [(dir+kind, neighbour)]
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


def _cosine(a: collections.Counter, b: collections.Counter) -> float:
    keys = set(a) | set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# --- the experiment-02 baseline (normalised AST token sequence) --------------------

def _ast_tokens(fn) -> list[str]:
    out: list[str] = []

    def rec(node):
        if isinstance(node, _OPAQUE):
            out.append("NESTED")
            return
        out.append(_ANON.get(type(node), type(node).__name__))
        for child in ast.iter_child_nodes(node):
            rec(child)

    for s in fn.body:
        rec(s)
    return out


def _ast_ratio(fa, fb) -> float:
    return difflib.SequenceMatcher(None, _ast_tokens(fa), _ast_tokens(fb)).ratio()


def render_matrix(nodes, edges) -> str:
    ids = sorted(nodes)
    idx = {n: i for i, n in enumerate(ids)}
    cell = {(idx[s], idx[d]): k for s, d, k in edges}
    head = "        " + " ".join(f"{i:>2}" for i in range(len(ids)))
    rows = [head]
    for n in ids:
        i = idx[n]
        line = " ".join(cell.get((i, j), " .") for j in range(len(ids)))
        rows.append(f"{i:>2} {nodes[n][:6]:<6} {line}")
    legend = "  (rows=from, cols=to; C=control dep, D=data dep)\n  nodes: " + \
        ", ".join(f"{i}={nodes[n]}" for i, n in enumerate(ids))
    return "\n".join(rows) + "\n" + legend


def _funcs(path: str):
    out = []
    root = pathlib.Path(path)
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((f"{f.name}::{node.name}", node))
    return out


def demo() -> None:
    fx = pathlib.Path(__file__).parent / "fixtures" / "pdg_clones.py"
    tree = ast.parse(fx.read_text())
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    print("== the body matrix, made visible: PDG of collect_direct ==")
    nd, ed = build_pdg(fns["collect_direct"])
    print(render_matrix(nd, ed))

    print("\n== PDG similarity vs experiment-02 AST-token similarity ==")
    pairs = [("collect_direct", "collect_tmp"), ("collect_direct", "scale_list"),
             ("interleave_a", "interleave_b")]
    for a, b in pairs:
        pa, pb = build_pdg(fns[a]), build_pdg(fns[b])
        pdg_sim = _cosine(wl_labels(*pa), wl_labels(*pb))
        ast_sim = _ast_ratio(fns[a], fns[b])
        verdict = "PDG sees the clone the AST-token misses" if pdg_sim - ast_sim > 0.1 else ""
        print(f"  {a} ~ {b}:  PDG={pdg_sim:.2f}  AST-token={ast_sim:.2f}  {verdict}")


def corpus(path: str, min_nodes: int = 10, thresh: float = 0.95) -> None:
    # min_nodes filters out tiny generic-shaped bodies (the PDG equivalent of Q1's
    # hub-callee noise): every "for x: out.append(...)" looks alike at coarse WL depth.
    funcs = [(q, build_pdg(fn)) for q, fn in _funcs(path) if len(fn.body) >= 3]
    sigs = [(q, wl_labels(n, e), n) for q, (n, e) in funcs if len(n) >= min_nodes]
    near = []
    for (qa, sa, na), (qb, sb, nb) in itertools.combinations(sigs, 2):
        sim = _cosine(sa, sb)
        if sim >= thresh and abs(len(na) - len(nb)) <= 2:
            near.append((sim, len(na), qa, qb))
    near.sort(reverse=True)
    print(f"\n== PDG near-clones in {path} "
          f"(>= {min_nodes} stmts, cosine >= {thresh}): {len(near)} ==")
    for sim, sz, qa, qb in near[:15]:
        print(f"  {sim:.2f} [{sz} stmts]  {qa}  ~  {qb}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        corpus(sys.argv[1])
    else:
        demo()
        corpus("src/stitchgraph")
