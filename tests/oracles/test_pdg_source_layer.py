"""Oracle for the STATEMENT layer's public surface: `structure.pdg_source` (design §5c phase 2).

The STATEMENT layer is a program-dependence graph (PDG) per function: nodes = statements (+ a
synthetic ENTRY carrying the parameters), edges = CONTROL ('C', nested-under-a-header) and DATA
('D', a def reaching a later use). This oracle covers the Python frontend (`structure.pdg_source`,
deep stdlib `ast`); the JS family, Go, Rust, and C/C++ each have their own `pdg_source` + oracle. This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source` keys (shared `_walk_functions` traversal).
  2. every graph is well-formed — a non-empty label list starting with ENTRY, edges as
     `(int src, int dst, kind)` with kind 'C'/'D' and endpoints in range.
  3. the defining STATEMENT-layer property — **reorder-invariance**: swapping two INDEPENDENT
     statements does not change the WL fingerprint of the PDG (the dependence graph is the same),
     whereas the raw statement order did change. This is what the PDG buys over a token/AST sequence.
  4. sensitivity — introducing a real data dependence between two statements DOES change it.

Advisory layer — computed on demand, never feeds liveness.
"""
from __future__ import annotations

import ast
import collections
import hashlib
import os
import subprocess
import sys

from stitchgraph.core import structure


def _wl(labels: list[str], edges: list[tuple[int, int, str]], iters: int = 3) -> collections.Counter:
    """Weisfeiler-Lehman kernel bag over a serialized graph — mirrors structure._wl_features, but on
    the (labels, edges) pair `pdg`/`vfg` return (so the oracle needs no private _VFG)."""
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


def _pdg_wl(src: str) -> collections.Counter:
    fn = ast.parse(src).body[0]
    return _wl(*structure.pdg(fn))


_SAMPLE = (
    "def calc(data):\n"
    "    result = []\n"
    "    for x in data:\n"
    "        if x > 0:\n"
    "            result.append(x)\n"
    "    return result\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\ndef other(a):\n    return a\n"
    assert set(structure.pdg_source(src)) == set(structure.fingerprint_source(src)) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        assert isinstance(edges, list)
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    # `x = a+1` and `y = b+2` are independent; swapping them changes source order but NOT the
    # dependence graph — the PDG fingerprint must be identical. (AST-token / expression order would
    # differ; this is what the statement layer buys.)
    a = _pdg_wl("def f(a, b):\n    x = a + 1\n    y = b + 2\n    return x + y\n")
    b = _pdg_wl("def f(a, b):\n    y = b + 2\n    x = a + 1\n    return x + y\n")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    # Independent vs a genuine data dependence (`y = x + 2` now uses x) — different dependence graph,
    # so the fingerprints must differ (guards against the invariant over-collapsing everything).
    indep = _pdg_wl("def f(a, b):\n    x = a + 1\n    y = b + 2\n    return x + y\n")
    dep = _pdg_wl("def f(a, b):\n    x = a + 1\n    y = x + 2\n    return x + y\n")
    assert indep != dep


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "def (:\n", "not python at all !!!", "def f(:\n  pass"):
        assert structure.pdg_source(bad) == {} or isinstance(structure.pdg_source(bad), dict)


# A function whose single statement reads several distinct names each defined in a different prior
# statement — so the data edges into the last statement are emitted from a set of load names. If that
# set is iterated in hash order, the edge LIST order (and get_matrix's `cells`) becomes
# PYTHONHASHSEED-dependent — non-reproducible across processes (R205).
_MULTIREAD = (
    "def f(p):\n"
    "    a = p\n"
    "    b = p\n"
    "    c = p\n"
    "    d = p\n"
    "    e = p\n"
    "    z = a + b + c + d + e\n"
    "    return z\n"
)


def _pdg_edges_under_seed(seed: str) -> str:
    """Serialize `pdg`'s edge list (order-sensitive) from a fresh interpreter pinned to `seed`.

    PYTHONHASHSEED must be set in the CHILD's env (not inherited): if the parent process ever pins it
    — a common CI reproducibility setting — inheriting would make every child share one seed and the
    test would go inert (pass even against the buggy set-iteration code). Setting it per child forces
    genuinely different string-set iteration orders, so a regression actually diverges."""
    prog = (
        "import ast;from stitchgraph.core import structure\n"
        f"fn=ast.parse({_MULTIREAD!r}).body[0]\n"
        "print([list(e) for e in structure.pdg(fn)[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_descends_into_match_case_bodies():
    # ast.Match is the one compound statement whose sub-statements live in cases[].body, not in
    # body/orelse/finalbody/handlers. Each case-body statement must become its own PDG node (parented
    # to the Match), and the Match header must NOT absorb case-body names (R210). An if/else and the
    # equivalent match should both expand their branch bodies into nodes.
    src = (
        "def f(x):\n"
        "    q = source()\n"
        "    match x:\n"
        "        case 1:\n"
        "            a = q + 1\n"
        "            return a\n"
        "        case _:\n"
        "            return q\n"
    )
    labels, edges = structure.pdg_source(src)["f"]
    # ENTRY, Assign(q), Match, Assign(a), Return(a), Return(q) — case bodies are present, not dropped.
    assert labels.count("Return") == 2, f"case bodies dropped: {labels}"
    assert "Assign" in labels[2:], f"in-case assign missing: {labels}"
    midx = labels.index("Match")
    # No q-def → Match misattribution: the q def (index 1) must reach the in-case `a = q + 1`, not the
    # Match header. So there is a D-edge from 1 into some node that is NOT the Match node.
    d_from_q = [(s, d) for s, d, k in edges if k == "D" and s == 1]
    assert d_from_q, "q def reaches nothing"
    assert all(d != midx for _, d in d_from_q), f"q misattributed to Match header: {d_from_q}"


def test_pdg_match_never_raises_on_exotic_patterns():
    # guard, sequence/mapping/class/capture patterns, wildcard — none may crash the builder.
    src = (
        "def g(x):\n"
        "    match x:\n"
        "        case [a, b] if a > 0:\n"
        "            return a + b\n"
        "        case {'k': v}:\n"
        "            return v\n"
        "        case str() as s:\n"
        "            return s\n"
        "        case _:\n"
        "            return None\n"
    )
    labels, edges = structure.pdg_source(src)["g"]
    assert labels[0] == "ENTRY" and labels.count("Return") == 4
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    # The edge list must be byte-identical regardless of PYTHONHASHSEED — set iteration order must
    # not leak into the output (R205). Distinct seeds shuffle string-set iteration order.
    seeds = ("0", "1", "7", "12345")
    outs = {_pdg_edges_under_seed(s) for s in seeds}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
