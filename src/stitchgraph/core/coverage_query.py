"""Forward-looking queries over a per-test coverage artifact (the same `stitchgraph-coverage-v1`
matrix `find_modes` consumes) — the *runtime* companion to the static call-graph ops. Pure set math
over the co-activation matrix `M[test, function]`; **no numpy** (unlike `find_modes`' SVD), so these
stay light and lazy. Advisory and read-only: they read the inert matrix, never execute code and never
touch the graph.

Two questions they answer:
  * "which tests should I run for a change to function X?" — the tests that *executed* X (`tests_for`),
    which `select_tests` fuses with the static blast radius.
  * "what code moves together with X / what implements this behaviour?" — the functions that co-activate
    with X across the suite (`co_functions`), i.e. its behavioural neighbourhood.
"""
from __future__ import annotations

import collections
from collections.abc import Callable

# base_test_id/normalize live in modes.py (imports must point that way — modes can't import
# this module back) so the POD ops normalize with the SAME logic (review 2026-07-03, F4).
from .modes import base_test_id, load_coverage, normalize

__all__ = ["load_coverage", "base_test_id", "normalize", "invert", "tests_for", "co_functions",
           "coactivation_pairs", "hidden_coupling", "untested", "greedy_order", "redundant_groups",
           "core_functions", "mode_drift"]

# A test that touches more than this many functions contributes ~n² pairs to the co-activation count;
# such near-global tests (a smoke/end-to-end that runs everything) add noise, not signal, so their
# pairwise contribution is skipped — this also bounds memory on huge suites (cardinal: never OOM).
_COOC_FUNC_CAP = 400


def invert(cov: dict[str, list[str]]) -> dict[str, set[str]]:
    """function id -> set of (normalised) test ids that executed it."""
    fmap: dict[str, set[str]] = {}
    for tid, funcs in cov.items():
        bt = base_test_id(tid)
        for f in funcs:
            fmap.setdefault(f, set()).add(bt)
    return fmap


def tests_for(cov: dict[str, list[str]], function_ids: set[str]) -> set[str]:
    """Normalised test ids that executed any of `function_ids`."""
    fset = set(function_ids)
    out: set[str] = set()
    for tid, funcs in cov.items():
        if fset.intersection(funcs):
            out.add(base_test_id(tid))
    return out


def co_functions(cov: dict[str, list[str]], fid: str, k: int = 20
                 ) -> list[tuple[str, float, int]]:
    """Functions whose per-test activation most resembles `fid`'s — the behavioural neighbourhood.
    Score is cosine similarity over the (binary) test columns: |T_f ∩ T_g| / sqrt(|T_f|·|T_g|).
    Returns up to `k` `(function_id, score, shared_test_count)` tuples, most similar first. Empty if
    `fid` never executed."""
    fmap = invert(cov)
    anchor = fmap.get(fid)
    if not anchor:
        return []
    a = len(anchor)
    out: list[tuple[str, float, int]] = []
    for g, ts in fmap.items():
        if g == fid:
            continue
        inter = len(anchor & ts)
        if not inter:
            continue
        score = inter / (a * len(ts)) ** 0.5
        out.append((g, round(score, 4), inter))
    out.sort(key=lambda x: (-x[1], -x[2], x[0]))
    return out[:k]


def coactivation_pairs(cov: dict[str, list[str]], min_shared: int = 3
                       ) -> list[tuple[str, str, float, int]]:
    """`(fa, fb, score, shared_tests)` for every function pair co-activating in ≥ `min_shared` tests,
    strongest first. Score is cosine over the (binary) test columns. Tests are normalised first so a
    parametrized test counts once; a near-global test (> `_COOC_FUNC_CAP` functions) is skipped so the
    pair count can't blow up quadratically."""
    norm = normalize(cov)
    sizes: dict[str, int] = collections.Counter()
    for funcs in norm.values():
        for f in funcs:
            sizes[f] += 1
    pair: collections.Counter[tuple[str, str]] = collections.Counter()
    for funcs in norm.values():
        fs = sorted(funcs)
        if len(fs) > _COOC_FUNC_CAP:
            continue
        for i in range(len(fs)):
            fi = fs[i]
            for j in range(i + 1, len(fs)):
                pair[(fi, fs[j])] += 1
    out: list[tuple[str, str, float, int]] = []
    for (a, b), c in pair.items():
        if c < min_shared:
            continue
        score = c / (sizes[a] * sizes[b]) ** 0.5
        out.append((a, b, round(score, 4), c))
    out.sort(key=lambda x: (-x[2], -x[3], x[0], x[1]))
    return out


