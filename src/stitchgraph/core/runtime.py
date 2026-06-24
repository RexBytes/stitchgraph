"""Runtime-trace fusion (design §2c). What actually executed vs. what's possible.

Ingests a coverage report and marks the nodes whose bodies actually ran, so the
graph can tell "statically reachable" from "observed". A runtime-hit node is
*definitely* live (a reachability seed, never dead), and dead-code findings get
more confident when grounded in a real run.

Formats (auto-detected) — multi-language:
- **coverage.py JSON** (`coverage json`) — Python.
- **LCOV** (`.info`) — JS/nyc, C/C++ gcov, and many others.
- **Go coverprofile** (`go test -coverprofile`) — `mode:` header.

A node is "hit" if any executed line falls inside its body (def-line, end-line].
Stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import Store


def load_coverage(trace_path: str | Path) -> tuple[dict[str, set[int]], str]:
    """Return ({file: executed_lines}, base_dir). Files may be absolute or
    relative; resolution is by suffix in `hit_node_ids`. Empty on any problem."""
    p = Path(trace_path)
    if not p.is_file():
        return {}, ""  # is_file() (not a bare read) so a FIFO/dir trace path returns
                       # empty instead of blocking forever: read_text() opens a FIFO and
                       # hangs, and the OSError guard never fires on a blocking open. This
                       # honours the "Empty on any problem" contract above (panel-FIFO class).
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}, ""
    base = str(p.resolve().parent)
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return _parse_json(text, base), base
    if stripped.startswith("mode:"):
        return _parse_go(text), base
    if "SF:" in text or "TN:" in text:
        return _parse_lcov(text), base
    return {}, base


def _parse_json(text: str, base: str) -> dict[str, set[int]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[str, set[int]] = {}
    for rel, info in (data.get("files") or {}).items():
        lines = info.get("executed_lines") or []
        # Coerce to ints and drop anything non-integer: a malformed/hand-crafted
        # report must not crash the later `lo <= ln <= end` range test (the LCOV and
        # Go parsers already cast defensively; JSON was the lone gap).
        out[os.path.normpath(os.path.join(base, rel))] = {
            int(ln) for ln in lines if isinstance(ln, int)
            or (isinstance(ln, str) and ln.strip().lstrip("-").isdigit())
        }
    return out


def _parse_lcov(text: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("SF:"):
            current = line[3:].strip()
            out.setdefault(current, set())
        elif line.startswith("DA:") and current is not None:
            try:
                ln, count = line[3:].split(",")[:2]
                if int(count) > 0:
                    out[current].add(int(ln))
            except ValueError:
                continue
        elif line.startswith("end_of_record"):
            current = None
    return out


def _parse_go(text: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for line in text.splitlines():
        if line.startswith("mode:") or not line.strip():
            continue
        try:
            loc, _stmts, count = line.rsplit(" ", 2)
            if int(count) <= 0:
                continue
            path, span = loc.split(":", 1)
            start, end = span.split(",")
            s_line = int(start.split(".")[0])
            e_line = int(end.split(".")[0])
            out.setdefault(path, set()).update(range(s_line, e_line + 1))
        except (ValueError, IndexError):
            continue
    return out


def hit_node_ids(store: Store, covmap: dict[str, set[int]], root: str) -> set[str]:
    """Node ids whose bodies executed. Matches a node's file to a coverage path
    by absolute path, then by suffix (robust to module-prefixed / absolute paths)."""
    norm = {os.path.normpath(k): v for k, v in covmap.items()}
    hits: set[str] = set()
    for node in store.all_nodes_full():
        start = _start_line(node.location)
        if start is None or node.end_line is None:
            continue
        rel = node.location.split(":", 1)[0]
        lines = norm.get(os.path.normpath(os.path.join(root, rel))) or _by_suffix(norm, rel)
        if not lines:
            continue
        # Body lines: exclude the def line for multi-line defs (Python marks it at
        # import time), but for a one-line def the body IS the def line.
        lo = start if start == node.end_line else start + 1
        if any(lo <= ln <= node.end_line for ln in lines):
            hits.add(node.id)
    return hits


def _by_suffix(norm: dict[str, set[int]], rel: str) -> set[int] | None:
    for k, v in norm.items():
        if k.endswith(os.sep + rel) or k.endswith("/" + rel) or k.endswith(rel):
            return v
    return None


def _start_line(location: str) -> int | None:
    parts = location.split(":")
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return None
