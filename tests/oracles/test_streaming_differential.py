"""Streaming differential oracle — `reindex(streaming=True)` == `reindex(streaming=False)`.

v2 work toward a constant-memory indexer. Phase 1 lowers the extraction memory peak by
dropping each file's AST after pass 1 and re-parsing in pass 2 (instead of holding every AST
at once). That must produce a BYTE-IDENTICAL graph to the in-memory path — same nodes (ids +
roles), same edges, same find_stale / find_holes / fan_in. This oracle pins that equivalence
on corpora that fit in memory (so BOTH paths can run and be compared), exactly as the
incremental oracle pins `replace_file == full reindex`.

If a later phase streams nodes/edges to the store and runs roles/resolvers over it, this same
oracle is the gate: it only ships when streaming still equals full here.
"""
from __future__ import annotations

import textwrap

import pytest

import stitchgraph as sg
from stitchgraph.core.reach import fan_in


def _node_rows(store):
    return sorted(
        (r["id"], r["kind"], r["name"], r["roles"] or "")
        for r in store.conn.execute("SELECT id, kind, name, roles FROM nodes")
    )


def _edge_rows(store):
    # Compare EVERY load-bearing edge field — including weight, provenance, and the internal
    # `name_based` flag. The last is deliberate (panel R49): the store's `_dedup_resolved_edges`
    # ORs `name_based` across a (src,relation,dst_id) group's survivor, while the in-memory
    # `_dedup_edges` keeps first-seen — so on a *mixed* group the survivors' flags could differ.
    # The reason they don't diverge in `reindex` output is structural, not luck: a precise +
    # name-based pair to the same (src,relation,dst_id) only ever arises within ONE source's
    # edges (same enclosing def), and the per-source sink dedup collapses that group BEFORE it
    # reaches the store — so the store's OR never sees a mixed group (verified: 0 affected rows
    # on the self-source). Pinning the flag here means that if any future change ever lets a
    # mixed group survive to the store, this oracle catches the divergence instead of letting a
    # re-widening difference slip through silently.
    return sorted(
        (r["src"], r["relation"], r["dst_symbol"] or "", r["dst_id"] or "",
         round(r["weight"], 6), r["provenance"], r["name_based"])
        for r in store.conn.execute(
            "SELECT src, relation, dst_symbol, dst_id, weight, provenance, name_based "
            "FROM edges")
    )


def _snapshot(store):
    stale = {c["id"] for c in (sg.find_stale(store).result or [])}
    holes = sg.find_holes(store).meta.get("count")
    fi = {k: v for k, v in fan_in(store).items() if store.get_node(k) is not None}
    return _node_rows(store), _edge_rows(store), stale, holes, fi


def _assert_identical(root: str):
    with sg.Store(":memory:") as full, sg.Store(":memory:") as stream:
        sg.reindex(full, root)                    # in-memory (all ASTs resident)
        sg.reindex(stream, root, streaming=True)  # streaming (AST re-parsed in pass 2)
        fn, fe, fs, fh, ffi = _snapshot(full)
        sn, se, ss, sh, sfi = _snapshot(stream)
    assert fn == sn, "node rows diverge (id/kind/name/roles)"
    assert fe == se, "edge rows diverge (src/relation/dst_symbol/dst_id)"
    assert fs == ss, f"find_stale diverged: {fs ^ ss}"
    assert fh == sh, f"find_holes count diverged: full={fh} stream={sh}"
    assert ffi == sfi, "fan_in diverged"


def test_streaming_equals_full_on_self_source():
    """The real codebase IS the corpus — a large multi-file `src/` tree."""
    _assert_identical("src")


def test_streaming_equals_full_on_entrypoint_shapes(tmp_path):
    """A small fixture exercising the role-seeding passes (exported re-exports, a console
    script, a framework-callback base, inheritance) — the parts most sensitive to extraction
    order — must converge byte-for-byte between the two paths."""
    files = {
        "pyproject.toml": '[project]\nname="demo"\n[project.scripts]\ndemo = "pkg.cli:main"\n',
        "pkg/__init__.py": '__all__ = ["Widget"]\nfrom pkg.core import Widget\n',
        "pkg/core.py": (
            "class Base:\n    def hook(self):\n        return self._impl()\n"
            "    def _impl(self):\n        return 1\n\n"
            "class Widget(Base):\n    def public_api(self):\n        return 2\n\n"
            "def dead_one():\n    return 0\n"
        ),
        "pkg/cli.py": "from pkg import core\ndef main():\n    return core.Widget().public_api()\n",
    }
    for rel, content in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(textwrap.dedent(content))
    _assert_identical(str(tmp_path))


