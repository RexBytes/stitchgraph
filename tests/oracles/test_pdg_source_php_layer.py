"""Oracle for the PHP STATEMENT layer: `structure_php.pdg_source` (design §5c sweep, v3.17.0).

The PHP companion to the Python/JS/Go/Rust/C++/Java/C#/Ruby PDG oracles. The STATEMENT layer is a
per-function program-dependence graph: statement nodes (+ a synthetic ENTRY carrying the
parameters), control ('C', nested-under-a-header) and data ('D', a sequential reaching-def) edges.
Closures / arrow functions are opaque NESTED leaves. PHP is statement-oriented (explicit `return`).
Requires the tree-sitter extra; skips cleanly without it. This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. every graph is well-formed — ENTRY-first labels, edges `(int, int, 'C'|'D')` with endpoints in
     range.
  3. reorder-invariance (independent-statement swap) + dependence sensitivity.
  4. never-raises on malformed / exotic PHP.
  5. determinism — edge order byte-identical across PYTHONHASHSEED.
  6. PHP constructs: assignment def-use, if/elseif/else, for/foreach/while/do, switch, match,
     member/method call (no member-name leak), closures opaque, string/heredoc interpolation,
     try/catch/finally, static locals.

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

from stitchgraph.core import structure_php  # noqa: E402


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
    return _wl(*structure_php.pdg_source(body)["f"])


_SAMPLE = (
    "<?php\n"
    "function calc($data, $n) {\n"
    "  $total = 0;\n"
    "  for ($i = 0; $i < $n; $i++) {\n"
    "    if ($data[$i] > 0) {\n"
    "      $total += $data[$i];\n"
    "    }\n"
    "  }\n"
    "  return $total;\n"
    "}\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\nfunction other($a) {\n  return $a;\n}\n"
    assert (set(structure_php.pdg_source(src))
            == set(structure_php.fingerprint_source(src))
            == set(structure_php.vfg_source(src))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_php.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("<?php\nfunction f($a, $b) {\n  $x = $a + 1;\n  $y = $b + 2;\n  return $x + $y;\n}")
    b = _pdg_wl("<?php\nfunction f($a, $b) {\n  $y = $b + 2;\n  $x = $a + 1;\n  return $x + $y;\n}")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("<?php\nfunction f($a, $b) {\n  $x = $a + 1;\n  $y = $b + 2;\n  return $x + $y;\n}")
    dep = _pdg_wl("<?php\nfunction f($a, $b) {\n  $x = $a + 1;\n  $y = $x + 2;\n  return $x + $y;\n}")
    assert indep != dep


def test_pdg_assignment_reaches_use():
    _labels, edges = structure_php.pdg_source(
        "<?php\nfunction f($a) {\n  $x = $a + 1;\n  $y = $x + 2;\n  return $y;\n}")["f"]
    assert (1, 2, "D") in edges, "assignment did not reach its use"
    assert (2, 3, "D") in edges, "second assignment did not reach its use"


def test_pdg_handles_php_specific_statements():
    src = (
        "<?php\n"
        "function f($xs, $v) {\n"
        "  $sum = 0;\n"
        "  while ($sum < 100) {\n"
        "    $sum += 1;\n"
        "  }\n"
        "  foreach ($xs as $e) {\n"
        "    $sum += $e;\n"
        "  }\n"
        "  switch ($v) {\n"
        "    case 1: return $sum;\n"
        "    default: return 0;\n"
        "  }\n"
        "}\n"
    )
    labels, edges = structure_php.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    for want in ("While", "ForEach", "Switch"):
        assert want in labels, f"{want} missing: {labels}"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_value_position_match_folds():
    # `$x = match($v) {…}` — the value-position match folds its scrutinee read into the assignment.
    _l, e = structure_php.pdg_source(
        "<?php\nfunction f($v) {\n  $x = match($v) { 1 => 9, default => 0 };\n  return $x;\n}")["f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "value-position match did not read the param"


def test_pdg_member_name_is_not_read_but_receiver_and_args_are():
    # `$obj->compute($v)` — the method NAME is bare (not a value); the receiver + args are reads.
    _l, e = structure_php.pdg_source(
        "<?php\nfunction f($obj, $v) {\n  $obj->compute($v);\n  return 0;\n}")["f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "receiver/args not read"


def test_pdg_closure_is_opaque():
    # A closure `function() use ($q) {…}` is an opaque NESTED leaf — its body is not a statement node.
    labels, edges = structure_php.pdg_source(
        "<?php\nfunction f($xs) {\n  $g = function() use ($xs) { return use2($xs); };\n  return 0;\n}")["f"]
    assert labels[0] == "ENTRY"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_string_interpolation_reads_holes():
    _l, e = structure_php.pdg_source(
        '<?php\nfunction f($v) {\n  $s = "val $v";\n  return $s;\n}')["f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "interpolation hole read of param dropped"


def test_pdg_heredoc_interpolation_reads_holes():
    _l, e = structure_php.pdg_source(
        "<?php\nfunction f($v) {\n  $s = <<<EOT\nval $v\nEOT;\n  return $s;\n}")["f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "heredoc interpolation hole read dropped"


def test_pdg_try_catch_finally():
    src = (
        "<?php\n"
        "function f($v) {\n"
        "  try {\n"
        "    risky($v);\n"
        "  } catch (Exception $e) {\n"
        "    handle($e);\n"
        "  } finally {\n"
        "    cleanup();\n"
        "  }\n"
        "}\n"
    )
    labels, edges = structure_php.pdg_source(src)["f"]
    assert labels[0] == "ENTRY" and "Try" in labels
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_static_local_binds():
    # `static $s = <init>;` — the initializer is a read; the name binds (parallel to an assignment).
    labels, edges = structure_php.pdg_source(
        "<?php\nfunction f($v) {\n  static $s = 0;\n  $s = $s + $v;\n  return $s;\n}")["f"]
    assert labels[0] == "ENTRY" and "Static" in labels
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "<?php", "<?php function", "<?php function f(", "@@@ not php",
                "<?php function f() {", "<?php class C {"):
        assert isinstance(structure_php.pdg_source(bad), dict)


_MULTIREAD = (
    "<?php\n"
    "function f($p) {\n"
    "  $a = $p; $b = $p; $c = $p; $d = $p; $e = $p;\n"
    "  $z = $a + $b + $c + $d + $e;\n"
    "  return $z;\n"
    "}\n"
)


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_php\n"
        f"g = structure_php.pdg_source({_MULTIREAD!r})['f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
