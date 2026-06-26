"""Corrupt-store oracle — the CHOKEPOINT invariant.

stitchgraph's own writers never emit a malformed row, but a tampered / bit-rotted /
old index can. Every DB row becomes a `Node`/`Edge` through exactly two functions —
`_row_to_node` / `_row_to_edge`. So the whole "an op must never raise on a corrupt
index" contract reduces to ONE invariant at that chokepoint:

    for any column poisoned with any wrong-typed value, the mapper either skips the
    row (returns None) or returns an object whose EVERY field has its declared type.

Checking here, not at the dozens of downstream consumers, is what makes this oracle
complete (a consumer-level check false-cleans when it happens not to hit the path —
panel R32B). The column list is read from `_SCHEMA`, so a newly-added column is
covered automatically — no hand-list to fall out of sync (panels R29-R32).
"""
from __future__ import annotations

import math
import sqlite3
import tempfile
from pathlib import Path

import pytest

from stitchgraph.core.envelope import Provenance
from stitchgraph.core.model import Edge, Node, NodeKind, Relation
from stitchgraph.core.store import _SCHEMA, Store, _row_to_edge, _row_to_node

# field -> declared dataclass type (NoneType allowed where the field is Optional)
_NODE_TYPES = {"id": str, "kind": NodeKind, "name": str, "location": str,
               "end_line": (int, type(None)), "is_stub": bool,
               "arity": (int, type(None)), "summary": (str, type(None)),
               "roles": frozenset}
_EDGE_TYPES = {"src": str, "relation": Relation, "dst_symbol": str,
               "dst_id": (str, type(None)), "weight": float, "provenance": Provenance,
               "location": str, "source": str, "name_based": bool}
_POISONS = {"blob": b"\x00\x01", "nan": float("nan"), "inf": float("inf"),
            "badenum": "NOT_AN_ENUM", "bigint": 2**70, "neg": -5}


def _columns(table: str) -> list[str]:
    with sqlite3.connect(":memory:") as c:
        c.row_factory = sqlite3.Row
        c.executescript(_SCHEMA)
        return [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]


def _seed(path: str) -> None:
    s = Store(path)
    s.add_node(Node("a.py::f", NodeKind.FUNCTION, "f", location="a.py:1:0",
                    summary="doc", end_line=3, arity=1))
    s.add_edge(Edge("a.py::f", Relation.CALLS, "f", dst_id="a.py::f"))
    s.commit()
    s.close()


def _violations(obj, types: dict, where: str) -> list[str]:
    bad = []
    for field, typ in types.items():
        val = getattr(obj, field)
        if not isinstance(val, typ):
            bad.append(f"{where}: {field}={type(val).__name__} (want {typ})")
        if field == "weight" and isinstance(val, float) and not math.isfinite(val):
            bad.append(f"{where}: weight non-finite {val}")
    return bad


@pytest.mark.parametrize("table,types,mapper", [
    ("nodes", _NODE_TYPES, _row_to_node),
    ("edges", _EDGE_TYPES, _row_to_edge),
])
def test_row_mapper_invariant_under_every_column_poison(table, types, mapper):
    """Schema-derived: poison each column with each wrong-typed value; the mapper must
    skip the row or return a fully type-correct object. Closes the corrupt-store class
    by construction — a new column is covered without editing this test."""
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as d:
        for col in _columns(table):
            for pname, pval in _POISONS.items():
                path = str(Path(d) / f"{table}_{col}_{pname}.db")
                _seed(path)
                conn = sqlite3.connect(path)
                try:
                    conn.execute(f"UPDATE {table} SET {col} = ?", (pval,))
                    conn.commit()
                except (sqlite3.Error, OverflowError):
                    conn.close()
                    continue  # value SQLite can't store (NOT NULL / PK / >64-bit) -> unreachable
                conn.row_factory = sqlite3.Row
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                conn.close()
                for r in rows:
                    obj = mapper(r)
                    if obj is not None:
                        bad += _violations(obj, types, f"{table}.{col}={pname}")
    assert not bad, "row-mapper type invariant violated:\n  " + "\n  ".join(bad[:20])


def test_ops_never_raise_on_a_corrupt_index(tmp_path):
    """End-to-end backstop for the chokepoint: a BLOB in every nodes/edges column, then
    every op returns a Result and emits no Infinity/NaN in JSON."""
    import json

    import stitchgraph as sg
    db = str(tmp_path / "corrupt.db")
    _seed(db)
    conn = sqlite3.connect(db)
    for table in ("nodes", "edges"):
        for col in _columns(table):
            try:
                conn.execute(f"UPDATE {table} SET {col} = X'0001'")
            except sqlite3.Error:
                pass
    conn.commit()
    conn.close()
    with sg.Store(db) as store:
        for call in (lambda: sg.find_stale(store), lambda: sg.scan(store),
                     lambda: sg.orient(store), lambda: sg.find_holes(store),
                     lambda: sg.find_similar(store, "f"),
                     lambda: sg.get_matrix(store, "a.py"),
                     lambda: sg.summarize_subsystem(store, "a.py"),
                     lambda: sg.risk(store, str(tmp_path))):
            r = call()
            assert r.ok in (True, False)
            js = json.dumps(r.to_dict())
            assert "Infinity" not in js and "NaN" not in js
