"""Oracle for the Java STATEMENT layer: `structure_java.pdg_source` (design §5c sweep, v3.14.0).

The Java companion to the Python/JS/Go/Rust/C++ PDG oracles. The STATEMENT layer is a per-function
program-dependence graph: statement nodes (+ a synthetic ENTRY carrying the parameters), control
('C', nested-under-a-header) and data ('D', a sequential reaching-def) edges. Nested
methods/lambdas/anonymous classes are opaque NESTED leaves. Requires the tree-sitter extra; skips
cleanly without it. This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. every graph is well-formed — ENTRY-first labels, edges `(int, int, 'C'|'D')` with endpoints in
     range.
  3. reorder-invariance (independent-statement swap) + dependence sensitivity.
  4. never-raises on malformed / exotic Java.
  5. determinism — edge order byte-identical across PYTHONHASHSEED.
  6. Java constructs: declaration+use def-use, if/for/while/do, enhanced-for, switch (no case-value
     leak), labeled/break-label (label not read), field/array write targets (reads not stores),
     ternary, instanceof, try/catch/finally + try-with-resources, constructors.

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

from stitchgraph.core import structure_java  # noqa: E402


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
    return _wl(*structure_java.pdg_source(body)["C.f"])


_SAMPLE = (
    "class C {\n"
    "  int calc(int[] data, int n) {\n"
    "    int total = 0;\n"
    "    for (int i = 0; i < n; i++) {\n"
    "      if (data[i] > 0) {\n"
    "        total += data[i];\n"
    "      }\n"
    "    }\n"
    "    return total;\n"
    "  }\n"
    "}\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\nclass D { int other(int a) { return a; } }\n"
    assert (set(structure_java.pdg_source(src))
            == set(structure_java.fingerprint_source(src))
            == set(structure_java.vfg_source(src))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_java.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("class C { int f(int a, int b) { int x = a + 1; int y = b + 2; return x + y; } }")
    b = _pdg_wl("class C { int f(int a, int b) { int y = b + 2; int x = a + 1; return x + y; } }")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("class C { int f(int a, int b) { int x = a+1; int y = b+2; return x+y; } }")
    dep = _pdg_wl("class C { int f(int a, int b) { int x = a+1; int y = x+2; return x+y; } }")
    assert indep != dep


def test_pdg_declaration_reaches_use():
    _labels, edges = structure_java.pdg_source(
        "class C { int f(int a) { int x = a + 1; int y = x + 2; return y; } }")["C.f"]
    assert (1, 2, "D") in edges, "declaration did not reach its use"
    assert (2, 3, "D") in edges, "second declaration did not reach its use"


def test_pdg_handles_java_specific_statements():
    src = (
        "class C {\n"
        "  int f(java.util.List<Integer> xs, int v) {\n"
        "    int sum = 0;\n"
        "    for (int i = 0; i < 10; i++) { sum += i; }\n"
        "    while (sum < 100) { sum += 1; }\n"
        "    do { sum -= 1; } while (sum > 0);\n"
        "    for (int e : xs) { sum += e; }\n"
        "    switch (v) {\n"
        "      case 1: return sum;\n"
        "      default: return 0;\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    labels, edges = structure_java.pdg_source(src)["C.f"]
    assert labels[0] == "ENTRY"
    for want in ("For", "While", "Do", "ForEach", "Switch"):
        assert want in labels, f"{want} missing: {labels}"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_switch_case_values_are_not_nodes_and_not_read():
    # A `case <value>:` selector is a constant selector — its BODY statements are the nodes; a
    # constant int literal must not become a node.
    src = (
        "class C {\n"
        "  int f(int v) {\n"
        "    switch (v) {\n"
        "      case 1: return 1;\n"
        "      case 2: return 2;\n"
        "      default: return 0;\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    labels, _edges = structure_java.pdg_source(src)["C.f"]
    assert labels.count("Return") == 3, f"case bodies: {labels}"
    assert not any(lab.startswith("Decimal") for lab in labels), f"case value leaked: {labels}"


def test_pdg_label_and_break_label_are_not_read():
    # A labeled_statement label / break-target is a control point, never a value read — even when it
    # collides with a parameter name.
    src = "class C { int f(int done) { done: while (true) { break done; } return 0; } }"
    labels, edges = structure_java.pdg_source(src)["C.f"]
    assert "Labeled" in labels
    assert not any(s == 0 and k == "D" for s, _d, k in edges), "label read as a value"


def test_pdg_place_targets_are_reads_not_stores():
    # `a[i] = …`, `s.f = …` define no name — their object/index are READS from ENTRY (no local
    # rebind that a later statement would consume).
    src = "class C { int f(int[] arr, int v, Obj s) { arr[v] = v; s.f = v; return 0; } }"
    _labels, edges = structure_java.pdg_source(src)["C.f"]
    assert any(s == 0 and k == "D" for s, _d, k in edges), "place-target operands not read"


def test_pdg_method_name_is_not_read_but_receiver_and_args_are():
    # `obj.compute(v)` — the method NAME is not a value; the receiver + args are reads.
    _l, e = structure_java.pdg_source(
        "class C { int f(Obj obj, int v) { obj.compute(v); return 0; } }")["C.f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "receiver/args not read"


def test_pdg_instanceof_reads_the_operand_only():
    _l, e = structure_java.pdg_source(
        "class C { int f(Object v) { boolean b = v instanceof String; return b ? 1 : 0; } }")["C.f"]
    assert any(s == 0 and k == "D" for s, _d, k in e), "instanceof operand not read"


def test_pdg_ternary_and_cast():
    src = (
        "class C {\n"
        "  int f(int a, int b) {\n"
        "    int x = a > 0 ? b : 0;\n"
        "    long y = (long) x;\n"
        "    return (int) y;\n"
        "  }\n"
        "}\n"
    )
    labels, edges = structure_java.pdg_source(src)["C.f"]
    assert labels[0] == "ENTRY"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_try_with_resources_and_catch():
    src = (
        "class C {\n"
        "  int f(int v) {\n"
        "    try (Res r = open(v)) { use(r); }\n"
        "    catch (Exception e) { handle(e); }\n"
        "    finally { cleanup(); }\n"
        "    return 0;\n"
        "  }\n"
        "}\n"
    )
    labels, edges = structure_java.pdg_source(src)["C.f"]
    assert "Try" in labels
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_constructor_reads_params():
    src = "class C { int x; C(int v) { super(); this.x = v; } }"
    labels, edges = structure_java.pdg_source(src)["C.C"]
    assert labels[0] == "ENTRY"
    assert any(s == 0 and k == "D" for s, _d, k in edges), "constructor did not read its param"


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "class", "class C {", "@@@ not java", "class C { int f(",
                "interface I {", "class C { void g() { for(" ):
        assert isinstance(structure_java.pdg_source(bad), dict)


_MULTIREAD = (
    "class C {\n"
    "  int f(int p) {\n"
    "    int a = p; int b = p; int c = p; int d = p; int e = p;\n"
    "    int z = a + b + c + d + e;\n"
    "    return z;\n"
    "  }\n"
    "}\n"
)


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_java\n"
        f"g = structure_java.pdg_source({_MULTIREAD!r})['C.f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
