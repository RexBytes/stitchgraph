"""Pure mode — run every sweep on its reference implementation.

v3.31.0 flipped the install default: `pip install stitchgraph` brings the full
dependency set and the accelerated paths (the adjacency sidecar, GraphBLAS) are
on by default, because each is pinned byte-identical to its pure-Python reference
by the equivalence tests. This switch is the opt-OUT: `--pure` on the CLI, `--pure`
on the MCP server, or `STITCHGRAPH_PURE=1` in the environment forces the reference
paths — for debugging a suspected accelerator bug, for byte-reproducing an old
run, or for the stdlib-only footprint.

Scope: pure mode disables only accelerators with *identical-result* fallbacks
(sidecar, GraphBLAS). Operations whose functionality REQUIRES numpy (`find_modes`,
`feature_map`, …) are unaffected — disabling those wouldn't make anything purer,
just broken. The state lives in the environment (not a module global) so worker
subprocesses spawned by an op inherit it.
"""

from __future__ import annotations

import os

_ENV = "STITCHGRAPH_PURE"


def pure_mode() -> bool:
    return os.environ.get(_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def set_pure_mode(enabled: bool) -> None:
    if enabled:
        os.environ[_ENV] = "1"
    else:
        os.environ.pop(_ENV, None)
