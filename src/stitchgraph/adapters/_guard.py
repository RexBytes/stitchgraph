"""Shared adapter guard: refuse queries against a missing / never-indexed DB.

`Store(path)` creates an empty database on open, so a mispointed adapter
(wrong cwd, typo'd --db, an MCP client launching the server from an arbitrary
directory) would answer every query from a vacuum at full confidence — `orient`
returns ok/1.0 with zero nodes and an LLM concludes "tiny repo, nothing dead,
all green". That is the exact failure mode the envelope contract exists to
prevent (review 2026-07-03, F2b). Query operations therefore refuse when the
DB file doesn't exist or has never been indexed; only `reindex` may create one.
"""

from __future__ import annotations

import os
import sqlite3

from ..core.envelope import Result, refuse
from ..core.store import Store

# Operations allowed to open a DB that doesn't exist yet (they populate it).
_CREATES_DB = {"reindex"}


def open_store(db: str, op_name: str) -> tuple[Store | None, Result | None]:
    """Open the index DB for an operation. Returns (store, None) on success,
    (None, refusal Result) when the op is a query and the DB is absent, empty,
    or unopenable — never silently creating an index to answer from."""
    creates = op_name in _CREATES_DB
    if not creates and db != ":memory:" and not os.path.exists(db):
        return None, refuse(
            f"index database {db!r} does not exist — run `reindex` first, or point "
            "--db (or STITCHGRAPH_DB for the MCP server) at the built index")
    try:
        store = Store(db)
    except (sqlite3.Error, OSError) as exc:
        # A db path that can't back a database (a directory, FIFO, device, or
        # unwritable location) must return an envelope, not a raw sqlite
        # traceback (panel R12).
        return None, refuse(f"cannot open index database {db!r}: {exc}")
    if not creates and not store.get_meta("root"):
        # The file exists but no reindex ever recorded a root: an empty shell
        # (typically created by a previous mispointed open) — refusing beats
        # confidently reporting an empty codebase.
        store.close()
        return None, refuse(
            f"index database {db!r} holds no indexed project — run `reindex` first")
    return store, None
