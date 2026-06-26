"""Incremental differential oracle — the real codebase IS the corpus.

The richest defect vein (panels R22/R24/R29/R31) is the incremental `replace_file`
pipeline drifting from a full `reindex`. The oracle: full reindex is ground truth;
the incremental path applied to the SAME final state must agree on `find_stale`,
`fan_in`, and `find_holes`. We don't *generate* a project (that generator would be
its own long tail) — `src/` is already a large, valid, multi-file one, and we apply
cheap MECHANICAL edits to it.

`find_stale` divergence in the live->dead direction is the cardinal; `fan_in`
incremental > full is inflation. Both are release-blocking.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

import stitchgraph as sg
from stitchgraph.core.extract import extract_project as extract_all  # combined polyglot
from stitchgraph.core.extract.python import extract_project
from stitchgraph.core.reach import fan_in

_SRC = "src"


def _by_file(items, key):
    out: dict[str, list] = defaultdict(list)
    for x in items:
        out[key(x).split("::", 1)[0]].append(x)
    return out


def _state(store):
    stale = {c["id"] for c in (sg.find_stale(store).result or [])}
    fi = {k: v for k, v in fan_in(store).items() if store.get_node(k) is not None}
    holes = sg.find_holes(store).meta.get("count")
    return stale, fi, holes


def _full():
    s = sg.Store(":memory:")
    sg.reindex(s, _SRC)
    return s


def _incremental(nbf, ebf, files):
    s = sg.Store(":memory:")
    # Pass the whole-project exported-id set so cross-file `exported` roles converge with a
    # full reindex even when a package __init__'s re-export surface changes (panel R37A).
    exported_ids = {n.id for fnodes in nbf.values() for n in fnodes if "exported" in n.roles}
    for f in files:
        s.replace_file(f, nbf.get(f, []), ebf.get(f, []), exported_ids=exported_ids)
    return s


def _nodes_edges():
    nodes, edges = extract_project(_SRC)
    return _by_file(nodes, lambda n: n.id), _by_file(edges, lambda e: e.src)


def test_incremental_build_equals_full_reindex():
    """Applying every file via replace_file (sorted order) equals a full reindex on
    find_stale + fan_in + find_holes."""
    nbf, ebf = _nodes_edges()
    files = sorted(set(nbf) | set(ebf))
    with _full() as full, _incremental(nbf, ebf, files) as inc:
        f_stale, f_fi, f_holes = _state(full)
        i_stale, i_fi, i_holes = _state(inc)
    assert i_stale == f_stale, f"stale diverged: incremental-only={i_stale - f_stale}"
    inflated = {k: (i_fi[k], f_fi.get(k, 0)) for k in i_fi if i_fi[k] > f_fi.get(k, 0)}
    assert not inflated, f"fan_in inflated (incremental > full): {inflated}"
    assert i_holes == f_holes


@pytest.mark.parametrize("target", [
    "stitchgraph/core/reach.py",
    "stitchgraph/core/extract/python.py",
    "stitchgraph/core/store.py",
    "stitchgraph/core/operations.py",
    "stitchgraph/core/algebra.py",
])
def test_delete_then_readd_converges(target):
    """Mechanical edit: build everything, then delete one file and re-add it; the result
    must still equal a full reindex (no orphaned/widened/module-retargeted edges)."""
    nbf, ebf = _nodes_edges()
    files = sorted(set(nbf) | set(ebf))
    if target not in files:
        pytest.skip(f"{target} not in extracted set")
    with _full() as full:
        f_stale, f_fi, _ = _state(full)
    with _incremental(nbf, ebf, files) as inc:
        inc.replace_file(target, [], [])               # delete
        inc.replace_file(target, nbf.get(target, []), ebf.get(target, []))  # re-add
        i_stale, i_fi, _ = _state(inc)
    assert i_stale == f_stale, f"{target}: stale diverged on delete->readd"
    inflated = {k for k in i_fi if i_fi[k] > f_fi.get(k, 0)}
    assert not inflated, f"{target}: fan_in inflated on delete->readd: {inflated}"


def test_polyglot_incremental_equals_full(tmp_path):
    """Cross-language differential: the incremental replace_file path must agree with a full
    reindex on a POLYGLOT tree. The Python-only oracle above never drove the tree-sitter
    extractor through replace_file, so the store's language-blind name resolution (a Rust
    `helper()` binding to a Go same-named def on the incremental path) was uncovered —
    inflating fan_in vs full reindex (panel R34A)."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_language_pack")
    files = {
        "lib.rs": "pub fn run() {\n    helper();\n}\nfn helper() {}\n",
        "other.go": "package main\nfunc helper() {}\nfunc main() {\n    helper()\n}\n",
        "app.py": "def helper():\n    return 1\ndef run():\n    return helper()\n",
    }
    for rel, content in files.items():
        (tmp_path / rel).write_text(content)
    root = str(tmp_path)
    nodes, edges = extract_all(root)
    nbf, ebf = _by_file(nodes, lambda n: n.id), _by_file(edges, lambda e: e.src)
    order = sorted(set(nbf) | set(ebf))
    with sg.Store(":memory:") as full, sg.Store(":memory:") as inc:
        sg.reindex(full, root)
        for f in order:
            inc.replace_file(f, nbf.get(f, []), ebf.get(f, []))
        f_fi = {k: v for k, v in fan_in(full).items() if full.get_node(k) is not None}
        i_fi = {k: v for k, v in fan_in(inc).items() if inc.get_node(k) is not None}
        f_stale = {c["id"] for c in (sg.find_stale(full).result or [])}
        i_stale = {c["id"] for c in (sg.find_stale(inc).result or [])}
    inflated = {k: (i_fi[k], f_fi.get(k, 0)) for k in i_fi if i_fi[k] > f_fi.get(k, 0)}
    assert not inflated, f"polyglot fan_in inflated (incremental > full): {inflated}"
    assert i_stale == f_stale, f"polyglot stale diverged: {i_stale ^ f_stale}"