def test_streaming_equals_full_polyglot(tmp_path):
    """The streaming property must hold ACROSS LANGUAGES, not just Python — every extractor
    feeds the same shared graph, so the flag must never perturb the combined result. A mixed
    Python + JS/TS + Go + Ruby + C + PHP tree (tree-sitter for the non-Python files) must
    produce a byte-identical graph either way. This is the gate for tree-sitter streaming
    (Phase 4): in streaming mode each file's parse tree + source are freed after pass 1, with
    its defs' call/ref/scope info precomputed — the result must stay identical. PHP is here
    because Magento (the motivating monorepo) is PHP."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    files = {
        "app.py": "def run():\n    return helper()\ndef helper():\n    return 1\n",
        "ui.js": "export function widget() { return draw(); }\nfunction draw() { return 1; }\n",
        "svc.ts": "@Controller()\nclass Svc {\n  @Get()\n  all() { return this.q(); }\n  q() { return 1; }\n}\n",
        "main.go": "package main\nfunc main() {\n    hello()\n}\nfunc hello() {}\n",
        "lib.rb": "class Service\n  def call\n    work\n  end\n  def work; 1; end\nend\n",
        "core.c": "int add(int a,int b){return a+b;}\nint main(void){return add(1,2);}\n",
        "Svc.php": (
            "<?php\nclass Service {\n  public function handle() { return $this->work(); }\n"
            "  private function work() { return 1; }\n}\n"
            "function bootstrap() { return (new Service())->handle(); }\nbootstrap();\n"
        ),
        # Rust trait impl — exercises _seed_trait_impl_methods (the parent-chain walk that
        # streaming precomputes into _DefInfo.is_trait_impl) AND _iface_ids (trait_item).
        "lib.rs": (
            "pub trait Greeter {\n    fn greet(&self) -> i32;\n}\n"
            "pub struct Hi;\n"
            "impl Greeter for Hi {\n    fn greet(&self) -> i32 { 1 }\n}\n"
        ),
        # C++ out-of-line member definition — exercises _cpp_method_scope (precomputed into
        # _DefInfo.cpp_scope/cpp_line in streaming mode) and the F->M method promotion.
        "widget.h": "class Widget {\npublic:\n    int value();\n};\n",
        "widget.cpp": "#include \"widget.h\"\nint Widget::value() { return 1; }\n",
        # TS interface — exercises _iface_ids (interface_declaration) for the streaming
        # _DefInfo.type path.
        "port.ts": "export interface Port {\n  open(): number;\n}\n",
    }
    for rel, content in files.items():
        (tmp_path / rel).write_text(content)
    _assert_identical(str(tmp_path))


def test_streaming_equals_full_heavy_fanout_and_cross_group(tmp_path):
    """Stress the Phase 2b per-source dedup sink: many same-named methods (so a bare call
    fans out to N AMBIGUOUS candidates — the Magento blow-up in miniature), repeated call
    sites (exact-duplicate edges to collapse), and classes whose INHERITS / constructor
    seed edges are emitted in DIFFERENT extractor loops than their method-call edges (so the
    same `src` appears in non-adjacent groups — only the final global store dedup can
    reconcile those). The streamed graph must still equal the in-memory one byte-for-byte."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    # 6 classes each with a `handle()` method → a bare `handle()` call is 6-way ambiguous.
    php = ["<?php"]
    for i in range(6):
        php.append(
            f"class Svc{i} extends Base {{\n"
            f"  public function handle() {{ return $this->run(); }}\n"
            f"  public function run() {{ return helper(); }}\n}}\n")
    php.append("class Base { public function boot() { return 1; } }\n")
    # A driver that calls the ambiguous name repeatedly (duplicate + fan-out edges).
    php.append("function helper() { return 1; }\n")
    php.append("function drive($s) { $s->handle(); $s->handle(); return helper(); }\n")
    php.append("drive(new Svc0());\n")
    (tmp_path / "svc.php").write_text("\n".join(php))
    _assert_identical(str(tmp_path))


def test_streaming_equals_full_on_homonym_override_fanout(tmp_path):
    """The Home Assistant shape (field report 2026-07-03): a pure-Python tree whose bare-name
    homonyms (`get`/`setup`/`run` defined in every module) fan out AMBIGUOUS edges per call
    site, with inheritance + overrides + framework bases + dunders layered on. This is the
    path the fix moved onto the per-file sink drain + the store-twin override widening
    (`Store._propagate_overrides`), so pin: streamed rows == in-memory rows, including the
    override AMBIGUOUS edges, callback/test roles, and dunder seeds."""
    for i in range(12):
        d = tmp_path / f"pkg{i % 3}"
        d.mkdir(exist_ok=True)
        (d / "__init__.py").write_text("")
        (d / f"m{i}.py").write_text(
            f"class Base{i}:\n"
            f"    def get(self): return {i}\n"
            f"    def __getitem__(self, k): return self._pick(k)\n"
            f"    def _pick(self, k): return k\n"
            f"class Sub{i}(Base{i}):\n"
            f"    def get(self): return {i} + 1\n"
            f"class Handler{i}(SomeFrameworkBase):\n"
            f"    def handle(self): return get()\n"
            f"def work{i}(x: Base{i}):\n"
            f"    x.get()\n"
            f"    return get(), setup(), run()\n"
            f"def get(): return 1\n"
            f"def setup(): return 2\n"
            f"def run(): return 3\n"
            f"if __name__ == '__main__':\n"
            f"    work{i}(Sub{i}())\n")
    _assert_identical(str(tmp_path))
