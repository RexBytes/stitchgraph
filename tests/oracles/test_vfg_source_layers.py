"""Cross-language oracle for the EXPRESSION layer's public surface: `vfg_source` (design §5c).

`vfg_source` exposes the per-function value-flow graph that `fingerprint_source` digests — the raw
graph `get_matrix(layer="expression")` drills into. This oracle pins, across ALL 12 languages, that:

  1. `vfg_source` keys EXACTLY match `fingerprint_source` keys (same functions, same qualnames) — the
     two share one traversal, so a drift here means a frontend diverged.
  2. every graph is well-formed — a non-empty node-label list and an edge list of
     `(int src, int dst, kind)` triples whose kind is 'd' (data) or 'c' (control), with indices in
     range of the node list.
  3. it is the REAL value-flow graph, not a husk — a metamorphic probe: a `helper()` CALL vs a `0`
     CONST in the body changes the node-label multiset (same property the body fingerprint relies on).

Advisory layer — computed on demand, never feeds liveness.
"""
from __future__ import annotations

import collections

import pytest

from stitchgraph.core import structure  # Python: stdlib ast, no extra

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import (  # noqa: E402
    structure_bash,
    structure_cpp,
    structure_csharp,
    structure_go,
    structure_java,
    structure_js,
    structure_php,
    structure_ruby,
    structure_rust,
)

# (module, kwargs, source-with-{probe}) — {probe} is a value-bearing position: helper() vs 0.
_CASES = {
    "python": (structure, {}, "def f(a):\n    x = {probe}\n    return x + a\n"),
    "javascript": (structure_js, {"lang": "javascript"}, "function f(a){ const x = {probe}; return x + a; }"),
    "typescript": (structure_js, {"lang": "typescript"}, "function f(a:number){ const x = {probe}; return x + a; }"),
    "go": (structure_go, {}, "func f(a int) int { x := {probe}; return x + a }"),
    "rust": (structure_rust, {}, "fn f(a:i32)->i32{ let x = {probe}; x + a }"),
    "cpp": (structure_cpp, {}, "int f(int a){ int x = {probe}; return x + a; }"),
    "java": (structure_java, {}, "class C { int f(int a){ int x = {probe}; return x + a; } }"),
    "csharp": (structure_csharp, {}, "class C { int F(int a){ int x = {probe}; return x + a; } }"),
    "ruby": (structure_ruby, {}, "def f(a)\n  x = {probe}\n  x + a\nend"),
    "php": (structure_php, {}, "<?php function f($a){ $x = {probe}; return $x + $a; }"),
    "bash": (structure_bash, {}, "f(){ local x=$({probe}); echo $((x + $1)); }"),
}


@pytest.mark.parametrize("lang", sorted(_CASES))
def test_vfg_source_keys_match_fingerprint_source(lang):
    mod, kw, tmpl = _CASES[lang]
    src = tmpl.replace("{probe}", "helper(a)" if lang != "bash" else 'helper "$1"')
    fps = mod.fingerprint_source(src, **kw)
    vfgs = mod.vfg_source(src, **kw)
    assert fps, f"{lang}: fingerprint_source captured no function"
    assert set(fps) == set(vfgs), (
        f"{lang}: vfg_source keys {sorted(vfgs)} != fingerprint_source keys {sorted(fps)} — the two "
        f"traversals have diverged")


@pytest.mark.parametrize("lang", sorted(_CASES))
def test_vfg_source_graphs_are_well_formed(lang):
    mod, kw, tmpl = _CASES[lang]
    src = tmpl.replace("{probe}", "helper(a)" if lang != "bash" else 'helper "$1"')
    for name, (labels, edges) in mod.vfg_source(src, **kw).items():
        assert labels and all(isinstance(x, str) for x in labels), f"{lang}/{name}: bad node labels"
        assert isinstance(edges, list)
        for e in edges:
            assert isinstance(e, tuple) and len(e) == 3, f"{lang}/{name}: edge not a 3-tuple: {e!r}"
            s, d, k = e
            assert isinstance(s, int) and isinstance(d, int), f"{lang}/{name}: non-int edge endpoint"
            assert 0 <= s < len(labels) and 0 <= d < len(labels), f"{lang}/{name}: edge index OOR"
            assert k in ("d", "c"), f"{lang}/{name}: edge kind {k!r} not data/control"


@pytest.mark.parametrize("lang", sorted(_CASES))
def test_vfg_source_reflects_body_value_flow(lang):
    # Metamorphic: a CALL vs a CONST in the body must change the node-label multiset — proves the
    # exposed graph is the real value-flow graph, not an empty/constant husk.
    mod, kw, tmpl = _CASES[lang]
    call = tmpl.replace("{probe}", "helper(a)" if lang != "bash" else 'helper "$1"')
    const = tmpl.replace("{probe}", "0")

    def labels(src):
        vf = mod.vfg_source(src, **kw)
        return collections.Counter(next(iter(vf.values()))[0]) if vf else collections.Counter()

    assert labels(call) != labels(const), (
        f"{lang}: a CALL vs a CONST body produced the same value-flow node multiset — vfg_source is "
        f"not reflecting body value flow")
