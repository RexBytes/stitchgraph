"""Oracle for the Go STATEMENT layer: `structure_go.pdg_source` (design §5c sweep, v3.11.0).

The Go companion to the Python/JS PDG oracles. The STATEMENT layer is a per-function
program-dependence graph: statement nodes (+ a synthetic ENTRY carrying the parameters and
receiver), control ('C', nested-under-a-header) and data ('D', a sequential reaching-def) edges.
Nested functions (`func_literal`) are opaque NESTED leaves. Requires the tree-sitter extra; skips
cleanly without it. This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. every graph is well-formed — ENTRY-first labels, edges `(int, int, 'C'|'D')` with endpoints in
     range.
  3. reorder-invariance (independent-statement swap) + dependence sensitivity.
  4. never-raises on malformed / exotic Go (select/type-switch/defer/goroutine/range).
  5. determinism — edge order byte-identical across PYTHONHASHSEED.

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

from stitchgraph.core import structure_go  # noqa: E402


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


def _pdg_wl(body: str) -> collections.Counter:
    return _wl(*structure_go.pdg_source("package m\n" + body)["f"])


_SAMPLE = (
    "package m\n"
    "func calc(data []int) int {\n"
    "\ttotal := 0\n"
    "\tfor _, x := range data {\n"
    "\t\tif x > 0 {\n"
    "\t\t\ttotal += x\n"
    "\t\t}\n"
    "\t}\n"
    "\treturn total\n"
    "}\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\nfunc other(a int) int { return a }\n"
    assert (set(structure_go.pdg_source(src))
            == set(structure_go.fingerprint_source(src))
            == set(structure_go.vfg_source(src))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_go.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("func f(a, b int) int { x := a + 1; y := b + 2; return x + y }")
    b = _pdg_wl("func f(a, b int) int { y := b + 2; x := a + 1; return x + y }")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("func f(a, b int) int { x := a + 1; y := b + 2; return x + y }")
    dep = _pdg_wl("func f(a, b int) int { x := a + 1; y := x + 2; return x + y }")
    assert indep != dep


def test_pdg_handles_go_specific_statements():
    # range/for, type-switch, select, defer, goroutine, send — none may crash or malform.
    src = (
        "package m\n"
        "func f(ch chan int, xs []int, v interface{}) int {\n"
        "\tdefer cleanup()\n"
        "\tgo worker(ch)\n"
        "\tsum := 0\n"
        "\tfor i, x := range xs {\n"
        "\t\tsum += x + i\n"
        "\t}\n"
        "\tswitch t := v.(type) {\n"
        "\tcase int:\n"
        "\t\treturn t\n"
        "\tdefault:\n"
        "\t\treturn 0\n"
        "\t}\n"
        "\tselect {\n"
        "\tcase m := <-ch:\n"
        "\t\treturn m\n"
        "\tdefault:\n"
        "\t\treturn sum\n"
        "\t}\n"
        "}\n"
    )
    labels, edges = structure_go.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    assert "For" in labels and "Switch" in labels and "Select" in labels
    assert "Defer" in labels and "Go" in labels
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "package", "func (", "@@@ not go", "func f() {", "package m\nvar x ="):
        assert isinstance(structure_go.pdg_source(bad), dict)


_MULTIREAD = (
    "package m\n"
    "func f(p int) int {\n"
    "\ta := p; b := p; c := p; d := p; e := p\n"
    "\tz := a + b + c + d + e\n"
    "\treturn z\n"
    "}\n"
)


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_go\n"
        f"g = structure_go.pdg_source({_MULTIREAD!r})['f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
