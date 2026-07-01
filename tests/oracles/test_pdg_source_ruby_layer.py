"""Oracle for the Ruby STATEMENT layer: `structure_ruby.pdg_source` (design §5c sweep, v3.16.0).

The Ruby companion to the Python/JS/Go/Rust/C++/Java/C# PDG oracles. The STATEMENT layer is a
per-function program-dependence graph: statement nodes (+ a synthetic ENTRY carrying the
parameters), control ('C', nested-under-a-header) and data ('D', a sequential reaching-def) edges.
Blocks / do-end / lambdas are opaque NESTED leaves. Ruby is expression-oriented: value-position
control folds. Requires the tree-sitter extra; skips cleanly without it. This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. every graph is well-formed — ENTRY-first labels, edges `(int, int, 'C'|'D')` with endpoints in
     range.
  3. reorder-invariance (independent-statement swap) + dependence sensitivity.
  4. never-raises on malformed / exotic Ruby.
  5. determinism — edge order byte-identical across PYTHONHASHSEED.
  6. Ruby constructs: assignment def-use, if/unless/case/while/until/for, value-position control
     folding, method call (no method-name leak), blocks opaque, string interpolation, rescue.

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

from stitchgraph.core import structure_ruby  # noqa: E402


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
    return _wl(*structure_ruby.pdg_source(body)["f"])


_SAMPLE = (
    "def calc(data, n)\n"
    "  total = 0\n"
    "  for i in 0..n\n"
    "    if data[i] > 0\n"
    "      total += data[i]\n"
    "    end\n"
    "  end\n"
    "  total\n"
    "end\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\ndef other(a)\n  a\nend\n"
    assert (set(structure_ruby.pdg_source(src))
            == set(structure_ruby.fingerprint_source(src))
            == set(structure_ruby.vfg_source(src))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_ruby.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("def f(a, b)\n  x = a + 1\n  y = b + 2\n  x + y\nend")
    b = _pdg_wl("def f(a, b)\n  y = b + 2\n  x = a + 1\n  x + y\nend")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("def f(a, b)\n  x = a + 1\n  y = b + 2\n  x + y\nend")
    dep = _pdg_wl("def f(a, b)\n  x = a + 1\n  y = x + 2\n  x + y\nend")
    assert indep != dep


def test_pdg_assignment_reaches_use():
    _labels, edges = structure_ruby.pdg_source(
        "def f(a)\n  x = a + 1\n  y = x + 2\n  y\nend")["f"]
    assert (1, 2, "D") in edges, "assignment did not reach its use"
    assert (2, 3, "D") in edges, "second assignment did not reach its use"


def test_pdg_handles_ruby_specific_statements():
    src = (
        "def f(xs, v)\n"
        "  sum = 0\n"
        "  while sum < 100\n"
        "    sum += 1\n"
        "  end\n"
        "  for e in xs\n"
        "    sum += e\n"
        "  end\n"
        "  case v\n"
        "  when 1 then sum\n"
        "  else 0\n"
        "  end\n"
        "end\n"
    )
    labels, edges = structure_ruby.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    for want in ("While", "For", "Case"):
        assert want in labels, f"{want} missing: {labels}"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_value_position_if_folds():
    # `x = if v then a else b end` — the value-position `if` folds its reads into the assignment.
    _l, e = structure_ruby.pdg_source(
        "def f(v)\n  x = if v then 1 else 2 end\n  x\nend")["f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "value-position if did not read the param"


def test_pdg_method_name_is_not_read_but_receiver_and_args_are():
    # `obj.compute(v)` — the method NAME is not a value; the receiver + args are reads.
    _l, e = structure_ruby.pdg_source(
        "def f(obj, v)\n  obj.compute(v)\n  0\nend")["f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "receiver/args not read"


def test_pdg_block_is_opaque():
    # A block `{ |q| ... }` is an opaque NESTED leaf — its body is not a statement node.
    labels, edges = structure_ruby.pdg_source(
        "def f(xs)\n  xs.each { |q| use(q) }\n  0\nend")["f"]
    assert labels[0] == "ENTRY"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_string_interpolation_reads_holes():
    _l, e = structure_ruby.pdg_source('def f(v)\n  s = "val #{v}"\n  s\nend')["f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "interpolation hole read of param dropped"


def test_pdg_rescue_clause():
    src = (
        "def f(v)\n"
        "  begin\n"
        "    risky(v)\n"
        "  rescue StandardError => e\n"
        "    handle(e)\n"
        "  end\n"
        "end\n"
    )
    labels, edges = structure_ruby.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "def", "def f(", "@@@ not ruby", "def f\n", "class C\n", "if x\n"):
        assert isinstance(structure_ruby.pdg_source(bad), dict)


_MULTIREAD = (
    "def f(p)\n"
    "  a = p; b = p; c = p; d = p; e = p\n"
    "  z = a + b + c + d + e\n"
    "  z\n"
    "end\n"
)


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_ruby\n"
        f"g = structure_ruby.pdg_source({_MULTIREAD!r})['f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
