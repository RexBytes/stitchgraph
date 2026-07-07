"""Turnkey coverage kits (research/26): the index-derived spans.json, the
universal converter's three formats, and — where the toolchain exists — a real
end-to-end Go capture. Field validation beyond this file: the Rust kit ran
against fd (267 tests -> find_modes), jest and vitest fixtures both produced
exact per-test-file attribution (2026-07-07)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import stitchgraph as sg
from stitchgraph.core import coverage_scaffold
from stitchgraph.core.operations import scaffold_coverage

pytest.importorskip("tree_sitter_language_pack")


def _write_kit_converter(tmp_path: Path) -> Path:
    conv = tmp_path / "to_canonical.py"
    conv.write_text(coverage_scaffold._UNI_CONVERTER)
    return conv


def _run_converter(tmp_path: Path, fmt: str) -> dict:
    conv = _write_kit_converter(tmp_path)
    out = tmp_path / "coverage_modes.json"
    proc = subprocess.run(
        [sys.executable, str(conv), fmt, "covdata", "spans.json",
         str(out), "tests.txt"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text())


SPANS = {"src/lib.rs": [["src/lib.rs::outer", 1, 20],
                        ["src/lib.rs::outer.inner", 5, 8],
                        ["src/lib.rs::other", 30, 40]]}


def test_converter_llvm_json(tmp_path):
    """llvm-cov export: executed functions marked via their entry line —
    including INNERMOST attribution for a nested fn."""
    (tmp_path / "spans.json").write_text(json.dumps(SPANS))
    (tmp_path / "covdata").mkdir()
    (tmp_path / "tests.txt").write_text("crate::tests::t_one\n")
    capture = {"data": [{"functions": [
        # executed: entry line 5 -> innermost span is outer.inner, not outer
        {"name": "_mangled1", "count": 3,
         "regions": [[5, 1, 8, 2, 3, 0, 0, 0]],
         "filenames": [str(tmp_path / "src/lib.rs")]},
        {"name": "_mangled2", "count": 0,           # NOT executed
         "regions": [[30, 1, 40, 2, 0, 0, 0, 0]],
         "filenames": [str(tmp_path / "src/lib.rs")]},
    ]}]}
    (tmp_path / "covdata" / "0.json").write_text(json.dumps(capture))
    got = _run_converter(tmp_path, "llvm-json")
    assert got == {"format": "stitchgraph-coverage-v1",
                   "tests": {"crate::tests::t_one": ["src/lib.rs::outer.inner"]}}


def test_converter_goprofile_strips_module_prefix(tmp_path):
    spans = {"calc/calc.go": [["calc/calc.go::Add", 3, 5],
                              ["calc/calc.go::Mul", 7, 13]]}
    (tmp_path / "spans.json").write_text(json.dumps(spans))
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.24\n")
    (tmp_path / "covdata").mkdir()
    (tmp_path / "tests.txt").write_text("example.com/demo/calc::TestAdd\n")
    (tmp_path / "covdata" / "0.out").write_text(textwrap.dedent("""\
        mode: set
        example.com/demo/calc/calc.go:3.20,5.2 1 1
        example.com/demo/calc/calc.go:7.20,13.2 4 0
    """))
    got = _run_converter(tmp_path, "goprofile")
    assert got["tests"] == {"example.com/demo/calc::TestAdd": ["calc/calc.go::Add"]}


def test_converter_istanbul(tmp_path):
    spans = {"src/calc.js": [["src/calc.js::add", 1, 3],
                             ["src/calc.js::mul", 5, 11]]}
    (tmp_path / "spans.json").write_text(json.dumps(spans))
    (tmp_path / "covdata").mkdir()
    (tmp_path / "tests.txt").write_text("tests/add.test.js\n")
    capture = {str(tmp_path / "src/calc.js"): {
        "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}},
                         "1": {"start": {"line": 6}, "end": {"line": 6}}},
        "s": {"0": 4, "1": 0}}}
    (tmp_path / "covdata" / "0.json").write_text(json.dumps(capture))
    got = _run_converter(tmp_path, "istanbul")
    assert got["tests"] == {"tests/add.test.js": ["src/calc.js::add"]}


def test_converter_zero_tests_fails_loud(tmp_path):
    """Self-audit 2026-07-07 (bug class 1, confident absence): an empty capture
    dir must NOT produce a 0-test artifact with exit 0 — that artifact reads as
    'this suite covers nothing' downstream. Fail loud, write nothing."""
    (tmp_path / "spans.json").write_text(json.dumps(SPANS))
    (tmp_path / "covdata").mkdir()
    (tmp_path / "tests.txt").write_text("t_one\n")
    conv = _write_kit_converter(tmp_path)
    out = tmp_path / "coverage_modes.json"
    proc = subprocess.run(
        [sys.executable, str(conv), "llvm-json", "covdata", "spans.json",
         str(out), "tests.txt"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "0 tests" in proc.stderr
    assert not out.exists(), "a 0-test artifact must never be written"


def test_converter_monorepo_suffix_fallback(tmp_path):
    """Self-audit 2026-07-07 (finding 7): the capture runs inside a monorepo
    subdirectory (where go.mod/package.json live) while spans.json keys carry
    the index-root prefix — a unique suffix match must recover attribution."""
    spans = {"backend/calc/calc.go": [["backend/calc/calc.go::Add", 3, 5]]}
    (tmp_path / "spans.json").write_text(json.dumps(spans))
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.24\n")
    (tmp_path / "covdata").mkdir()
    (tmp_path / "tests.txt").write_text("example.com/demo/calc::TestAdd\n")
    (tmp_path / "covdata" / "0.out").write_text(
        "mode: set\nexample.com/demo/calc/calc.go:3.20,5.2 1 1\n")
    got = _run_converter(tmp_path, "goprofile")
    assert got["tests"] == {
        "example.com/demo/calc::TestAdd": ["backend/calc/calc.go::Add"]}


def test_converter_bad_capture_skipped_not_fatal(tmp_path):
    (tmp_path / "spans.json").write_text(json.dumps(SPANS))
    (tmp_path / "covdata").mkdir()
    (tmp_path / "tests.txt").write_text("t_bad\nt_good\n")
    (tmp_path / "covdata" / "0.json").write_text("{ not json")
    good = {"data": [{"functions": [
        {"name": "x", "count": 1, "regions": [[1, 1, 20, 2, 1, 0, 0, 0]],
         "filenames": [str(tmp_path / "src/lib.rs")]}]}]}
    (tmp_path / "covdata" / "1.json").write_text(json.dumps(good))
    got = _run_converter(tmp_path, "llvm-json")
    assert got["tests"] == {"t_good": ["src/lib.rs::outer"]}


# -- kit generation ------------------------------------------------------------
def test_spans_json_matches_index(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "lib.rs").write_text(textwrap.dedent("""\
        pub fn greet(name: &str) -> String {
            format!("hello {}", name)
        }

        pub fn wave() -> u8 {
            1
        }
    """))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    res = scaffold_coverage(store, out_dir=str(tmp_path / "kit"), language="rust")
    assert res.ok and "rust" in res.result["turnkey"]
    spans = json.loads((tmp_path / "kit" / "spans.json").read_text())
    entries = {e[0]: (e[1], e[2]) for e in spans["src/lib.rs"]}
    assert entries["src/lib.rs::greet"] == (1, 3)
    assert entries["src/lib.rs::wave"] == (5, 7)
    store.close()


def test_turnkey_manifest_lists_four_languages(tmp_path):
    store = sg.Store(":memory:")
    for lang, expect in (("python", True), ("rust", True), ("go", True),
                         ("javascript", True), ("java", False)):
        res = scaffold_coverage(store, out_dir=str(tmp_path / lang), language=lang)
        assert res.ok
        assert (lang in res.result["turnkey"]) is expect, lang
        run = (tmp_path / lang / "run_coverage.sh").read_text()
        assert ("TODO" in run) is (not expect), lang
    store.close()


# -- real-toolchain end to end (gated) -----------------------------------------
def test_go_kit_end_to_end(tmp_path):
    if shutil.which("go") is None:
        pytest.skip("go not installed")
    root = tmp_path / "godemo"
    (root / "calc").mkdir(parents=True)
    (root / "go.mod").write_text("module example.com/godemo\n\ngo 1.21\n")
    (root / "calc" / "calc.go").write_text(textwrap.dedent("""\
        package calc

        func Add(a, b int) int {
            return a + b
        }

        func Mul(a, b int) int {
            total := 0
            for i := 0; i < b; i++ {
                total = Add(total, a)
            }
            return total
        }
    """))
    (root / "calc" / "calc_test.go").write_text(textwrap.dedent("""\
        package calc

        import "testing"

        func TestAdd(t *testing.T) {
            if Add(2, 3) != 5 {
                t.Fatal("add")
            }
        }

        func TestMul(t *testing.T) {
            if Mul(3, 4) != 12 {
                t.Fatal("mul")
            }
        }
    """))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    assert scaffold_coverage(store, out_dir=str(root / "kit"), language="go").ok
    store.close()
    # No copying kit files into the project root: the script must find its own
    # to_canonical.py/spans.json relative to ITSELF (self-audit 2026-07-07 —
    # the kit used to silently require an undocumented copy step).
    proc = subprocess.run(["bash", "kit/run_coverage.sh"], cwd=root,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    got = json.loads((root / "coverage_modes.json").read_text())
    assert got["tests"]["example.com/godemo/calc::TestAdd"] == ["calc/calc.go::Add"]
    assert got["tests"]["example.com/godemo/calc::TestMul"] == [
        "calc/calc.go::Add", "calc/calc.go::Mul"]
