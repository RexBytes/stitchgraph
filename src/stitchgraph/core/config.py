"""`stitchgraph.toml` configuration (design §4 / §13.3).

The single config home: entry-point overrides (the trust escape hatch — pin roots
the detector can't see, so live code is never wrongly flagged dead), index ignore
globs, the review threshold, and the orient hub metric. Stdlib-only (tomllib).

Lookup walks up from the start directory (default: cwd) to the first
`stitchgraph.toml`; absent file -> all defaults.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # [entry_points]
    include: set[str] = field(default_factory=set)   # extra roots (force-live)
    include_tests: bool = True
    # [index]
    ignore: list[str] = field(default_factory=list)  # globs skipped on reindex
    # [review]
    threshold: float = 0.80
    # [orient]
    hub_metric: str = "transitive_fan_in"            # | fan_in | pagerank
    # [similar]
    embed_model: str | None = None                   # model2vec model for find_similar
    source: Path | None = None                       # the file we loaded, if any


def find_config(start: str | Path | None = None) -> Path | None:
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        candidate = d / "stitchgraph.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config(start: str | Path | None = None) -> Config:
    path = find_config(start)
    if path is None:
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Config()
    ep = data.get("entry_points", {})
    idx = data.get("index", {})
    rev = data.get("review", {})
    orient = data.get("orient", {})
    return Config(
        include=set(ep.get("include", []) or []),
        include_tests=bool(ep.get("include_tests", True)),
        ignore=list(idx.get("ignore", []) or []),
        threshold=float(rev.get("threshold", 0.80)),
        hub_metric=str(orient.get("hub_metric", "transitive_fan_in")),
        embed_model=(data.get("similar", {}) or {}).get("embed_model"),
        source=path,
    )
