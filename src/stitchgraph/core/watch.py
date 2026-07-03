"""File-change watching (design §1, incremental updates).

Polls source-file mtimes (stdlib, no dependency) and reports when something
changed, so the CLI `watch` command can re-index on edits. Full reindex is fast
at personal scale; the per-file `Store.replace_file` path remains for true
incremental updates later.
"""

from __future__ import annotations

import os
from pathlib import Path

from .extract.python import SKIP_DIRS
from .extract.treesitter import EXT_LANG

SOURCE_EXTS = {".py", *EXT_LANG}


def snapshot(root: str | Path) -> dict[str, float]:
    """Map of source file -> mtime under root. Prunes the extractors' shared SKIP_DIRS —
    a private 8-entry copy had drifted from it (no .tox/.mypy_cache/vendor/…), so tox or
    `composer install` churn triggered full reindexes of files the indexer would never
    read (review 2026-07-03, F10b)."""
    root = Path(root)
    out: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if Path(f).suffix in SOURCE_EXTS:
                p = os.path.join(dirpath, f)
                try:
                    out[p] = os.path.getmtime(p)
                except OSError:
                    continue
    return out


def changed(old: dict[str, float], new: dict[str, float]) -> bool:
    """True if any source file was added, removed, or modified."""
    if old.keys() != new.keys():
        return True
    return any(old[k] != new[k] for k in new)
