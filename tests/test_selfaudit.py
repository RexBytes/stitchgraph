"""Fixes surfaced by the v3.23.0 self-analysis dogfood (research/12):
  1. scan `live_stub` false-positive — a function registered via a call/attribute decorator
     (@app.callback(), @app.route()) with an idiomatic empty body is NOT an unimplemented stub.
  2. genuine coverage gaps that find_gaps flagged — Edge.to_dict / Edge.resolved were never
     exercised by the suite.
"""
from __future__ import annotations

import ast

import stitchgraph as sg
from stitchgraph.core.extract.python import _is_stub
from stitchgraph.core.model import Edge, Provenance, Relation


def _stub(src: str) -> bool:
    return _is_stub(ast.parse(src).body[0])


def test_registration_decorator_empty_body_is_not_a_stub():
    # idiomatic registered callbacks/routes — the decorator carries the behaviour
    assert not _stub("@app.callback(invoke_without_command=True)\ndef f():\n    pass")
    assert not _stub("@app.route('/x')\ndef f():\n    pass")
    assert not _stub("@foo.register\ndef f():\n    pass")          # attribute decorator
    # genuine stubs are still detected
    assert _stub("def f():\n    pass")                            # bare pass, no decorator
    assert _stub("def f():\n    ...")                             # ellipsis
    assert _stub("@property\ndef f(self):\n    pass")             # bare-name decorator
    assert _stub("@app.route('/x')\ndef f():\n    raise NotImplementedError")  # explicit, even decorated
    assert not _stub("def f():\n    return 1")                    # real implementation


def test_scan_does_not_red_flag_typer_callback():
    """The concrete case from the dogfood: cli.py::build_app._root (a Typer @app.callback with a
    `pass` body) must not appear as a RED live_stub."""
    st = sg.Store(":memory:")
    sg.reindex(st, "src/stitchgraph")
    sc = sg.scan(st)
    res = sc.result if isinstance(sc.result, list) else (sc.result or {}).get("findings", [])
    red = [f for f in res if isinstance(f, dict) and f.get("kind") == "live_stub"
           and f.get("urgency") == "red"]
    assert not any("build_app._root" in (f.get("node") or "") for f in red)


def test_edge_resolved_property():
    assert Edge(src="a", relation=Relation.CALLS, dst_symbol="b", dst_id="m.py::b").resolved is True
    assert Edge(src="a", relation=Relation.CALLS, dst_symbol="b", dst_id=None).resolved is False


def test_edge_to_dict_shape():
    e = Edge(src="m.py::a", relation=Relation.CALLS, dst_symbol="b", dst_id="m.py::b",
             weight=0.5, provenance=Provenance.INFERRED)
    d = e.to_dict()
    assert d == {"src": "m.py::a", "relation": Relation.CALLS.value, "dst_id": "m.py::b",
                 "dst_symbol": "b", "weight": 0.5, "provenance": Provenance.INFERRED.value}
