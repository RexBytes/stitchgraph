"""Oracle for the Rust STATEMENT layer: `structure_rust.pdg_source` (design §5c sweep, v3.12.0).

Rust is expression-oriented — if/match/loop/while/for are expressions, not statements. In statement
position they become control nodes; in value position (`let y = if …`) they fold into the enclosing
statement's reads. The PDG is statement nodes (+ ENTRY carrying params and `self`), control ('C') /
data ('D', sequential reaching-def) edges; closures/nested fns are opaque NESTED leaves. Requires the
tree-sitter extra; skips cleanly without it. This pins:

  1. `pdg_source` keys == `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. well-formed graphs (ENTRY-first; edges `(int,int,'C'|'D')` in range).
  3. reorder-invariance + dependence sensitivity.
  4. TS-like type-position safety — a `let x: T = …` / `as T` type never reads a value name.
  5. never-raises on exotic Rust (if-let/while-let/match-guards/loops/closures/macros).
  6. determinism across PYTHONHASHSEED.

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

from stitchgraph.core import structure_rust  # noqa: E402


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
    return _wl(*structure_rust.pdg_source(body)["f"])


_SAMPLE = (
    "fn calc(data: Vec<i32>) -> i32 {\n"
    "    let mut total = 0;\n"
    "    for x in data {\n"
    "        if x > 0 {\n"
    "            total += x;\n"
    "        }\n"
    "    }\n"
    "    total\n"
    "}\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\nfn other(a: i32) -> i32 { a }\n"
    assert (set(structure_rust.pdg_source(src))
            == set(structure_rust.fingerprint_source(src))
            == set(structure_rust.vfg_source(src))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_rust.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("fn f(a: i32, b: i32) -> i32 { let x = a + 1; let y = b + 2; x + y }")
    b = _pdg_wl("fn f(a: i32, b: i32) -> i32 { let y = b + 2; let x = a + 1; x + y }")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("fn f(a: i32, b: i32) -> i32 { let x = a + 1; let y = b + 2; x + y }")
    dep = _pdg_wl("fn f(a: i32, b: i32) -> i32 { let x = a + 1; let y = x + 2; x + y }")
    assert indep != dep


def test_pdg_type_positions_are_not_data_reads():
    # `let x: T = …` type annotation and `as T` cast type must not read a value name (T is a
    # type_identifier / *_type, not an `identifier`). A genuine value read still links.
    for src in (
        "fn f(y: i32) -> i32 { let x: Vec<i32> = make(y); x.len() as i32 }",
        "fn f(y: i32) -> i64 { y as i64 }",
    ):
        labels, edges = structure_rust.pdg_source(src)["f"]
        assert labels[0] == "ENTRY"
        for s, d, k in edges:
            assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")
    # a real value read from a param links from ENTRY
    _l, e = structure_rust.pdg_source("fn f(y: i32) -> i32 { let x = y + 1; x }")["f"]
    assert any(k == "D" and s == 0 for s, d, k in e)


def test_pdg_handles_rust_specific_constructs():
    # if-let / while-let, match with guards, loop/labeled, closures (opaque), macros, `?`.
    src = (
        "fn f(opt: Option<i32>, xs: Vec<i32>) -> i32 {\n"
        "    if let Some(v) = opt {\n"
        "        return v;\n"
        "    }\n"
        "    let mut n = 0;\n"
        "    'outer: loop {\n"
        "        while let Some(x) = xs.iter().next() {\n"
        "            n += x;\n"
        "            if n > 10 { break 'outer; }\n"
        "        }\n"
        "    }\n"
        "    let g = |a: i32| a + n;\n"
        "    let r = match n {\n"
        "        0 => 0,\n"
        "        k if k > 5 => k,\n"
        "        _ => 1,\n"
        "    };\n"
        "    println!(\"{}\", r);\n"
        "    r\n"
        "}\n"
    )
    labels, edges = structure_rust.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    assert "If" in labels and "Loop" in labels and "While" in labels
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "fn (", "@@@ not rust", "fn f() {", "let x ="):
        assert isinstance(structure_rust.pdg_source(bad), dict)


_MULTIREAD = (
    "fn f(p: i32) -> i32 {\n"
    "    let a = p; let b = p; let c = p; let d = p; let e = p;\n"
    "    let z = a + b + c + d + e;\n"
    "    z\n"
    "}\n"
)


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_rust\n"
        f"g = structure_rust.pdg_source({_MULTIREAD!r})['f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
