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
    include: set[str] = field(default_factory=set)   # extra roots (force-live), exact node ids
    # Glob patterns over module FILE paths whose modules are loaded dynamically by a
    # framework (plugin trees, integration registries) and so have no static importer —
    # e.g. Home Assistant loads every `components/<domain>/*` module by name, so
    # `root_modules = ["components/*"]` roots them all and their module-level wiring
    # (schema validators, registered hooks) stops surfacing as dead-code candidates
    # (field analysis 2026-07-03). Matched with fnmatch against each MODULE node's file.
    root_modules: list[str] = field(default_factory=list)
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
    try:
        here = Path(start or Path.cwd()).resolve()
        for d in (here, *here.parents):
            candidate = d / "stitchgraph.toml"
            if candidate.is_file():
                return candidate
    except (OSError, ValueError):
        # resolve()/is_file() raise on an over-long path or an embedded NUL; a bad start
        # path simply has no config — return None so the caller falls back to defaults
        # rather than crashing (panels YYY/ZZZ — same class as the store-lookup guard).
        return None
    return None


def load_config(start: str | Path | None = None) -> Config:
    cfg = _load(start)
    # Apply the review threshold to the envelope so `[review] threshold` actually
    # gates needs_review (the documented promise in stitchgraph.toml.example).
    from .envelope import set_review_threshold
    set_review_threshold(cfg.threshold)
    return cfg


def _load(start: str | Path | None) -> Config:
    path = find_config(start)
    if path is None:
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        # A non-UTF-8 stitchgraph.toml (hand-edited in a legacy encoding) must degrade to
        # defaults, not crash every CLI command — same robustness class as malformed TOML
        # and the non-UTF-8 source-file guard in the extractor (panel R20A).
        return Config()
    # A hand-edited stitchgraph.toml can put any TOML type under any key, and config is
    # loaded on every CLI command — so guard every access by shape: a malformed section or
    # value falls back to its default instead of crashing. Same robustness class as the
    # coverage-JSON shape guard (panel LLL); `exists()`-style assumptions bite here too.
    if not isinstance(data, dict):
        return Config(source=path)

    def _table(key: str) -> dict:
        v = data.get(key)
        return v if isinstance(v, dict) else {}

    def _str_list(v: object) -> list[str]:
        # Drop empty entries: an empty glob in `ignore` reaches PurePath.match() and
        # raises "empty pattern" (panel R33B); a blank entry is never a useful value.
        return [s for x in v if (s := str(x))] if isinstance(v, list) else []

    ep, idx, rev = _table("entry_points"), _table("index"), _table("review")
    orient, sim = _table("orient"), _table("similar")
    try:
        threshold = float(rev.get("threshold", 0.80))
    except (TypeError, ValueError):
        threshold = 0.80
    if not (0.0 <= threshold <= 1.0):
        # NaN (`float("nan")` doesn't raise) or out-of-range would silently disable
        # needs_review (`conf < nan` is always False); fall back to the default (panel ZZZ).
        threshold = 0.80
    embed = sim.get("embed_model")
    return Config(
        include=set(_str_list(ep.get("include"))),
        root_modules=_str_list(ep.get("root_modules")),
        include_tests=bool(ep.get("include_tests", True)),
        ignore=_str_list(idx.get("ignore")),
        threshold=threshold,
        hub_metric=str(orient.get("hub_metric", "transitive_fan_in")),
        embed_model=embed if isinstance(embed, str) else None,
        source=path,
    )
