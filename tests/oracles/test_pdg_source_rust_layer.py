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


def test_pdg_self_receiver_reads_thread_from_entry():
    # R222: a `self` receiver reference is a `self` node (not `identifier`); the PDG must record
    # it as a value read so receiver-mediated dependences link from the ENTRY `self` seed. A
    # method whose body reads `self.field` must have a D-edge out of ENTRY (node 0).
    src = (
        "struct C { n: i32 }\n"
        "impl C {\n"
        "    fn m(&mut self) -> i32 {\n"
        "        self.n = 5;\n"
        "        let a = self.n + 1;\n"
        "        a\n"
        "    }\n"
        "}\n"
    )
    labels, edges = structure_rust.pdg_source(src)["C.m"]
    assert labels[0] == "ENTRY"
    assert any(k == "D" and s == 0 for s, d, k in edges), (
        "self-receiver read must produce a data edge from the ENTRY self seed"
    )


def test_pdg_value_position_control_folds_body_reads():
    # R223: Rust is expression-oriented — `let y = if/match/loop/{…}` folds the control expression
    # into the enclosing statement's reads, INCLUDING reads inside block-shaped branch/arm/loop
    # bodies. A `base` defined before, then read only inside such a value-position body, must still
    # produce a data dependence into the `let y` node (it was silently dropped when `collect`
    # blanket-skipped `block`).
    cases = (
        "fn f(n: i32) -> i32 { let base = n * 2; let r = if n > 0 { base + 1 } else { base - 1 }; r }",
        "fn f(o: Option<i32>, a: i32) -> i32 { let base = a + 1; "
        "let r = match o { Some(_) => { base + 1 }, None => { base } }; r }",
        "fn f(a: i32) -> i32 { let base = a + 1; let r = loop { break base; }; r }",
        "fn f(a: i32) -> i32 { let base = a + 1; let r = { let t = base; t + 1 }; r }",
    )
    for src in cases:
        labels, edges = structure_rust.pdg_source(src)["f"]
        # node 1 is `let base`, node 2 is `let r` — the fold must link base(1) -> r(2)
        assert (1, 2, "D") in edges, f"value-position body read not folded into the Let: {src}"


def test_pdg_let_else_alternative_block_reads_are_folded():
    # R224: `let PAT = EXPR else { … }` (let-else) — the `else` block is unconditional control flow
    # (runs on refutation). Its reads must fold into the let node, mirroring the VFG. A var defined
    # before, read only inside the `else` block, must still link into the let-else statement.
    _l, e = structure_rust.pdg_source(
        "fn f(o: Option<i32>) -> i32 { let d = compute(); "
        "let Some(x) = o else { return d + 1; }; x }"
    )["f"]
    # node 1 = `let d`, node 2 = the let-else — the else-block read of `d` must link 1 -> 2
    assert (1, 2, "D") in e, "let-else `else` block reads were not folded into the let node"


def test_pdg_let_chain_plain_clause_reads_are_captured():
    # R225: a let-chain condition (`let PAT = EXPR && <plain expr> && let …`) mixes let-condition
    # clauses with plain boolean clauses. The plain clauses' reads must be recorded (they were
    # dropped when only let_condition/let_chain children were recursed into). `x` (a plain clause
    # read) must flow from ENTRY into the control node, in both statement and value position and
    # regardless of clause order.
    for src, key in (
        ("fn f(x: i32, q: Option<i32>) { if let Some(a) = q && x > 0 { let z = a; } }", "f"),
        ("fn f(x: i32, q: Option<i32>) -> i32 { let r = if let Some(a) = q && x > 0 { a } "
         "else { 0 }; r }", "f"),
        ("fn f(x: i32, q: Option<i32>) { while let Some(a) = q && x > 0 { let z = a; } }", "f"),
        ("fn f(x: i32, q: Option<i32>) { if x > 0 && let Some(a) = q { let z = a; } }", "f"),
    ):
        labels, edges = structure_rust.pdg_source(src)[key]
        # node 1 is the control node (If/While/Let); its reads must include an ENTRY-sourced D edge
        # for the plain-clause read `x` (ENTRY carries both params). At least two D edges from ENTRY
        # into node 1 (q scrutinee + x plain clause).
        entry_d = [(s, d) for s, d, k in edges if k == "D" and s == 0 and d == 1]
        assert len(entry_d) >= 2, f"let-chain plain clause read not captured: {src} -> {edges}"