def test_src_layout_incremental_equals_full(tmp_path):
    """src-layout differential: a PyPA `src/` project gains project-level state — the
    `source_prefix` and the src-stripped `module_by_qual` aliases that let absolute imports
    (`from app import util`) resolve. The standing oracle corpus (`src/` indexed AT src) never
    triggers src-layout, so this pins that the incremental replace_file path converges with a
    full reindex when that state is in play — else live module-load-only code (reached only via
    an absolute-import side effect) could diverge live->dead on one path (the cardinal)."""
    files = {
        "src/app/__init__.py": '__all__ = ["main"]\nfrom app.cli import main\n',
        "src/app/cli.py": "from app import util\ndef main():\n    return util.helper()\n",
        "src/app/util.py": (
            "def helper():\n    return _internal()\n"
            "def _internal():\n    return 1\n"
            "def truly_dead():\n    return 99\n"
        ),
    }
    for rel, content in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(content)
    root = str(tmp_path)
    nodes, edges = extract_project(root)
    nbf, ebf = _by_file(nodes, lambda n: n.id), _by_file(edges, lambda e: e.src)
    order = sorted(set(nbf) | set(ebf))
    exported_ids = {n.id for fn in nbf.values() for n in fn if "exported" in n.roles}
    with sg.Store(":memory:") as full, sg.Store(":memory:") as inc:
        sg.reindex(full, root)
        for f in order:
            inc.replace_file(f, nbf.get(f, []), ebf.get(f, []), exported_ids=exported_ids)
        f_stale = {c["id"] for c in (sg.find_stale(full).result or [])}
        i_stale = {c["id"] for c in (sg.find_stale(inc).result or [])}
        f_fi = {k: v for k, v in fan_in(full).items() if full.get_node(k) is not None}
        i_fi = {k: v for k, v in fan_in(inc).items() if inc.get_node(k) is not None}
    assert i_stale == f_stale, f"src-layout stale diverged: {i_stale ^ f_stale}"
    inflated = {k for k in i_fi if i_fi[k] > f_fi.get(k, 0)}
    assert not inflated, f"src-layout fan_in inflated (incremental > full): {inflated}"
    # the genuinely-dead helper must still be flagged on BOTH paths (not masked by the fix)
    assert any(s.endswith("util.py::truly_dead") for s in f_stale)
