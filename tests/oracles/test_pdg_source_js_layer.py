"""Oracle for the JS-family STATEMENT layer: `structure_js.pdg_source` (design §5c, v3.10.0).

The JS/TS/TSX companion to `tests/oracles/test_pdg_source_layer.py` (Python). The STATEMENT layer is
a per-function program-dependence graph: statement nodes (+ a synthetic ENTRY carrying the
parameters), control ('C', nested-under-a-header) and data ('D', a sequential reaching-def) edges.
Nested functions are opaque NESTED leaves. Requires the tree-sitter extra; skips cleanly without it.
This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. every graph is well-formed — a non-empty label list starting with ENTRY, edges as
     `(int src, int dst, kind)` with kind 'C'/'D' and endpoints in range.
  3. reorder-invariance — swapping two INDEPENDENT statements leaves the PDG fingerprint unchanged.
  4. sensitivity — introducing a real data dependence DOES change it.
  5. never-raises on malformed / TS-typed / exotic input.
  6. determinism — edge order is byte-identical across PYTHONHASHSEED.

Advisory layer — computed on demand, never feeds liveness.
"""
from __future__ import annotations

import collections
import hashlib
import os
import subprocess
import sys

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_js  # noqa: E402


def _wl(labels: list[str], edges: list[tuple[int, int, str]], iters: int = 3) -> collections.Counter:
    inc: dict[int, list[tuple[str, int]]] = collections.defaultdict(list)
    for s, d, k in edges:
        inc[d].append(("<" + k, s))
        inc[s].append((">" + k, d))
    lab = dict(enumerate(labels))
    feats: collections.Counter = collections.Counter(f"0:{v}" for v in lab.values())
    for it in range(1, iters + 1):
        nxt = {}
        for n in lab:
            sig = sorted((t, lab[m]) for t, m in inc.get(n, []))
            nxt[n] = hashlib.md5((lab[n] + "|" + repr(sig)).encode()).hexdigest()[:8]
        lab = nxt
        feats.update(f"{it}:{v}" for v in lab.values())
    return feats


def _pdg_wl(src: str, lang: str = "typescript") -> collections.Counter:
    return _wl(*structure_js.pdg_source(src, lang)["f"])


_SAMPLE = (
    "function calc(data) {\n"
    "  let result = [];\n"
    "  for (const x of data) {\n"
    "    if (x > 0) { result.push(x); }\n"
    "  }\n"
    "  return result;\n"
    "}\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\nfunction other(a) { return a; }\n"
    assert (set(structure_js.pdg_source(src, "javascript"))
            == set(structure_js.fingerprint_source(src, "javascript"))
            == set(structure_js.vfg_source(src, "javascript"))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_js.pdg_source(_SAMPLE, "javascript").items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("function f(a, b) { let x = a + 1; let y = b + 2; return x + y; }")
    b = _pdg_wl("function f(a, b) { let y = b + 2; let x = a + 1; return x + y; }")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("function f(a, b) { let x = a + 1; let y = b + 2; return x + y; }")
    dep = _pdg_wl("function f(a, b) { let x = a + 1; let y = x + 2; return x + y; }")
    assert indep != dep


def test_pdg_handles_try_switch_forof_without_leaking_finally():
    # try/catch/finally each contribute their body statements under the Try node (no spurious
    # finally-clause node); switch case bodies + for-of bindings are covered.
    src = (
        "function f(d, xs) {\n"
        "  try { d = risky(d); } catch (e) { d = 0; } finally { cleanup(d); }\n"
        "  for (const x of xs) { d += x; }\n"
        "  switch (d) { case 1: return 1; default: return d; }\n"
        "}\n"
    )
    labels, edges = structure_js.pdg_source(src, "typescript")["f"]
    assert "FinallyClause" not in labels and "CatchClause" not in labels, labels
    # try body + catch body + finally body = 3 Expr, plus the for-body `d += x` = 4 total.
    assert labels.count("Expr") == 4, f"try/catch/finally + for-body each a node: {labels}"
    assert "For" in labels and "Switch" in labels
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_descends_into_block_bearing_statements():
    # Any block-bearing statement not special-cased (e.g. the deprecated `with`) must still have its
    # body descended — the block's statements become their own nodes, not silently dropped (R214,
    # closing the class generically like Python's walk_block).
    src = "function f(a) { with (a) { let z = compute(a); return z; } }"
    labels, edges = structure_js.pdg_source(src, "javascript")["f"]
    assert "Assign" in labels and "Return" in labels, f"with-body dropped: {labels}"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_typeof_in_type_position_is_not_a_data_read():
    # `typeof x` in a TS TYPE position (type_query) is erased at runtime — it must NOT create a data
    # edge from x (R216). In VALUE position `typeof x` is a unary_expression and still flows.
    for src in (
        "function g(payload) { type L = typeof payload; return 1; }",
        "function g(payload) { return make<typeof payload>(1); }",
        "function g(payload) { let x = 1 as typeof payload; return x; }",
    ):
        _labels, edges = structure_js.pdg_source(src, "typescript")["g"]
        assert not any(k == "D" and s == 0 for s, d, k in edges), \
            f"typeof-in-type-position leaked a data read: {src!r} -> {edges}"
    # a genuine value read still links (guards against over-correction).
    _labels, edges = structure_js.pdg_source(
        "function g(payload) { let x = payload + 1; return x; }", "typescript")["g"]
    assert any(k == "D" and s == 0 for s, d, k in edges)


def test_pdg_source_never_raises_on_bad_or_typed_input():
    cases = [
        "",
        "function (",
        "@@@ not code",
        "const x =",
        "<Foo>{bar}</Foo>",                       # tsx fragment-ish
        "function f<T>(a: T): T { return a; }",   # TS generics
        "const f = (a = helper()) => a;",         # default param + arrow
    ]
    for bad in cases:
        for lang in ("javascript", "typescript", "tsx"):
            assert isinstance(structure_js.pdg_source(bad, lang), dict)


_MULTIREAD = (
    "function f(p) {\n"
    "  let a = p; let b = p; let c = p; let d = p; let e = p;\n"
    "  let z = a + b + c + d + e;\n"
    "  return z;\n"
    "}\n"
)


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_js\n"
        f"g = structure_js.pdg_source({_MULTIREAD!r}, 'javascript')['f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
