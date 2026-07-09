"""stitchgraph.toml config: entry-point override + ignore globs."""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg


def _project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # `orphan` has no caller and isn't exported -> stale by default.
    (pkg / "m.py").write_text(
        "def orphan():\n    return helper()\n\n"
        "def helper():\n    return 1\n"
    )


def test_entry_point_override_makes_node_live(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))

    stale_before = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "orphan" in stale_before  # genuinely orphaned

    (tmp_path / "stitchgraph.toml").write_text(
        '[entry_points]\ninclude = ["pkg/m.py::orphan"]\n')
    stale_after = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
    assert "orphan" not in stale_after   # pinned as a root
    assert "helper" not in stale_after   # now reached from orphan
    store.close()


def test_ignore_glob_skips_files(tmp_path, monkeypatch):
    _project(tmp_path)
    (tmp_path / "pkg" / "generated.py").write_text("def boom():\n    return 1\n")
    (tmp_path / "stitchgraph.toml").write_text(
        '[index]\nignore = ["pkg/generated.py"]\n')
    monkeypatch.chdir(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    assert store.nodes_by_name("boom") == []  # skipped by the ignore glob
    store.close()


def test_root_modules_glob_roots_dynamic_plugin_tree(tmp_path, monkeypatch):
    """`[entry_points] root_modules` (HA field analysis 2026-07-03): a framework loads
    `plugins/<name>.py` dynamically by name, so the module has no static importer and its
    module-level wiring (a registered validator) is flagged stale. A root_modules glob
    roots the MODULE node — rescuing what the module body references, while a function
    defined in the file but never referenced stays a stale candidate."""
    pkg = tmp_path / "plugins"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "solar.py").write_text(
        "def validate(cfg):\n    return cfg\n\n"
        "def never_referenced():\n    return 0\n\n"
        "SCHEMA = {'validator': validate}\n"
    )
    monkeypatch.chdir(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))

    stale_before = {c["id"] for c in sg.find_stale(store).result}
    assert "plugins/solar.py::validate" in stale_before  # no static importer

    (tmp_path / "stitchgraph.toml").write_text(
        '[entry_points]\nroot_modules = ["plugins/*"]\n')
    stale_after = {c["id"] for c in sg.find_stale(store).result}
    assert "plugins/solar.py::validate" not in stale_after  # module-level wiring live
    assert "plugins/solar.py::never_referenced" in stale_after  # NOT blanket-rescued
    store.close()


def test_orient_hub_ranking_discounts_ambiguous_fanout(tmp_path, monkeypatch):
    """Orient's fallback hub metric counts CONFIDENT (EXTRACTED) fan-in only (HA field
    analysis 2026-07-03: homonym attribute nodes with fan-in ~12,000 of pure AMBIGUOUS
    widening arms crowded out every real hub). `hub` below is precisely imported and
    called; `get` is a homonym whose bare-name calls fan out as AMBIGUOUS arms to both
    copies — raw fan_in would rank a `get` copy at least even with `hub`, confident
    fan-in must rank `hub` first."""
    (tmp_path / "stitchgraph.toml").write_text('[orient]\nhub_metric = "fan_in"\n')
    (tmp_path / "hub.py").write_text("def hub():\n    return 1\n")
    (tmp_path / "g1.py").write_text("def get():\n    return 1\n")
    (tmp_path / "g2.py").write_text("def get():\n    return 2\n")
    for i in range(3):
        # one function calls hub precisely; three more fan out to the `get` homonym —
        # each `get` copy's RAW fan-in (9) strictly dominates hub's (3), so pre-fix
        # ranking put a `get` copy on top; confident fan-in must invert that.
        (tmp_path / f"caller{i}.py").write_text(
            "from hub import hub\n"
            f"def use{i}():\n    return hub()\n"
            + "".join(f"def u{i}_{j}():\n    return get()\n" for j in range(3)))
    monkeypatch.chdir(tmp_path)
    store = sg.Store(":memory:")
    sg.reindex(store, str(tmp_path))
    res = sg.orient(store)
    assert res.meta["hub_metric"] == "confident_fan_in"
    hubs = res.result["top_hubs"]
    assert hubs, "expected hubs"
    assert hubs[0]["id"] == "hub.py::hub", hubs
    store.close()


def test_include_tests_defaults_true_and_respects_override(tmp_path):
    """Mutation (scripts/mutate.py on config.py): the `include_tests` default flips
    True->False unnoticed — no test pinned it. Absent key must default True; an explicit
    false must be honoured."""
    from stitchgraph.core.config import load_config
    # load_config(start) searches `start` (a directory) upward for stitchgraph.toml.
    no_key = tmp_path / "nokey"
    no_key.mkdir()
    (no_key / "stitchgraph.toml").write_text("[entry_points]\ninclude = []\n")
    assert load_config(no_key).include_tests is True
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "stitchgraph.toml").write_text("[entry_points]\ninclude_tests = false\n")
    assert load_config(explicit).include_tests is False


def test_quoted_boolean_strings_fall_back_to_defaults(tmp_path):
    """A hand-quoted "false" is truthy, so bool() silently ENABLED the feature
    the user was turning off (external review 2026-07-09). Malformed boolean
    values fall back to the default — the same shape-guard rule every other
    field (tables, lists, threshold, timeout, lsp.enabled) already follows —
    while real TOML booleans are honoured."""
    from stitchgraph.core.config import load_config
    quoted = tmp_path / "quoted"
    quoted.mkdir()
    (quoted / "stitchgraph.toml").write_text(
        '[entry_points]\ninclude_tests = "false"\n'
        '[index]\nadjacency_cache = "false"\nsimilarity_cache = "no"\n'
        'edge_compression = 0\n')
    cfg = load_config(quoted)
    assert cfg.include_tests is True          # default, not bool("false") == True
    assert cfg.adjacency_cache is True
    assert cfg.similarity_cache is True
    assert cfg.edge_compression is True       # int 0 is malformed for a bool field
    honoured = tmp_path / "honoured"
    honoured.mkdir()
    (honoured / "stitchgraph.toml").write_text(
        '[entry_points]\ninclude_tests = false\n[index]\nedge_compression = false\n')
    cfg2 = load_config(honoured)
    assert cfg2.include_tests is False
    assert cfg2.edge_compression is False
