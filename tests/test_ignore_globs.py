"""v3.37.0: root-anchored gitignore-style ignore globs (research/18 bug 2).

The old `PurePath.match` matcher failed in BOTH directions on the Home Assistant
field run: right-anchoring made `script/**` swallow `homeassistant/components/
script/*` (6 files wrongly dropped), and pre-3.13 non-recursive `**` made
`tests/components/**` ignore nothing below one level (6,627 files wrongly
indexed). Every case here is one of those two failure directions or a
compatibility guarantee the fix keeps."""

from __future__ import annotations

import stitchgraph as sg
from stitchgraph.core.globs import ignored

# -- the two field-run failure directions, pinned exactly ---------------------

def test_anchored_pattern_does_not_match_nested_same_named_dir():
    """`script/**` means the TOP-LEVEL script tree — not any dir named script."""
    assert ignored("script/x.py", ["script/**"])
    assert ignored("script/hassfest/model.py", ["script/**"])  # deep, still top-level tree
    assert not ignored("homeassistant/components/script/config.py", ["script/**"])
    assert not ignored("homeassistant/components/script/__init__.py", ["script/**"])


def test_double_star_is_recursive():
    """`tests/components/**` ignores the WHOLE subtree, however deep."""
    pats = ["tests/components/**"]
    assert ignored("tests/components/abode/test_init.py", pats)
    assert ignored("tests/components/a/b/c/d.py", pats)
    assert ignored("tests/components/conftest.py", pats)
    assert not ignored("tests/helpers/test_condition.py", pats)
    assert not ignored("tests/components", pats)  # the dir itself isn't a file match


# -- semantics kept / added ----------------------------------------------------

def test_no_slash_matches_basename_or_dirname_anywhere():
    assert ignored("a/b/app.min.js", ["*.min.js"])
    assert ignored("app.min.js", ["*.min.js"])
    assert ignored("pkg/__snapshots__/x.py", ["__snapshots__"])  # dir name anywhere
    assert not ignored("pkg/snapshots/x.py", ["__snapshots__"])


def test_directory_pattern_ignores_subtree_without_trailing_glob():
    assert ignored("gen/fixtures/deep/x.py", ["gen/fixtures"])
    assert not ignored("gen/fixtures_extra/x.py", ["gen/fixtures"])


def test_mid_pattern_double_star_and_classes():
    assert ignored("a/b/c/gen.py", ["a/**/gen.py"])
    assert ignored("a/gen.py", ["a/**/gen.py"])  # ** matches zero segments
    assert not ignored("b/a/gen.py", ["a/**/gen.py"])
    assert ignored("pkg/v1.py", ["pkg/v[0-9].py"])
    assert not ignored("pkg/vx.py", ["pkg/v[0-9].py"])


def test_exact_path_and_star_stay_within_segment():
    assert ignored("pkg/generated.py", ["pkg/generated.py"])
    assert ignored("pkg/gen_a.py", ["pkg/gen_*.py"])
    assert not ignored("pkg/sub/gen_a.py", ["pkg/gen_*.py"])  # * never crosses '/'


def test_empty_and_slash_only_patterns_are_skipped():
    assert not ignored("a.py", ["", "/"])
    assert not ignored("a.py", None)
    assert not ignored("a.py", [])


# -- end-to-end through both extractors ----------------------------------------

def test_reindex_applies_anchored_semantics(tmp_path, monkeypatch):
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "tool.py").write_text("def top_level_tool():\n    return 1\n")
    nested = tmp_path / "app" / "script"
    nested.mkdir(parents=True)
    (nested / "logic.py").write_text("def nested_logic():\n    return 1\n")
    deep = tmp_path / "tests" / "components" / "abode"
    deep.mkdir(parents=True)
    (deep / "helper.py").write_text("def deep_test_helper():\n    return 1\n")
    (tmp_path / "stitchgraph.toml").write_text(
        '[index]\nignore = ["script/**", "tests/components/**"]\n')
    monkeypatch.chdir(tmp_path)
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(tmp_path))
        assert store.nodes_by_name("top_level_tool") == []      # anchored ignore applies
        assert store.nodes_by_name("deep_test_helper") == []    # recursive ** applies
        assert len(store.nodes_by_name("nested_logic")) == 1    # NOT swallowed by script/**
