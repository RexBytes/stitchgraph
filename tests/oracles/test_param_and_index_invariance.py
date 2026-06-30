"""Cross-language completeness oracle: assignment-target index + default-parameter value (v3.7.0).

Two value-bearing positions that earlier escaped the per-language oracles and were surfaced by the
v3.7.0 certification panel (round 2):

* **Assignment-target subscript index** — `d[helper()] = v` must NOT fingerprint identically to
  `d[0] = v`. The RHS read `d[helper()]` was always walked, but the `bind()` (write) path dropped the
  index expression in Python, JS/TS, Go, Rust and C/C++ (Java/C#/PHP/Ruby already walked it).
* **Default parameter value** — `f(a = helper())` must NOT fingerprint identically to `f(a = 0)`. The
  default is evaluated value flow. C++/C#/PHP/Ruby walked it (the v3.7.0 "D1" fix); Python and JS did
  not until round 2. (Go/Rust/Java have no default-argument syntax — not exercised here.)

Both are advisory-layer completeness violations (the body matrix never feeds `find_stale`), not
cardinal. This battery pins the invariant across every applicable frontend and — as a guard against
over-pruning — checks that the value still matters (a CALL differs from a CONST). See
`docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import pytest

from stitchgraph.core import structure  # Python: stdlib ast, no extra

pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import (  # noqa: E402
    structure_cpp,
    structure_csharp,
    structure_go,
    structure_java,
    structure_js,
    structure_php,
    structure_ruby,
    structure_rust,
)


def _one(fps):
    return next(iter(fps.values())) if fps else None


def _differs(mod, call_src, const_src, **kw):
    a = _one(mod.fingerprint_source(call_src, **kw))
    b = _one(mod.fingerprint_source(const_src, **kw))
    assert a is not None and b is not None, "a function was not captured"
    return a != b


# {probe} -> helper() (a CALL) vs 0 (a CONST), in an assignment-TARGET subscript index.
_INDEX = {
    "python": (structure, {}, "def f(d, v):\n    d[{probe}] = v\n    return d\n"),
    "javascript": (structure_js, {"lang": "javascript"}, "function f(d, v){ d[{probe}] = v; return d; }"),
    "typescript": (structure_js, {"lang": "typescript"}, "function f(d, v){ d[{probe}] = v; return d; }"),
    "go": (structure_go, {}, "func f(d map[int]int, v int) { d[{probe}] = v }"),
    "rust": (structure_rust, {}, "fn f(d: T, v: i32) { d[{probe}] = v; }"),
    "cpp": (structure_cpp, {}, "void f(int* d, int v){ d[{probe}] = v; }"),
    "java": (structure_java, {}, "class C { void f(int[] d, int v){ d[{probe}] = v; } }"),
    "csharp": (structure_csharp, {}, "class C { void F(int[] d, int v){ d[{probe}] = v; } }"),
    "php": (structure_php, {}, "<?php function f($d, $v){ $d[{probe}] = $v; }"),
    "ruby": (structure_ruby, {}, "def f(d, v)\n d[{probe}] = v\nend"),
}


@pytest.mark.parametrize("lang", sorted(_INDEX))
def test_assignment_target_index_is_walked(lang):
    mod, kw, tmpl = _INDEX[lang]
    assert _differs(mod, tmpl.replace("{probe}", "helper()"), tmpl.replace("{probe}", "0"), **kw), (
        f"{lang}: a CALL vs a CONST in an assignment-target subscript index produced identical "
        f"fingerprints — the write path is dropping the index expression")


# {probe} in a parameter default value. Only languages that HAVE default-argument syntax.
_DEFAULT = {
    "python": (structure, {}, "def f(a={probe}):\n    return a\n"),
    "javascript": (structure_js, {"lang": "javascript"}, "function f(a = {probe}){ return a; }"),
    "typescript": (structure_js, {"lang": "typescript"}, "function f(a = {probe}){ return a; }"),
    "cpp": (structure_cpp, {}, "int f(int a = {probe}){ return a; }"),
    "csharp": (structure_csharp, {}, "class C { int F(int a = {probe}){ return a; } }"),
    "php": (structure_php, {}, "<?php function f($a = {probe}){ return $a; }"),
    "ruby": (structure_ruby, {}, "def f(a = {probe})\n a\nend"),
}


@pytest.mark.parametrize("lang", sorted(_DEFAULT))
def test_default_parameter_value_is_walked(lang):
    mod, kw, tmpl = _DEFAULT[lang]
    assert _differs(mod, tmpl.replace("{probe}", "helper()"), tmpl.replace("{probe}", "0"), **kw), (
        f"{lang}: a CALL vs a CONST in a parameter default value produced identical fingerprints "
        f"— the default expression is being dropped")


# JS/TS destructuring defaults in a *declaration / assignment target* (not a parameter) — these route
# through `bind()`, a different path than parameter defaults. `{x = helper()} = a` / `[x = helper()] = a`.
_JS_DESTRUCTURE = {
    "js-object": ("javascript", "function f(a){ const {x = {probe}} = a; return x; }"),
    "js-array": ("javascript", "function f(a){ const [x = {probe}] = a; return x; }"),
    "js-pair": ("javascript", "function f(a){ const {k: x = {probe}} = a; return x; }"),
    "ts-object": ("typescript", "function f(a){ const {x = {probe}} = a; return x; }"),
}


@pytest.mark.parametrize("label", sorted(_JS_DESTRUCTURE))
def test_js_destructuring_default_is_walked(label):
    lang, tmpl = _JS_DESTRUCTURE[label]
    assert _differs(structure_js, tmpl.replace("{probe}", "helper()"),
                    tmpl.replace("{probe}", "0"), lang=lang), (
        f"{label}: a CALL vs a CONST in a destructuring default (declaration target) produced "
        f"identical fingerprints — the default expression is being dropped by bind()")


# A COMPUTED method/getter/setter key in an object literal is evaluated in the enclosing scope (the
# method body stays opaque NESTED), so a CALL there must change the fingerprint. (Data-property
# computed keys were always walked; the method form was the gap.)
_JS_COMPUTED_KEY = {
    "js-method": ("javascript", "function f(){ return { [{probe}]() { return 1; } }; }"),
    "js-getter": ("javascript", "function f(){ return { get [{probe}]() { return 1; } }; }"),
    "ts-method": ("typescript", "function f(){ return { [{probe}]() { return 1; } }; }"),
}


@pytest.mark.parametrize("label", sorted(_JS_COMPUTED_KEY))
def test_js_computed_method_key_is_walked(label):
    lang, tmpl = _JS_COMPUTED_KEY[label]
    assert _differs(structure_js, tmpl.replace("{probe}", "helper()"),
                    tmpl.replace("{probe}", "0"), lang=lang), (
        f"{label}: a CALL vs a CONST in a computed method key produced identical fingerprints — the "
        f"computed key (evaluated in the enclosing scope) is being dropped")
