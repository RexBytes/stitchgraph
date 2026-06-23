"""Precision/recall eval harness (design §13.1).

The stance is precision over recall on dead code: never flag live code as dead.
This builds a project with a *known* live set and dead set and asserts the
contract — no live symbol is ever flagged, and the genuinely dead ones are found.
"""

from __future__ import annotations

from pathlib import Path

import stitchgraph as sg

# A deliberately simple project (no classes / context managers, which today's
# name-based resolution can't fully model) so the dead/live truth is crisp.
SOURCE = {
    "lib/__init__.py": 'from .api import entry\n__all__ = ["entry"]\n',
    "lib/api.py": (
        "from .helpers import used_helper\n\n"
        "def entry(x):\n"               # exported -> live root
        "    return used_helper(x)\n"
    ),
    "lib/helpers.py": (
        "def used_helper(x):\n"          # reached from entry -> live
        "    return inner(x)\n\n"
        "def inner(x):\n"                # reached from used_helper -> live
        "    return x + 1\n\n"
        "def dead_one():\n"              # nobody calls -> dead
        "    return dead_two()\n\n"
        "def dead_two():\n"              # only reached from dead_one -> dead
        "    return 0\n"
    ),
}

LIVE = {"entry", "used_helper", "inner"}
DEAD = {"dead_one", "dead_two"}


def _build(root: Path) -> sg.Store:
    for rel, text in SOURCE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    store = sg.Store(":memory:")
    sg.reindex(store, str(root))
    return store


def test_precision_no_live_flagged_dead(tmp_path):
    with _build(tmp_path) as store:
        flagged = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        # PRECISION: not a single live symbol may be flagged stale.
        assert flagged & LIVE == set(), f"live code wrongly flagged: {flagged & LIVE}"


def test_recall_finds_known_dead(tmp_path):
    with _build(tmp_path) as store:
        flagged = {c["id"].split("::")[-1] for c in sg.find_stale(store).result}
        # RECALL: the genuinely dead functions are surfaced.
        assert DEAD <= flagged, f"missed dead code: {DEAD - flagged}"


def test_stale_is_advisory_not_asserted(tmp_path):
    """Dead-code results must stay needs_review, never a confident verdict."""
    with _build(tmp_path) as store:
        res = sg.find_stale(store)
        assert res.needs_review
        assert res.confidence < 0.8
