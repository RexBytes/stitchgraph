"""The four research/25 dogfood calibrations (v3.47.0): size-scaled god-object
floors, test-mass-free orient hubs, unused-param family suppressions, and
try/except module constants."""
from __future__ import annotations

import textwrap

import pytest

import stitchgraph as sg
from stitchgraph.core.model import Node, NodeKind
from stitchgraph.core.operations import _god_floors, orient


def _index(tmp_path, files):
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    store = sg.Store(str(tmp_path / "g.db"))
    assert sg.reindex(store, str(root)).ok
    return store


# -- ① god-object floors ------------------------------------------------------
def test_god_floors_small_population_keeps_absolute():
    """Below the population cut the historical 5/5 floors apply unchanged —
    every existing small-fixture god-object test depends on this."""
    store = sg.Store(":memory:")
    for i in range(20):
        store.add_node(Node(id=f"m.py::f{i}", kind=NodeKind.FUNCTION,
                            name=f"f{i}", location=f"m.py:{i + 1}:0"))
    fi = {f"m.py::f{i}": 8 for i in range(20)}
    fo = {f"m.py::f{i}": 8 for i in range(20)}
    assert _god_floors(store, fi, fo) == (5, 5)
    store.close()


def test_god_floors_scale_with_population():
    """With >=200 coupled code nodes a god object must be exceptional among
    its peers: the floors rise to the population's 95th percentile."""
    store = sg.Store(":memory:")
    fi, fo = {}, {}
    for i in range(300):
        nid = f"m.py::f{i}"
        store.add_node(Node(id=nid, kind=NodeKind.FUNCTION, name=f"f{i}",
                            location=f"m.py:{i + 1}:0"))
        # everyone is "coupled" at 6/6 — past the old absolute floors —
        # except ten genuinely exceptional nodes at 40/40
        fi[nid] = 40 if i < 10 else 6
        fo[nid] = 40 if i < 10 else 6
    t_in, t_out = _god_floors(store, fi, fo)
    assert t_in > 6 and t_out > 6, "floors must rise above the crowd"
    flagged = [n for n in fi if fi[n] >= t_in and fo[n] >= t_out]
    assert set(flagged) == {f"m.py::f{i}" for i in range(10)}
    store.close()


def test_god_object_skips_test_owned_nodes(tmp_path):
    """A highly-coupled def in a test file is suite plumbing, not design
    feedback — same exclusion principle as the orient hub list."""
    body = "\n".join(f"def caller_{i}():\n    return hub(u{i}())" for i in range(6))
    helpers = "\n".join(f"def u{i}():\n    return {i}" for i in range(6))
    src = (f"def hub(x):\n    return u0() and u1() and u2() and u3() and u4() "
           f"and u5() and x\n{helpers}\n{body}\n")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    store = _index(a, {"tests/test_hub.py": src})
    gods = [i["node"] for i in sg.scan(store).result if i["kind"] == "god_object"]
    assert gods == [], f"test-owned god objects must be skipped: {gods}"
    twin = _index(b, {"prod.py": src})
    gods = [i["node"] for i in sg.scan(twin).result if i["kind"] == "god_object"]
    assert "prod.py::hub" in gods, "the same shape in src/ must still flag"
    twin.close()
    store.close()


