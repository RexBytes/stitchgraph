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
import re

from .modes import load_coverage

__all__ = ["load_coverage", "base_test_id", "normalize", "invert", "tests_for", "co_functions",
           "coactivation_pairs", "hidden_coupling"]

# A test that touches more than this many functions contributes ~n² pairs to the co-activation count;
# such near-global tests (a smoke/end-to-end that runs everything) add noise, not signal, so their
# pairwise contribution is skipped — this also bounds memory on huge suites (cardinal: never OOM).
_COOC_FUNC_CAP = 400

# coverage.py --cov-context=test appends exactly one of these phases after the full pytest id.
_PHASE = re.compile(r"\|(?:run|setup|teardown)$")
# pytest parametrization is the trailing bracket group; greedy + end-anchored so it also strips a
# param that itself contains '|' or nested brackets (e.g. `test[a|b]`, `test[a[b]]`).
_PARAM = re.compile(r"\[.*\]$")


def base_test_id(tid: str) -> str:
    """Normalise a coverage test-id key to stitchgraph's test node-id convention:
    drop coverage.py's `|phase` suffix and pytest `[param]` ids, and rewrite the pytest
    `file::Class::method` separator to `file::Class.method` so class-based test ids line up with
    the graph's `path::qualified.name` nodes. The phase suffix is stripped *before* the param group
    (and only when it is a real run/setup/teardown phase) so a param containing `|` is not truncated."""
    tid = _PHASE.sub("", tid)            # coverage.py --cov-context=test phase (run/setup/teardown)
    tid = _PARAM.sub("", tid)            # pytest parametrization (greedy → nested brackets / '|' safe)
    parts = tid.split("::")
    if len(parts) <= 2:
        return tid
    return parts[0] + "::" + ".".join(parts[1:])   # file::Class::method -> file::Class.method


def normalize(cov: dict[str, list[str]]) -> dict[str, set[str]]:
    """Collapse coverage rows by normalised test id — a parametrized test's `[param]`/`|phase` rows
    merge into one behavioural row (its union of executed functions), so co-activation counts a test
    once rather than once per parameter."""
    out: dict[str, set[str]] = {}
    for tid, funcs in cov.items():
        out.setdefault(base_test_id(tid), set()).update(funcs)
    return out


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


def hidden_coupling(cov: dict[str, list[str]], connected: set[frozenset[str]],
                    min_shared: int = 3, min_score: float = 0.5, limit: int = 40
                    ) -> list[tuple[str, str, float, int]]:
    """High co-activation pairs with **no static edge** between them (`connected` = the set of
    `frozenset({src, dst})` structurally-linked function pairs). These are candidates for *implicit*
    coupling — dependencies (shared state, dispatch, protocol, or a common caller) that the call graph
    cannot see. Returns up to `limit` `(fa, fb, score, shared_tests)`, strongest first."""
    out: list[tuple[str, str, float, int]] = []
    for a, b, score, c in coactivation_pairs(cov, min_shared=min_shared):
        if score < min_score:
            continue
        if frozenset((a, b)) in connected:
            continue
        out.append((a, b, score, c))
        if len(out) >= limit:
            break
    return out