def hidden_coupling(cov: dict[str, list[str]],
                    connected: set[frozenset[str]] | Callable[[str, str], bool],
                    min_shared: int = 3, min_score: float = 0.5, limit: int = 40
                    ) -> list[tuple[str, str, float, int]]:
    """High co-activation pairs with **no static edge** between them. `connected`
    is either the set of `frozenset({src, dst})` structurally-linked pairs, or —
    v3.39.0, the memory fix — a `(a, b) -> bool` probe: materialising the set cost
    one frozenset PER RESOLVED EDGE (~10 GB at 27M edges, the recorded
    known-cost-op peak), while only the few hundred candidate pairs the
    co-activation scan surfaces ever get LOOKED UP. These are candidates for
    *implicit* coupling — dependencies (shared state, dispatch, protocol, or a
    common caller) the call graph cannot see. Returns up to `limit`
    `(fa, fb, score, shared_tests)`, strongest first."""
    is_linked = (connected if callable(connected)
                 else lambda a, b: frozenset((a, b)) in connected)
    out: list[tuple[str, str, float, int]] = []
    for a, b, score, c in coactivation_pairs(cov, min_shared=min_shared):
        if score < min_score:
            continue
        if is_linked(a, b):
            continue
        out.append((a, b, score, c))
        if len(out) >= limit:
            break
    return out


def untested(cov: dict[str, list[str]], function_ids: set[str]) -> set[str]:
    """Of `function_ids`, those that no test executed (present in zero coverage rows)."""
    exercised = {f for funcs in cov.values() for f in funcs}
    return set(function_ids) - exercised


def greedy_order(cov: dict[str, list[str]]) -> list[tuple[str, int]]:
    """Fail-fast test order: repeatedly pick the test that adds the most *new* function coverage
    (ties broken by LOWEST test id — the same first-wins rule as `find_modes`' minimal cover,
    review 2026-07-03 F11g; the old key picked the lexicographically LAST id), then append the
    rest (which add nothing new) in id order. Returns `(test_id, new_functions)` for every
    test — the prefix up to the first 0 is a minimal cover. Once the best gain hits 0 every
    remaining gain is 0, so the tail is appended in one step instead of re-scanning the whole
    suite per pick (the old loop was O(n²·row) after exhaustion — review 2026-07-03, F11b)."""
    norm = normalize(cov)
    remaining = sorted(norm)
    covered: set[str] = set()
    order: list[tuple[str, int]] = []
    while remaining:
        best, best_gain = None, -1
        for t in remaining:  # id-sorted, so first strict maximum == lowest-id tie-break
            gain_t = len(norm[t] - covered)
            if gain_t > best_gain:
                best, best_gain = t, gain_t
        if best_gain == 0:
            order.extend((t, 0) for t in remaining)   # nothing left adds coverage
            break
        assert best is not None
        order.append((best, best_gain))
        remaining.remove(best)
        covered |= norm[best]
    return order


def redundant_groups(cov: dict[str, list[str]], min_size: int = 2) -> list[list[str]]:
    """Groups of tests with an **identical** function-coverage profile (≥ `min_size` members),
    largest first. Exact-profile only (bounded, O(n)); near-duplicate profiles are `co_change`'s job.
    NOTE these are coverage-identical, not necessarily behaviourally redundant — parametrized
    data-driven tests share a profile yet test different inputs. A review aid, never auto-delete."""
    norm = normalize(cov)
    groups: dict[frozenset[str], list[str]] = {}
    for tid, funcs in norm.items():
        groups.setdefault(frozenset(funcs), []).append(tid)
    out = [sorted(members) for members in groups.values() if len(members) >= max(2, min_size)]
    out.sort(key=lambda g: (-len(g), g[0]))
    return out


def core_functions(cov: dict[str, list[str]], top: int = 20) -> list[tuple[str, int, float]]:
    """The always-on core: functions executed by the most tests, `(function_id, test_count,
    fraction_of_tests)`, most frequent first. High frequency ≈ high behavioural blast radius."""
    norm = normalize(cov)
    n = len(norm) or 1
    freq: collections.Counter[str] = collections.Counter()
    for funcs in norm.values():
        for f in funcs:
            freq[f] += 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [(f, c, round(c / n, 4)) for f, c in ranked]


def mode_drift(old: dict[str, list[str]], new: dict[str, list[str]]) -> dict[str, list[str]]:
    """Behavioural diff between two coverage snapshots at the function-exposure level:
    which functions gained test exposure and which lost it (two categories — a newly-present
    function appears under `gained_coverage`, a removed one under `lost_coverage`)."""
    old_ex = {f for funcs in old.values() for f in funcs}
    new_ex = {f for funcs in new.values() for f in funcs}
    return {
        "gained_coverage": sorted(new_ex - old_ex),
        "lost_coverage": sorted(old_ex - new_ex),
    }