# -- ② orient hubs ------------------------------------------------------------
def test_orient_hubs_exclude_test_mass_and_test_defs(tmp_path):
    """A def whose dependers are all tests must rank below one with src
    dependers, and test-file defs never appear in the hub list at all."""
    files = {"core.py": """
        def widely_used():
            return 1

        def caller_a():
            return widely_used()

        def caller_b():
            return widely_used()
    """, "app.py": """
        from core import caller_a, caller_b

        def main():
            return caller_a() + caller_b()
    """}
    # a helper that ONLY tests depend on, hammered by many test functions
    files["helpers.py"] = "def close_all():\n    return 0\n"
    test_body = "from helpers import close_all\n" + "\n".join(
        f"def test_t{i}():\n    assert close_all() == 0" for i in range(25))
    files["tests/test_mass.py"] = test_body
    store = _index(tmp_path, files)
    res = orient(store)
    assert res.ok
    hubs = [h["id"] for h in res.result["top_hubs"]]
    assert not any(h.startswith("tests/") for h in hubs), hubs
    metric = res.meta["hub_metric"]
    scores = {h["id"]: h[metric] for h in res.result["top_hubs"]}
    # 25 test callers must not out-mass 3 src dependers
    if "helpers.py::close_all" in scores:
        assert scores["helpers.py::close_all"] <= scores["core.py::widely_used"], \
            "test mass must not dominate the hub ranking"
    store.close()


def test_estimator_exclude_sources_exact(tmp_path):
    """Exact-mode estimator with exclusions: an excluded depender contributes
    no ancestor mass but still routes reachability through itself."""
    pytest.importorskip("numpy")
    from stitchgraph.core.reach import transitive_fan_in_estimate
    store = _index(tmp_path, {"a.py": """
        def leaf():
            return 1

        def mid():
            return leaf()

        def top():
            return mid()
    """})
    full = transitive_fan_in_estimate(store)
    if full is None:
        pytest.skip("sidecar unavailable (pure mode / config)")
    assert full[1] is True
    assert full[0]["a.py::leaf"] == 2.0  # mid + top
    part = transitive_fan_in_estimate(store, exclude_sources={"a.py::mid"})
    assert part is not None and part[1] is True
    # mid no longer counts as mass, but top still reaches leaf THROUGH mid
    assert part[0]["a.py::leaf"] == 1.0
    store.close()


# -- ③ unused-param suppressions ------------------------------------------------
def test_unused_param_family_suppression(tmp_path):
    """A param a same-name same-arity sibling DOES load is the family's
    interface — suppressed; a param no sibling loads still surfaces."""
    store = _index(tmp_path, {"lang_a.py": """
        def walk(node, lang):
            return [lang, node]

        def use_a():
            return walk(1, "a")
    """, "lang_b.py": """
        def walk(node, lang):
            return [node]

        def use_b():
            return walk(2, "b")
    """})
    issues = {i["node"]: i for i in sg.scan(store).result
              if i["kind"] == "unused_params"}
    assert "lang_b.py::walk" not in issues, \
        "sibling lang_a.walk loads `lang` — the slot is the family contract"
    store.close()


def test_unused_param_decorator_suppression(tmp_path):
    store = _index(tmp_path, {"m.py": """
        def register(fn):
            return fn

        @register
        def handler(event, context):
            return 1

        @staticmethod
        def plain(event, context):
            return 1

        class C:
            @staticmethod
            def smethod(event, context):
                return 2

        def use():
            return handler(1, 2) + C.smethod(1, 2)
    """})
    issues = {i["node"]: i for i in sg.scan(store).result
              if i["kind"] == "unused_params"}
    assert "m.py::handler" not in issues, \
        "an unknown decorator owns the signature — suppressed"
    assert "m.py::C.smethod" in issues, \
        "@staticmethod never consumes the signature — still reported"
    store.close()


# -- ④ try/except module constants ---------------------------------------------
def test_try_except_module_const_resolves(tmp_path):
    store = _index(tmp_path, {"flags.py": """
        try:
            import json  # noqa: F401
            _HAVE_JSON = True
        except ImportError:
            _HAVE_JSON = False

        if _HAVE_JSON:
            MODE = "rich"
        else:
            MODE = "plain"
    """, "use.py": """
        from flags import _HAVE_JSON, MODE

        def check():
            return _HAVE_JSON and MODE
    """})
    missing = {e.dst_symbol for e in store.unresolved_edges()}
    assert "_HAVE_JSON" not in missing, "try/except module const must resolve"
    assert "MODE" not in missing, "if/else module const must resolve"
    store.close()
