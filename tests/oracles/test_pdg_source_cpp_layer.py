"""Oracle for the C/C++ STATEMENT layer: `structure_cpp.pdg_source` (design §5c sweep, v3.13.0).

The C/C++ companion to the Python/JS/Go/Rust PDG oracles. The STATEMENT layer is a per-function
program-dependence graph: statement nodes (+ a synthetic ENTRY carrying the parameters), control
('C', nested-under-a-header) and data ('D', a sequential reaching-def) edges. Nested
functions/lambdas are opaque NESTED leaves. One walker covers both C and C++. Requires the
tree-sitter extra; skips cleanly without it. This pins:

  1. `pdg_source` keys EXACTLY match `fingerprint_source`/`vfg_source` keys (shared `_walk`).
  2. every graph is well-formed — ENTRY-first labels, edges `(int, int, 'C'|'D')` with endpoints in
     range.
  3. reorder-invariance (independent-statement swap) + dependence sensitivity.
  4. never-raises on malformed / exotic C/C++.
  5. determinism — edge order byte-identical across PYTHONHASHSEED.
  6. C/C++ constructs: declaration+use def-use, if/for/while/do, for-range, switch case bodies (no
     case-value leak), goto/labeled (label not read), pointer/deref/subscript/field write targets
     (reads not stores), ternary, new/delete, member-init list.

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

from stitchgraph.core import structure_cpp  # noqa: E402


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
    return _wl(*structure_cpp.pdg_source(body)["f"])


_SAMPLE = (
    "int calc(int* data, int n) {\n"
    "  int total = 0;\n"
    "  for (int i = 0; i < n; i++) {\n"
    "    if (data[i] > 0) {\n"
    "      total += data[i];\n"
    "    }\n"
    "  }\n"
    "  return total;\n"
    "}\n"
)


def test_pdg_source_keys_match_fingerprint_source():
    src = _SAMPLE + "\nint other(int a) { return a; }\n"
    assert (set(structure_cpp.pdg_source(src))
            == set(structure_cpp.fingerprint_source(src))
            == set(structure_cpp.vfg_source(src))) != set()


def test_pdg_graphs_are_well_formed():
    for name, (labels, edges) in structure_cpp.pdg_source(_SAMPLE).items():
        assert labels and labels[0] == "ENTRY", f"{name}: PDG must start with ENTRY"
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int)
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{name}: edge index OOR"
            assert k in ("C", "D"), f"{name}: edge kind {k!r} not control/data"


def test_pdg_is_reorder_invariant_for_independent_statements():
    a = _pdg_wl("int f(int a, int b) { int x = a + 1; int y = b + 2; return x + y; }")
    b = _pdg_wl("int f(int a, int b) { int y = b + 2; int x = a + 1; return x + y; }")
    assert a == b


def test_pdg_distinguishes_a_real_dependence_change():
    indep = _pdg_wl("int f(int a, int b) { int x = a + 1; int y = b + 2; return x + y; }")
    dep = _pdg_wl("int f(int a, int b) { int x = a + 1; int y = x + 2; return x + y; }")
    assert indep != dep


def test_pdg_declaration_reaches_use():
    # a local declared then used: the def must reach the use.
    _labels, edges = structure_cpp.pdg_source(
        "int f(int a) { int x = a + 1; int y = x + 2; return y; }")["f"]
    # node1 = `int x = a+1`, node2 = `int y = x+2`, node3 = return y.
    assert (1, 2, "D") in edges, "declaration did not reach its use"
    assert (2, 3, "D") in edges, "second declaration did not reach its use"


def test_pdg_handles_cpp_specific_statements():
    # if/for/while/do/for-range/switch/try — none may crash or malform.
    src = (
        "int f(std::vector<int>& xs, int v) {\n"
        "  int sum = 0;\n"
        "  for (int i = 0; i < 10; i++) { sum += i; }\n"
        "  while (sum < 100) { sum += 1; }\n"
        "  do { sum -= 1; } while (sum > 0);\n"
        "  for (auto e : xs) { sum += e; }\n"
        "  switch (v) {\n"
        "    case 1: return sum;\n"
        "    default: return 0;\n"
        "  }\n"
        "  try { risky(); } catch (Err& e) { handle(e); }\n"
        "  return sum;\n"
        "}\n"
    )
    labels, edges = structure_cpp.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    for want in ("For", "While", "Do", "ForRange", "Switch", "Try"):
        assert want in labels, f"{want} missing: {labels}"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_switch_case_values_are_not_nodes_and_not_read():
    # A `case <value>:` selector is a constant selector — it must NOT become a PDG node, and a
    # collision with a param name must NOT thread a read (the case-operand class fixed in Go R219 /
    # Python R210). Only case BODIES are statements.
    src = (
        "int f(int v) {\n"
        "  switch (v) {\n"
        "    case 1: return 1;\n"
        "    case 2: return 2;\n"
        "    default: return 0;\n"
        "  }\n"
        "}\n"
    )
    labels, edges = structure_cpp.pdg_source(src)["f"]
    assert labels.count("Return") == 3, f"case bodies: {labels}"
    assert "NumberLiteral" not in labels, f"case value leaked as a node: {labels}"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_goto_and_label_are_not_read():
    # A statement_identifier (a labeled_statement label / goto target) is a control point, never a
    # value read — even when it collides with a parameter name.
    src = "int f(int done) { done: goto done; return 0; }"
    labels, edges = structure_cpp.pdg_source(src)["f"]
    assert "Labeled" in labels and "Goto" in labels
    assert not any(s == 0 and k == "D" for s, _d, k in edges), "label read as a value"


def test_pdg_place_targets_are_reads_not_stores():
    # `*p = …`, `a[i] = …`, `s.f = …` define no name — their object/index are READS. The pointer /
    # array / struct object stays bound at ENTRY (no local rebind).
    src = "int f(int* p, int* arr, int v, S s) { *p = v; arr[v] = v; s.f = v; return 0; }"
    _labels, edges = structure_cpp.pdg_source(src)["f"]
    # every one of the three place-assignments reads params from ENTRY (never mints a store the
    # later statements would consume).
    assert any(s == 0 and k == "D" for s, _d, k in edges), "place-target operands not read"


def test_pdg_ternary_new_delete():
    src = (
        "int f(int a, int b) {\n"
        "  int* p = new int(a);\n"
        "  int x = a ? b : 0;\n"
        "  delete p;\n"
        "  return x;\n"
        "}\n"
    )
    labels, edges = structure_cpp.pdg_source(src)["f"]
    assert labels[0] == "ENTRY"
    for s, d, k in edges:
        assert 0 <= s < len(labels) and 0 <= d < len(labels) and k in ("C", "D")


def test_pdg_constructor_member_init_list():
    # A constructor member-initializer-list is a sibling of the body; each member init reads its
    # operands (`n(compute(x))` reads x), the member NAME is not a value.
    src = "struct S { int n; S(int x): n(compute(x)) {} };"
    labels, edges = structure_cpp.pdg_source(src)["S.S"]
    assert "MemberInit" in labels, labels
    assert any(s == 0 and k == "D" for s, _d, k in edges), "member-init did not read its param"


def test_pdg_source_never_raises_on_bad_input():
    for bad in ("", "int", "int f(", "@@@ not c++", "int f() {", "struct S {", "void g() { for("):
        assert isinstance(structure_cpp.pdg_source(bad), dict)


_MULTIREAD = (
    "int f(int p) {\n"
    "  int a = p; int b = p; int c = p; int d = p; int e = p;\n"
    "  int z = a + b + c + d + e;\n"
    "  return z;\n"
    "}\n"
)


def test_pdg_parenthesized_rmw_target_records_the_store():
    # R239: `(x) += v` / `(x)++` — a read-modify-write of a parenthesized simple lvalue must record
    # the STORE (not just a read), so a later use threads from the RMW statement, not from ENTRY.
    for src in (
        "void f(int x, int v) { (x) += v; use(x); }",
        "void f(int x) { (x)++; use(x); }",
        "void f(int x) { ((x)) += 1; use(x); }",
    ):
        _l, e = structure_cpp.pdg_source(src)["f"]
        # node 1 = the RMW statement, node 2 = `use(x)` — the RMW's store must reach the use.
        assert (1, 2, "D") in e, f"parenthesized RMW store dropped: {src} -> {e}"


def _edges_under_seed(seed: str) -> str:
    prog = (
        "from stitchgraph.core import structure_cpp\n"
        f"g = structure_cpp.pdg_source({_MULTIREAD!r})['f']\n"
        "print([list(e) for e in g[1]])\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True,
                         check=True, env=env)
    return out.stdout.strip()


def test_pdg_edge_order_is_deterministic_across_hash_seeds():
    outs = {_edges_under_seed(s) for s in ("0", "1", "7", "12345")}
    assert len(outs) == 1, f"pdg edge order varies with PYTHONHASHSEED: {outs}"
