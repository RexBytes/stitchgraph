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
    # Derived mmapped CSR sidecar for the reachability sweeps (adjcache.py). On by
    # default; costs one lazy build after each (re)index and pays it back on the
    # first sweep. Disable for read-only index locations or to pin the pure paths.
    adjacency_cache: bool = True
    # Derived token-vector sidecar for find_similar/find_component (simcache.py).
    # Same contract: lazy, generation-gated, delete-safe.
    similarity_cache: bool = True
    # Homonym-group edge compression (research/20): store each widened AMBIGUOUS
    # fan-out as one interned candidate-set reference instead of one row per
    # candidate. Pure representation change (edges_all serves the identical row
    # multiset); off = the flat-only layout, the differential campaign's control
    # arm and the escape hatch. STITCHGRAPH_NO_EDGE_COMPRESSION=1 also disables.
    edge_compression: bool = True
    # [review]
    threshold: float = 0.80
    # [orient]
    hub_metric: str = "transitive_fan_in"            # | fan_in | pagerank
    # [similar]
    embed_model: str | None = None                   # model2vec model for find_similar
    # [lsp] — the language-server precision pass (research/24). Tri-state:
    # None (AUTO, the default since v3.48.0) runs it whenever a matching
    # server binary is installed — the best available analysis by default,
    # silent fallback to the name-based graph otherwise; true forces it
    # (missing servers decline loudly); false disables. STITCHGRAPH_NO_LSP=1
    # also disables (reindex param excepted). `servers` maps a file extension
    # to a command line (empty string disables that extension's default).
    lsp_enabled: bool | None = None
    lsp_timeout: float = 15.0
    lsp_servers: dict[str, str] = field(default_factory=dict)
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
    orient, sim, lsp = _table("orient"), _table("similar"), _table("lsp")
    try:
        threshold = float(rev.get("threshold", 0.80))
    except (TypeError, ValueError):
        threshold = 0.80
    if not (0.0 <= threshold <= 1.0):
        # NaN (`float("nan")` doesn't raise) or out-of-range would silently disable
        # needs_review (`conf < nan` is always False); fall back to the default (panel ZZZ).
        threshold = 0.80
    embed = sim.get("embed_model")
    try:
        lsp_timeout = float(lsp.get("timeout", 15.0))
    except (TypeError, ValueError):
        lsp_timeout = 15.0
    if not (0.0 < lsp_timeout <= 600.0):  # NaN/zero/absurd -> default
        lsp_timeout = 15.0
    servers_tbl = lsp.get("servers")
    lsp_servers = ({str(k): str(v) for k, v in servers_tbl.items()}
                   if isinstance(servers_tbl, dict) else {})
    raw_enabled = lsp.get("enabled")
    # tri-state: bool honoured, "auto"/absent/anything else -> AUTO (None)
    lsp_enabled = raw_enabled if isinstance(raw_enabled, bool) else None
    return Config(
        include=set(_str_list(ep.get("include"))),
        root_modules=_str_list(ep.get("root_modules")),
        include_tests=bool(ep.get("include_tests", True)),
        ignore=_str_list(idx.get("ignore")),
        adjacency_cache=bool(idx.get("adjacency_cache", True)),
        similarity_cache=bool(idx.get("similarity_cache", True)),
        edge_compression=bool(idx.get("edge_compression", True)),
        threshold=threshold,
        hub_metric=str(orient.get("hub_metric", "transitive_fan_in")),
        embed_model=embed if isinstance(embed, str) else None,
        lsp_enabled=lsp_enabled,
        lsp_timeout=lsp_timeout,
        lsp_servers=lsp_servers,
        source=path,
    )