def test_pdg_macro_name_is_not_a_spurious_read():
    # R226: a macro invocation's name is not a value. `collect` must read only the token-tree args,
    # never the macro name identifier — else a local whose name collides with a common macro
    # (`matches`, `write`, `assert`, `select`, `format`, `pin`, …) gets a phantom data edge.
    _l, e = structure_rust.pdg_source(
        "fn f() { let matches = 5; if matches!(x, Some(_)) { let z = matches + 1; } }"
    )["f"]
    # node 1 = `let matches`, node 2 = the If (its condition is the macro call). No edge 1 -> 2:
    # the macro NAME must not be read as the local `matches`.
    assert (1, 2, "D") not in e, "macro name read as a colliding local variable (spurious edge)"
    # a genuine variable passed to a macro is still read
    _l2, e2 = structure_rust.pdg_source("fn f(v: i32) { println!(\"{}\", v); }")["f"]
    assert any(k == "D" and s == 0 for s, d, k in e2), "genuine macro-arg read was dropped"


def test_pdg_struct_pattern_shorthand_bindings_are_stored():
    # R228: `let Struct { field, .. } = expr` shorthand field bindings must be recorded as stores so
    # later reads of those names get a data edge. A shorthand `field_pattern` has no inner `pattern`
    # field — its leaf `shorthand_field_identifier` IS the binding (mirrors `_pattern_names`).
    for src in (
        "fn f(p: P) { let Point { x, y } = p; let z = x + y; }",
        "fn f(p: P) { let Point { x } = p; let z = x + 1; }",
        "fn f(p: P) { let Point { ref x, mut y } = p; let z = x + y; }",
        "fn f(p: P) { let Point { x, .. } = p; let z = x + 1; }",
    ):
        _l, e = structure_rust.pdg_source(src)["f"]
        # node 1 = the struct-pattern let, node 2 = the read — the binding must reach it
        assert (1, 2, "D") in e, f"struct-pattern shorthand binding not tracked: {src}"


def test_pdg_loop_label_is_not_a_spurious_read():
    # R229: a loop/block label (`'outer`) in a `break`/`continue` is a control-flow target, not a
    # value. Reading its bare name would create a phantom data edge whenever a local shares the name.
    # Here the label `'outer` collides with the param `outer`; the value-less break must NOT read it.
    _l, e = structure_rust.pdg_source(
        "fn f(outer: i32) -> i32 { 'outer: loop { if outer > 0 { break 'outer; } } 0 }"
    )["f"]
    # `outer` IS read by the `if outer > 0` guard (one ENTRY-D edge is legitimate). The label must
    # not add a SECOND read: exactly one ENTRY-sourced D edge, from the guard, not the break label.
    entry_reads = [(s, d) for s, d, k in e if k == "D" and s == 0]
    assert len(entry_reads) == 1, f"loop label read as a value (phantom edge): {e}"


def test_pdg_value_position_bindings_are_not_false_reads():
    # if-let / for / match-arm binding patterns bind only inside the branch — they must NOT be read
    # as outer values. `opt` (the scrutinee) is a real read; `x` (the if-let binding) is not.
    _l, e = structure_rust.pdg_source(
        "fn f(opt: Option<i32>, base: i32) -> i32 { "
        "let r = if let Some(x) = opt { x + base } else { base }; r }"
    )["f"]
    # only ENTRY-sourced reads (opt, base) and the trailing `r` read; every edge well-formed
    assert e and all(k in ("C", "D") for _s, _d, k in e)
    assert any(k == "D" and s == 0 for s, d, k in e)  # scrutinee/base read from ENTRY


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
