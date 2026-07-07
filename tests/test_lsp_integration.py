"""Real-binary LSP integration (research/24) — each test gates on its server
actually being installed, so CI without the binaries skips cleanly while a
developer machine (or the field validation environment) exercises the true
wire protocol, project-load timing, and cross-file resolution."""
from __future__ import annotations

import shutil
import textwrap

import pytest

import stitchgraph as sg

pytest.importorskip("tree_sitter_language_pack")


def _need(binary: str) -> None:
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} not installed")


def test_rust_analyzer_disambiguates(tmp_path):
    _need("rust-analyzer")
    root = tmp_path / "crate"
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text(textwrap.dedent("""\
        [package]
        name = "probe"
        version = "0.1.0"
        edition = "2021"
    """))
    # two `greet` defs -> the name-based pass widens; rust-analyzer knows the
    # `util::` path names exactly one of them
    (root / "src" / "util.rs").write_text(
        'pub fn greet(name: &str) -> String { format!("hello {}", name) }\n')
    (root / "src" / "other.rs").write_text(
        'pub fn greet(name: &str) -> String { format!("howdy {}", name) }\n')
    (root / "src" / "main.rs").write_text(textwrap.dedent("""\
        mod util;
        mod other;

        fn main() {
            let s = util::greet("world");
            println!("{}", s);
        }
    """))
    (root / "stitchgraph.toml").write_text("[lsp]\nenabled = true\n")
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(root))
    assert res.ok
    report = res.result.get("lsp", {})
    assert any(s.get("resolved", 0) >= 1 for s in report.values()), report
    lsp_edges = {(e.src, e.dst_id) for e in store.resolved_edges()
                 if e.source == "lsp"}
    assert ("src/main.rs::main", "src/util.rs::greet") in lsp_edges
    # monotone: the wrong-arm AMBIGUOUS edge survives
    arms = {e.dst_id for e in store.resolved_edges()
            if e.src == "src/main.rs::main" and e.dst_symbol
            and e.dst_symbol.endswith("greet")}
    assert "src/other.rs::greet" in arms
    store.close()


def test_typescript_language_server_disambiguates(tmp_path):
    _need("typescript-language-server")
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "tsconfig.json").write_text(
        '{ "compilerOptions": { "module": "commonjs", "strict": true } }')
    (root / "src" / "util.ts").write_text(
        'export function greet(name: string): string { return "hello " + name; }\n')
    (root / "src" / "other.ts").write_text(
        'export function greet(name: string): string { return "howdy " + name; }\n')
    (root / "src" / "main.ts").write_text(textwrap.dedent("""\
        import { greet } from "./util";

        export function run(): string {
            return greet("world");
        }
    """))
    store = sg.Store(str(tmp_path / "g.db"))
    res = sg.reindex(store, str(root), lsp=True)
    assert res.ok
    edges = {(e.dst_id, e.provenance.value, e.source)
             for e in store.resolved_edges()
             if e.src == "src/main.ts::run" and e.dst_symbol == "greet"}
    assert ("src/util.ts::greet", "extracted", "lsp") in edges, edges
    assert ("src/other.ts::greet", "ambiguous", "tree-sitter") in edges
    store.close()


def test_type_at_rust_hover(tmp_path):
    _need("rust-analyzer")
    from stitchgraph.core.operations import type_at
    root = tmp_path / "crate"
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n')
    (root / "src" / "main.rs").write_text(textwrap.dedent("""\
        fn double(x: u32) -> u32 { x * 2 }

        fn main() {
            let y = double(21);
            println!("{}", y);
        }
    """))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    res = type_at(store, "src/main.rs", 4, 12)  # the `double` call
    assert res.ok, res.review_reasons
    assert "u32" in res.result["type"]
    store.close()
