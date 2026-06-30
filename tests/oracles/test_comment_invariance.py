"""Cross-language completeness oracle: comments are trivia (v3.7.0).

A comment is non-semantic — adding or removing one is a no-op refactor, so it MUST NOT change a
function's body fingerprint (else `find_similar`/`graph_diff` mis-rank a commented clone or flag a
comment-only edit as a body change). This battery pins that invariant across EVERY body-matrix
frontend, and — as a guard against over-pruning — also checks that a real CALL-vs-CONST change in the
same body still DIFFERS (so the comment-skip didn't accidentally swallow live value flow).

The leak was a shared-design defect: a `comment` tree-sitter node fell through each frontend's generic
fallback into the value-flow graph. It was latent in Go/Rust/C++/Java/C# (shipped v3.3.0–v3.6.0) and
present in the new Ruby/PHP/Bash frontends; Python is immune (its `ast` discards comments) and JS/TS
were already immune. Surfaced by the v3.7.0 adversarial panel; see `docs/BODY_MATRIX_LESSONS.md`.
"""
from __future__ import annotations

import pytest

from stitchgraph.core import structure  # Python: stdlib ast, no extra needed

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


def _one(fps):
    return next(iter(fps.values())) if fps else None


# (module, kwargs, plain, with-comments, call-variant, const-variant)
# `plain` vs `with` differ ONLY by added comments (in several positions) -> must be IDENTICAL.
# `call` vs `const` differ by a real helper() CALL vs 0 CONST -> must DIFFER (no over-pruning).
_CASES = {
    "python": (structure, {},
               "def f(x):\n    a()\n    return g(x)\n",
               "def f(x):\n    # c\n    a()  # t\n    return g(x)\n",
               "def f(x):\n    return g(x)\n",
               "def f(x):\n    return 0\n"),
    "javascript": (structure_js, {"lang": "javascript"},
                   "function f(x){ a(); return g(x); }",
                   "function f(x){ /*c*/ a(); // t\n return g(x); }",
                   "function f(x){ return g(x); }",
                   "function f(x){ return 0; }"),
    "typescript": (structure_js, {"lang": "typescript"},
                   "function f(x:number){ a(); return g(x); }",
                   "function f(x:number){ /*c*/ a(); // t\n return g(x); }",
                   "function f(x:number){ return g(x); }",
                   "function f(x:number){ return 0; }"),
    "go": (structure_go, {},
           "func f(x int) int { a(); return g(x) }",
           "func f(x int) int { // c\n a() // t\n return g(x) }",
           "func f(x int) int { return g(x) }",
           "func f(x int) int { return 0 }"),
    "rust": (structure_rust, {},
             "fn f(x:i32)->i32 { a(); g(x) }",
             "fn f(x:i32)->i32 { // c\n a(); /*b*/ g(x) }",
             "fn f(x:i32)->i32 { g(x) }",
             "fn f(x:i32)->i32 { 0 }"),
    "cpp": (structure_cpp, {},
            "int f(int x){ a(); return g(x); }",
            "int f(int x){ /*c*/ a(); // t\n return g(x); }",
            "int f(int x){ return g(x); }",
            "int f(int x){ return 0; }"),
    "java": (structure_java, {},
             "class C { int f(int x){ a(); return g(x); } }",
             "class C { int f(int x){ /*c*/ a(); // t\n return g(x); } }",
             "class C { int f(int x){ return g(x); } }",
             "class C { int f(int x){ return 0; } }"),
    "csharp": (structure_csharp, {},
               "class C { int F(int x){ a(); return g(x); } }",
               "class C { int F(int x){ // c\n a(); /*t*/ return g(x); } }",
               "class C { int F(int x){ return g(x); } }",
               "class C { int F(int x){ return 0; } }"),
    "ruby": (structure_ruby, {},
             "def f(x)\n a()\n g(x)\nend",
             "def f(x)\n # c\n a() # t\n g(x)\nend",
             "def f(x)\n g(x)\nend",
             "def f(x)\n 0\nend"),
    "php": (structure_php, {},
            "<?php function f($x){ a(); return g($x); }",
            "<?php function f($x){ /*c*/ a(); // t\n return g($x); }",
            "<?php function f($x){ return g($x); }",
            "<?php function f($x){ return 0; }"),
    "bash": (structure_bash, {},
             'f(){ a; g "$x"; }',
             'f(){ # c\n a # t\n g "$x"; }',
             'f(){ g "$x"; }',
             'f(){ g 0; }'),
}


@pytest.mark.parametrize("lang", sorted(_CASES))
def test_comments_do_not_change_the_fingerprint(lang):
    mod, kw, plain, commented, _call, _const = _CASES[lang]
    a = _one(mod.fingerprint_source(plain, **kw))
    b = _one(mod.fingerprint_source(commented, **kw))
    assert a is not None and b is not None, f"{lang}: a function was not captured"
    assert a == b, (
        f"{lang}: adding comments changed the body fingerprint — a comment node is leaking into the "
        f"value-flow graph (it must be skipped as trivia)")


@pytest.mark.parametrize("lang", sorted(_CASES))
def test_comment_skip_does_not_over_prune(lang):
    # Guard against the fix swallowing real flow: a CALL vs a CONST in the body must still DIFFER.
    mod, kw, _plain, _commented, call, const = _CASES[lang]
    a = _one(mod.fingerprint_source(call, **kw))
    b = _one(mod.fingerprint_source(const, **kw))
    assert a is not None and b is not None
    assert a != b, (
        f"{lang}: a CALL vs a CONST body produced identical fingerprints — the comment-skip is "
        f"over-pruning real value flow")
