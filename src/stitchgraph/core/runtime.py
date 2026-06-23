"""Runtime-trace fusion (design §2c). What actually executed vs. what's possible.

Ingests a coverage.py JSON report (`coverage run -m pytest && coverage json`) and
marks the nodes whose bodies actually ran. This lets the graph distinguish
"statically reachable" from "observed in practice": a runtime-hit node is
*definitely* live (so it's a reachability seed, and never dead), and dead-code
findings become more confident when grounded in a real execution.

JSON is the interchange format (stdlib `json`, zero extra deps). A node is "hit"
if any executed line falls inside its body (def-line, end-line].
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import Store


def load_coverage(trace_path: str | Path) -> tuple[dict[str, set[int]], str]:
    """Return ({abs_file: executed_lines}, base_dir). Empty on any problem."""
    p = Path(trace_path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, ""
    base = str(p.resolve().parent)  # coverage paths are relative to where it ran
    out: dict[str, set[int]] = {}
    for rel, info in (data.get("files") or {}).items():
        lines = info.get("executed_lines") or []
        absf = os.path.normpath(os.path.join(base, rel))
        out[absf] = set(lines)
    return out, base


def hit_node_ids(store: Store, covmap: dict[str, set[int]], root: str) -> set[str]:
    """Node ids whose bodies executed, per the coverage map."""
    hits: set[str] = set()
    for node in store.all_nodes_full():
        start = _start_line(node.location)
        if start is None or node.end_line is None:
            continue
        absf = os.path.normpath(os.path.join(root, node.location.split(":", 1)[0]))
        lines = covmap.get(absf)
        if lines and any(start < ln <= node.end_line for ln in lines):
            hits.add(node.id)
    return hits


def _start_line(location: str) -> int | None:
    parts = location.split(":")
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return None
