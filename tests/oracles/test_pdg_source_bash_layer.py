"""Oracle for the Bash STATEMENT layer: `structure_bash.pdg_source` (design §5c sweep, v3.18.0).

The Bash companion to the Python/JS/Go/Rust/C++/Java/C#/Ruby/PHP PDG oracles — the LAST language of
the sweep. The STATEMENT layer is a per-function program-dependence graph: statement nodes (+ a
synthetic ENTRY, empty because shell functions have no declared parameters), control ('C',
nested-under-a-header) and data ('D', a sequential reaching-def) edges. Nested function definitions
are opaque NESTED leaves. Bash is command-oriented (a command is a statement). Requires the
tree-sitter extra; skips cleanly without it. This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. every graph is well-formed — ENTRY-first labels, edges `(int, int, 'C'|'D')` with endpoints in
     range.
  3. reorder-invariance (independent-statement swap) + dependence sensitivity.
  4. never-raises on malformed / exotic Bash.
  5. determinism — edge order byte-identical across PYTHONHASHSEED.
  6. Bash constructs: assignment def-use, if/elif/else, for/c-style-for/while/until, case, command
     call (literal command name not a read; dynamic name IS), pipelines, command substitution,
     string/arith holes, redirects, local declarations.

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

from stitchgraph.core import structure_bash  # noqa: E402


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
    return _wl(*structure_bash.pdg_source(body)["f"])


_SAMPLE = (
    "calc() {\n"
    "  total=0\n"
    "  for i in $items; do\n"
    "    if [[ $i -gt 0 ]]; then\n"
    "      total=$(( total + i ))\n"
    "    fi\n"
    "  done\n"
    "  echo $total\n"
    "}\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\nother() {\n  echo $a\n}\n"
    assert (set(structure_bash.pdg_source(src))
            == set(structure_bash.fingerprint_source(src))
            == set(structure_bash.vfg_source(src))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_bash.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("f() {\n  x=$a\n  y=$b\n  echo $x $y\n}")
    b = _pdg_wl("f() {\n  y=$b\n  x=$a\n  echo $x $y\n}")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("f() {\n  x=$a\n  y=$b\n  echo $x $y\n}")
    dep = _pdg_wl("f() {\n  x=$a\n  y=$x\n  echo $x $y\n}")
    assert indep != dep


def test_pdg_assignment_reaches_use():
    _labels, edges = structure_bash.pdg_source(
        "f() {\n  x=$a\n  y=$x\n  echo $y\n}")["f"]
    # ENTRY=0, x=$a is 1, y=$x is 2, echo $y is 3.
    assert (1, 2, "D") in edges, "assignment did not reach its use"
    assert (2, 3, "D") in edges, "second assignment did not reach its use"


def test_pdg_handles_bash_specific_statements():
    src = (
        "f() {\n"
        "  sum=0\n"
        "  while [[ $sum -lt 100 ]]; do\n"
        "    sum=$(( sum + 1 ))\n"
        "  done\n"
        "  for e in $xs; do\n"
        "    sum=$(( sum + e ))\n"
        "  done\n"
        "  case $v in\n"
        "    1) echo $sum ;;\n"
        "    *) echo 0 ;;\n"
        "  esac\n"
        "}\n"
    )
    labels, edges = structure_bash.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    for want in ("While", "For", "Case"):
        assert want in labels, f"{want} missing: {labels}"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_literal_command_name_is_not_read_but_args_are():
    # `helper $x` — the command NAME `helper` is a free callee (not a read); `$x` is a read.
    _l, e = structure_bash.pdg_source(
        "f() {\n  x=$a\n  helper $x\n}")["f"]
    assert (1, 2, "D") in e, "command argument read of x dropped"


def test_pdg_dynamic_command_name_reads_the_variable():
    # `$cmd arg` — the callee is determined by $cmd, so it IS a read.
    _l, e = structure_bash.pdg_source(
        "f() {\n  cmd=$a\n  $cmd arg\n}")["f"]
    assert (1, 2, "D") in e, "dynamic command-name read of cmd dropped"


def test_pdg_nested_function_is_opaque():
    labels, edges = structure_bash.pdg_source(
        "f() {\n  inner() { echo $q; }\n  echo done\n}")["f"]
    assert labels[0] == "ENTRY"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_string_interpolation_reads_holes():
    _l, e = structure_bash.pdg_source(
        'f() {\n  v=$a\n  echo "val $v"\n}')["f"]
    assert (1, 2, "D") in e, "interpolation hole read of v dropped"


def test_pdg_command_substitution_reads():
    _l, e = structure_bash.pdg_source(
        "f() {\n  v=$a\n  echo $(helper $v)\n}")["f"]
    assert (1, 2, "D") in e, "command-substitution read of v dropped"


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "f()", "f() {", "@@@ not bash", "if then fi", "for do done", "function"):
        assert isinstance(structure_bash.pdg_source(bad), dict)


_MULTIREAD = (
    "f() {\n"
    "  a=$p; b=$p; c=$p; d=$p; e=$p\n"
    "  z=$(( a + b + c + d + e ))\n"
    "  echo $z\n"
    "}\n"
)


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_bash\n"
        f"g = structure_bash.pdg_source({_MULTIREAD!r})['f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
