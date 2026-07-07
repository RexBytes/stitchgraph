"""The README's supported-language-version table, enforced.

Every (oldest, newest) bound the README claims is pinned here as a parse probe
against the bundled grammar pack: the newest marquee syntax and the oldest
baseline of each language must parse with no ERROR node. A grammar-pack upgrade
that drops either end fails THIS file before it ships a wrong README claim.

Known, documented exception: Rust 2015's `try!` macro (`try` is reserved in the
modern grammar) — the README footnotes it, and the inverse is asserted below so
the footnote disappears the day the grammar accepts it again.
"""
from __future__ import annotations

import pytest

ts = pytest.importorskip("tree_sitter_language_pack")

# (language, README bound, marquee syntax)
PROBES = [
    # -- newest bounds -------------------------------------------------------
    ("python", "3.12/3.13", "type Vec[T] = list[T]\nclass C[T]:\n"
     "    def m[U](self, x: U) -> U: return x"),
    ("python", "3.12 nested f-strings", 'x = f"{f"{1+1}"}"'),
    ("javascript", "ES2022", "class A { static { init(); } #x = 1;\n"
     "  get x() { return this.#x ?? obj?.y; } }"),
    ("javascript", "ES2024", "const re = /[\\p{L}]/v;"),
    ("typescript", "TS 4.9 satisfies", "const c = { a: 1 } satisfies Record<string, number>;"),
    ("typescript", "TS 5.0", "@dec class B {}\n"
     "function f<const T extends readonly unknown[]>(x: T) { return x; }"),
    ("typescript", "TS 5.4 using", "function g() { using h = open(); }"),
    ("rust", "2024 edition async closures", "fn f() { let c = async || { 1 }; }"),
    ("rust", "1.79 inline const", "fn f() -> u32 { const { 1 + 1 } }"),
    ("rust", "1.65 let-else", "fn f(o: Option<u32>) -> u32 { "
     "let Some(x) = o else { return 0 }; x }"),
    ("c", "C11", "_Static_assert(1, \"x\");\n"
     "#define t(x) _Generic((x), int: 1, default: 0)\n"
     "int main(void){return t(1);}"),
    ("c", "C23", "[[nodiscard]] bool f(void) { return true; }"),
    ("cpp", "C++17", "auto [a, b] = std::pair{1, 2};"),
    ("cpp", "C++20 concepts", "template<class T> concept C = requires(T t) { t.f(); };"),
    ("cpp", "C++20 coroutines", "task f() { co_return 1; }"),
    ("csharp", "C# 11 raw strings", 'var s = """hello"""; '),
    ("csharp", "C# 12", "class P(int x) { int[] a = [1, 2, x]; }"),
    ("go", "1.18 generics", "package m\nfunc Map[T, U any](s []T, f func(T) U) []U { return nil }"),
    ("go", "1.22 range-over-int", "package m\nfunc f() { for i := range 10 { _ = i } }"),
    ("java", "16 records", "record Point(int x, int y) {}"),
    ("java", "17 sealed", "sealed interface Shape permits Circle {}"),
    ("java", "21 switch patterns", "class A { int f(Object o) { return switch (o) "
     "{ case Integer i -> i; default -> 0; }; } }"),
    ("ruby", "3.0 pattern match + endless def",
     "def sq(x) = x * x\ncase v\nin {a: Integer => n} then n\nend"),
    ("ruby", "3.2 anonymous forwarding", "def f(*, **, &) = g(*, **, &)"),
    ("ruby", "3.4 it param", "r = [1,2].map { it * 2 }"),
    ("php", "8.1", "<?php enum S { case A; } class C { public readonly int $x; } "
     "$r = match(true) { default => 1 };"),
    ("php", "8.4 property hooks", "<?php class D { public int $v { get => 1; } }"),
    ("bash", "4 assoc arrays + coproc", "declare -A m; m[k]=v; coproc { cat; }"),
    ("bash", "5 nameref", "declare -n ref=var"),
    # -- oldest bounds -------------------------------------------------------
    ("python", "3.8 baseline", "def f(a, /, b): pass\nif (n := 1): pass"),
    ("javascript", "ES5", "var x = 1; function f(a) { return a; } "
     "f.prototype.g = function() {};"),
    ("typescript", "TS 2.0", "namespace N { export interface I { x: number; } } "
     "enum E { A } class C implements N.I { x = 1; }"),
    ("rust", "2015 extern crate", "extern crate foo;\nfn f() {}"),
    ("c", "C89", "int f(x)\nint x;\n{ return x; }\nint main(void) { return f(1); }"),
    ("cpp", "C++98", "template<class T> T f(T t) throw() { return t; }"),
    ("csharp", "C# 2", "class C<T> { delegate void D(T t); event D E; }"),
    ("go", "1.0", "package main\nfunc main() { ch := make(chan int, 1); "
     "ch <- 1; println(<-ch) }"),
    ("java", "7", "class A { void f() { try (AutoCloseable c = null) {} "
     "catch (Exception e) {} } }"),
    ("ruby", "1.9", "h = { a: 1 }\n[1,2].each { |x| puts x }\n"
     "def f(*a, &b); b.call(a); end"),
    ("php", "5", "<?php class C { function C() {} } "
     "foreach ($a as $k => $v) { echo $v; }"),
    ("bash", "3 / POSIX", "f() { local x=1; [ \"$x\" -eq 1 ] && echo y; }\n"
     "for i in 1 2; do f; done"),
]


@pytest.mark.parametrize(("lang", "bound", "src"),
                         PROBES, ids=[f"{p[0]}-{p[1]}" for p in PROBES])
def test_readme_version_bound_parses(lang, bound, src):
    tree = ts.get_parser(lang).parse(src.encode())
    assert not tree.root_node.has_error, \
        f"{lang} {bound}: README claims this parses — grammar pack regressed?"


def test_rust_try_macro_footnote_still_true():
    """The README footnote exists BECAUSE this errors; if a grammar upgrade
    starts accepting `try!`, this inverse assertion fails and the footnote
    (plus this test) should be removed."""
    src = "fn f() -> Result<u32, ()> { let x = try!(g()); Ok(x) }"
    tree = ts.get_parser("rust").parse(src.encode())
    assert tree.root_node.has_error, \
        "rust grammar now parses try! — drop the README footnote"
