"""Cardinal source-matrix oracle — a small parametrized matrix, not a generator.

The cardinal sin is live code flagged dead. The late-stage cardinals were symmetry
gaps across two axes: the SCOPE the use sits in {module, class-body, function} and the
USE-KIND {call, attribute-read, name-reference} (panels R30/R31). This enumerates the
cells: a symbol used (any kind) from a reachable site (any scope) must never appear in
`find_stale` on the confident path. A not-yet-covered cell fails here instead of waiting
for a panel. Cheap by construction — fixed templates, no synthesized source.
"""
from __future__ import annotations

import textwrap

import pytest

import stitchgraph as sg

# Each cell renders a `pkg/api.py` whose Engine.compute (and its private helper _inner)
# is *used* via (scope, use-kind) from code reachable through the exported `entry`.
_SCOPES = {
    "module":     "RESULT = {expr}\ndef entry():\n    return RESULT\n",
    "class_body": "class Holder:\n    KEPT = {expr}\ndef entry():\n    return Holder\n",
    "function":   "def entry():\n    obj = make()\n    return {expr}\n",
}
_USES = {
    "call":        "obj.compute()",
    "attr_read":   "obj.compute",
    "name_ref":    "compute_ref",       # bare-name reference to a module function
    "subscript":   "[obj.compute][0]",
}
# module/class-body scopes have no local `obj`; they use the module-level `_e`.
_RECV = {"module": "_e", "class_body": "_e", "function": "obj"}


def _render(scope: str, use: str) -> str:
    recv = _RECV[scope]
    expr = _USES[use].replace("obj.", f"{recv}.")
    if use == "name_ref":
        # exercise a bare-name reference instead of a member access
        expr = "compute_ref"
    body = _SCOPES[scope].format(expr=expr)
    return textwrap.dedent("""
        class Engine:
            def compute(self):
                return self._inner()
            def _inner(self):
                return 1
        def make():
            return Engine()
        def compute_ref():
            return Engine().compute()
        _e = make()
    """) + body


@pytest.mark.parametrize("scope", list(_SCOPES))
@pytest.mark.parametrize("use", list(_USES))
def test_used_symbol_never_flagged_dead(tmp_path, scope, use):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "p"\nversion = "0.1"\n')
    (root / "pkg" / "__init__.py").write_text("from .api import entry\n__all__ = ['entry']\n")
    (root / "pkg" / "api.py").write_text(_render(scope, use))
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(root))
        stale = {c["id"] for c in (sg.find_stale(store).result or [])}
    target = "pkg/api.py::Engine.compute" if use != "name_ref" else "pkg/api.py::compute_ref"
    assert target not in stale, f"[{scope}/{use}] live symbol flagged dead: {target}"
    # the cardinal's transitive tail: a live member's private helper must stay live too
    if use in ("call", "attr_read", "subscript"):
        assert "pkg/api.py::Engine._inner" not in stale, f"[{scope}/{use}] helper flagged dead"
