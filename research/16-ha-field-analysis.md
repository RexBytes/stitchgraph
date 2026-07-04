# 16 — Field analysis: Home Assistant 2024.3.3 (the first post-constant-memory hunt)

**Date:** 2026-07-03/04 · **Tool:** v3.28.0 (PR #44) · **Corpus:** HA 2024.3.3 sdist,
`homeassistant/` package — 6,728 files, 58,998 nodes, 16.0M edges, indexed end-to-end
under a 4 GB address-space ulimit at 158 MB peak (the index whose construction is the
v3.28.0 validation). This is the first *analysis* run on a graph of this size, and it
paid for itself twice: real dead code in HA, and three scale defects/artifacts in our
own query layer — all fixed in v3.29.0.

## POD verdict

Not runnable here, honestly: `find_modes` / `find_gaps` / `select_tests` consume a
**captured behavioural matrix** (coverage vectors from running tests), the sdist ships
no tests, and HA's suite needs its full dev environment. The ops would refuse, which is
the designed behaviour. A real POD pass = clone HA's git repo, install the dev env, run
`tests/util` + `tests/helpers` under the capture harness, `find_modes` on core.
Recorded as future work.

## What HA's devs missed (grep-verified: zero call sites in the shipped package)

`find_stale` → 1,703 candidates at confidence 0.6 / `needs_review=True`. ~1,300 are
HA's dynamic conventions (service-dispatched entity methods, `getattr`-loaded hooks) —
the hedged envelope doing its job. The verified residue:

- `util/color.py::rgbww_to_color_temperature` + `_white_levels_to_color_temperature` —
  a dead pair (the private helper's only caller is the dead public function)
- `util/network.py::is_ipv6_address` — added for symmetry; its IPv4 twin has many users
- `util/json.py::json_loads_array`, `helpers/config_validation.py::string_with_no_html`
- `helpers/deprecation.py`: `deprecated_class`, `deprecated_function`,
  `deprecated_substitute`, `get_deprecated` (4 of 5 helpers unused in the tree)
- `loader.py::manifest_from_legacy_module` (legacy shim), `setup.py::setup_component`
  (sync twin, test-suite-only), `scripts/benchmark::_create_state_changed_event_from_old_new`
- components: `calendar/__init__.py::_get_api_date`,
  `opencv/image_processing.py::_create_processor_from_config`

Caveats stated: 2024.3.3 vintage; public names may have custom-integration users; tests
aren't shipped in the sdist. The private ones are unambiguous.

Correct-but-instructive false positives: `helpers/condition.py::{sun,time,zone}_from_config`
(dispatched via `getattr(sys.modules[__name__], ...)` — condition.py:221) and
`_ScriptRun._async_*_step` (f-string `getattr` dispatch). `find_holes` (29, conf 0.7) were
all index-boundary imports from third-party libs. `find_chokepoints` (conf 1.0):
`cv.string` blast radius 132, `Platform` enum 100 — config validation is HA's true
articulation layer.

## The detector experiment → `[entry_points] root_modules`

A 6-line `EntryPointDetector` seeding every `components/` MODULE node rescued exactly
the right 33 candidates (`register_discovery_flow`, `cv.x10_address`, diagnostics
privates, zha dispatch-dict entries) — everything rooted only from modules HA's loader
imports dynamically by name. Now shipped as config:

```toml
[entry_points]
root_modules = ["components/*"]
```

Module-node seeding is the right granularity: it roots the module-level wiring without
blanket-rescuing unreferenced functions in the same file (pinned by
`test_root_modules_glob_roots_dynamic_plugin_tree`).

## Tool defects found by the run (fixed in v3.29.0)

1. **`scan` was Edge-object scale, twice.** Its provenance-share step indexed every
   resolved edge into Python dicts (`resolved_edges()` + two dict-of-lists), and its
   EXTRACTED-only liveness sweep materialised the same list again inside
   `_adjacency(edge_filter=...)`. MemoryError at a 6 GB cap on the 16M-edge graph while
   every sweep around it ran at adjacency scale. Fixed: per-component / per-candidate
   COUNT queries in SQLite (temp-table join for cycles, indexed probes for god objects)
   and a streaming `Store.iter_resolved_full()` for the filtered sweep. Differential:
   162 issues on stitchgraph's own graph, byte-identical old vs new. Calibrated on the
   ~1.2M-edge gate corpus: **1,486 MB → 185 MB** (and ~3× faster); the memory gate now
   runs `scan` under a 400 MB cap that demonstrably kills the old code.
2. **`orient`'s hub list drowned in homonym artifacts.** Top "hubs" were `.hass`/`.data`
   attribute nodes with fan-in ~12,000 — pure AMBIGUOUS widening arms across 8,600
   classes. The fan-in fallback now counts CONFIDENT (EXTRACTED) edges only, via one SQL
   GROUP BY (O(nodes), no Python edge sweep), reported as `confident_fan_in`. Pinned by
   a test where the homonym's raw fan-in (9) strictly dominates the real hub's (3) and
   the ranking must invert. Follow-up: the GraphBLAS metrics (`transitive_fan_in`,
   `pagerank`) still rank over raw matrices — a provenance-filtered matrix variant is
   the remaining piece.
3. **`risk` refused correctly** on the git-less sdist (confidence 0.0) — no action.

## Scale profile of the query layer on 16M edges (16 GB box)

| op | time | peak RSS |
|---|---|---|
| `orient` (pre-fix fallback) | 106 s | 63 MB |
| `find_holes` | 11 s | 63 MB |
| `find_stale` | 119 s | 1.97 GB |
| `find_chokepoints` | 216 s | 3.24 GB |
| `scan` (pre-fix) | died | >6 GB |
| `scan` (fixed) | see v3.29.0 notes | adjacency scale |

The remaining O(edges) query structure is the compact in-memory adjacency itself
(documented in LIMITATIONS.md); pushing it to GraphBLAS-on-disk is the next rung if a
graph outgrows it.
