"""Behavioural-mode analysis via POD (design §6 win 3) — the *runtime* counterpart to the static
`find_subsystems`. Given a per-test coverage artifact (which test executed which function), it forms
the co-activation matrix `M[test, function]` and takes its POD (mean-centred SVD). The singular
vectors are the codebase's **behavioural modes**: sets of functions that fire together across the
suite (routing, blueprints, contexts, …). Advisory and read-only — like `orient`/`risk`/`find_subsystems`
it never feeds liveness.

Language-agnostic by construction: it consumes a canonical JSON artifact
`{"format": "stitchgraph-coverage-v1", "tests": {"<test id>": ["<function id>", ...], ...}}`
produced (in the user's own sandbox) by `scaffold_coverage`'s capture kit, from any language's
per-test coverage tool. stitchgraph itself never runs the code — it only reads the inert matrix.

numpy is required (SVD); the optional `[spectral]` extra (scipy) adds a sparse `svds` path for very
large matrices, else a dense `numpy.linalg.svd` fallback capped at `_DENSE_CAP` on the smaller
dimension.
"""
from __future__ import annotations

import collections
import json
import math
import re
from typing import Any

from .store import Store

try:
    import numpy as np
    HAS_NUMPY = True
except Exception:  # noqa: BLE001 — numpy absent → the operation refuses cleanly
    HAS_NUMPY = False

try:
    from scipy.sparse import csr_matrix  # noqa: F401
    from scipy.sparse.linalg import svds
    HAS_SCIPY = True
except Exception:  # noqa: BLE001 — no [spectral] extra → dense numpy fallback (capped)
    HAS_SCIPY = False

_DENSE_CAP = 4000  # cap on min(n_tests, n_functions) for the dense SVD path
FORMAT = "stitchgraph-coverage-v1"


def _toks(name: str) -> list[str]:
    """Identifier → lowercase word tokens (camelCase / snake_case / dotted split), len>1."""
    out: list[str] = []
    for part in re.split(r"[._\-/]", name):
        out += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part)
    return [t.lower() for t in out if len(t) > 1]


def load_coverage(path: str) -> dict[str, list[str]]:
    """Parse the canonical per-test coverage artifact → {test_id: [function_id, ...]}. Returns {} on
    any problem (missing file, bad JSON, wrong shape) — the caller refuses cleanly, never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001 — unreadable / not JSON
        return {}
    if not isinstance(data, dict):
        return {}
    tests = data.get("tests", data if data.get("format") is None else {})
    if not isinstance(tests, dict):
        return {}
    out: dict[str, list[str]] = {}
    for tid, funcs in tests.items():
        if isinstance(tid, str) and isinstance(funcs, list):
            fs = [f for f in funcs if isinstance(f, str)]
            if fs:
                out[tid] = fs
    return out


def _leaf(fid: str) -> str:
    return fid.split("::")[-1]


def _module(fid: str) -> str:
    return fid.split("::", 1)[0] if "::" in fid else fid


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


def normalize(cov: dict[str, list[str]] | dict[str, set[str]]) -> dict[str, set[str]]:
    """Collapse coverage rows by normalised test id — a parametrized test's `[param]`/`|phase` rows
    merge into one behavioural row (its union of executed functions), so co-activation counts a test
    once rather than once per parameter. The POD ops normalise too (review 2026-07-03, F4): the
    turnkey converter emits `test|run` / `test|setup` context keys verbatim, and un-normalised rows
    inflated the test count, produced spurious "redundant pairs" (identical setup rows), and put
    non-runnable ids like `…::test_0|setup` in `minimal_test_set`."""
    out: dict[str, set[str]] = {}
    for tid, funcs in cov.items():
        out.setdefault(base_test_id(tid), set()).update(funcs)
    return out


def decompose(store: Store, coverage_path: str, k: int | None = None
              ) -> tuple[dict[str, Any], dict[str, Any]]:
    """POD over the per-test co-activation matrix. Returns (payload, meta). `payload` carries the
    ranked behavioural `modes`, the `intrinsic_dimensionality` (modes to 90% energy), a
    `minimal_test_set` (greedy cover of executed functions), and `redundant_test_pairs`. Raises
    RuntimeError with a clear message when numpy is missing or the matrix is too large for the dense
    path without scipy. Advisory — computed on demand, never feeds liveness."""
    if not HAS_NUMPY:
        raise RuntimeError("behavioural-mode analysis needs numpy")
    covn = normalize(load_coverage(coverage_path))   # merge |phase / [param] rows (F4)
    if len(covn) < 4:
        return ({"modes": [], "intrinsic_dimensionality": 0, "minimal_test_set": [],
                 "minimal_test_count": 0, "redundant_test_pairs": 0},
                {"tests": len(covn), "functions": 0, "density": 0.0, "solver": "none",
                 "reason": "need at least 4 tests with coverage"})

    tests = sorted(covn)
    funcs = sorted({f for fs in covn.values() for f in fs})
    fidx = {f: i for i, f in enumerate(funcs)}
    nT, nF = len(tests), len(funcs)
    if nF < 4:
        return ({"modes": [], "intrinsic_dimensionality": 0, "minimal_test_set": [],
                 "minimal_test_count": 0, "redundant_test_pairs": 0},
                {"tests": nT, "functions": nF, "density": 0.0, "solver": "none",
                 "reason": "need at least 4 distinct executed functions"})

    smaller = min(nT, nF)
    if not HAS_SCIPY and smaller > _DENSE_CAP:
        raise RuntimeError(
            f"co-activation matrix is {nT}x{nF} (min dim {smaller} > {_DENSE_CAP}); install the "
            "'spectral' extra (pip install 'stitchgraph[spectral]') for the sparse SVD that scales")

    # rows as column-index sets: the greedy cover / redundancy substrate, and (sparse path) the
    # matrix source — the dense ndarray is only ever built on the capped dense path (F7).
    rowsets = [{fidx[f] for f in covn[t]} for t in tests]
    nnz = sum(len(r) for r in rowsets)
    density = nnz / float(nT * nF)

    # --- POD (mean-centred SVD) ---
    kk = min(smaller - 1, 16 if k is None else max(2, min(k, smaller - 1)))
    dim_lower_bound = False
    try:
        if HAS_SCIPY and smaller > _DENSE_CAP:
            # Build the CSR directly from the row sets — materialising the dense matrix here
            # defeated the entire point of the sparse path (O(nT·nF) before svds started; F7).
            # svds runs on the raw sparse M: centring would densify, so these are uncentred
            # modes (mode 1 ≈ the mean profile); deterministic v0.
            rows = np.fromiter((i for i, rs in enumerate(rowsets) for _ in rs),
                               dtype=np.int64, count=nnz)
            cols = np.fromiter((c for rs in rowsets for c in sorted(rs)),
                               dtype=np.int64, count=nnz)
            A = csr_matrix((np.ones(nnz), (rows, cols)), shape=(nT, nF))
            v0 = np.ones(min(A.shape)) / math.sqrt(min(A.shape))
            u, s, vt = svds(A, k=kk, v0=v0)
            order = np.argsort(-s)
            S, Vt, U = s[order], vt[order], u[:, order]
            solver = "scipy"
            # Total energy of the uncentred operator is exactly ‖M‖²_F = nnz (binary matrix),
            # so mode fractions are relative to the TRUE total even though only kk singular
            # values were computed (F3).
            tot = float(nnz)
        else:
            M = np.zeros((nT, nF), dtype=float)
            for ti, rs in enumerate(rowsets):
                M[ti, list(rs)] = 1.0
            Mc = M - M.mean(axis=0, keepdims=True)
            U, S, Vt = np.linalg.svd(Mc, full_matrices=False)
            # Total energy from the FULL spectrum, before truncation — truncating first made
            # `intrinsic_dimensionality` measure "90% of the top-16 energy", silently saturating
            # at 16 and understating dimensionality on long-tailed suites (F3).
            S_full = S
            tot = float((S ** 2).sum())
            U, S, Vt = U[:, :kk], S[:kk], Vt[:kk]
            solver = "numpy-dense"
    except np.linalg.LinAlgError as exc:  # SVD non-convergence — surface as a clean refuse
        raise RuntimeError(f"SVD did not converge on the {nT}x{nF} co-activation matrix: {exc}") from exc

    energy = (S ** 2)
    # Guard degenerate energy: when every test has an identical profile the mean-centred matrix is
    # all-zero (every singular value 0), so there is *no* behavioural variance. Report intrinsic
    # dimensionality 0 rather than letting searchsorted(all-zeros, 0.90) overrun (panel R272).
    nmodes = kk if k is None else min(k, kk)
    frac = energy / tot if tot > 0 else np.zeros_like(energy)
    cum = np.cumsum(frac)
    if tot <= 0 or not len(cum):
        k90 = 0
    elif cum[-1] >= 0.90:
        # Exact: the computed spectrum reaches 90% of the TRUE total (always the case on the
        # dense path when the tail is short; frequently on the sparse path too).
        k90 = int(np.searchsorted(cum, 0.90)) + 1
    else:
        # The kk computed modes don't reach 90% of the true total. On the dense path the full
        # spectrum is available — count over it exactly (k90 may legitimately exceed the number
        # of *reported* modes: that IS the long-tail information the old clamp hid, F3). On the
        # sparse path only kk values exist, so kk is an honest lower bound (flagged in meta).
        if solver == "numpy-dense":
            full_cum = np.cumsum((S_full ** 2) / tot)
            k90 = int(np.searchsorted(full_cum, 0.90)) + 1
        else:
            k90 = kk
            dim_lower_bound = True
    modes: list[dict[str, Any]] = []
    for m in range(nmodes):
        load = Vt[m]
        order = np.argsort(-np.abs(load))[:8]
        top = [funcs[i] for i in order]
        # distinctive-token label from the top functions' leaf names
        tf: collections.Counter[str] = collections.Counter()
        for fid in top:
            tf.update(set(_toks(_leaf(fid))))
        label = " ".join(t for t, _ in tf.most_common(4)) or "(unlabelled)"
        dirs = collections.Counter(_module(funcs[i]) for i in order)
        texpr = np.argsort(-np.abs(U[:, m]))[:5]
        modes.append({
            "energy": round(float(frac[m]), 4),
            "label": label,
            "modules": [d for d, _ in dirs.most_common(3)],
            "functions": [_leaf(f) for f in top],
            "tests": [tests[i] for i in texpr.tolist()],
        })

    # --- minimal covering test set (greedy set cover) ---
    # Exhausted rows (zero new coverage) are pruned each round instead of being re-scored
    # forever: the old max-over-all-remaining + list.remove was O(nT² · row) and effectively
    # hung on 50k-row suites full of duplicate profiles (review 2026-07-03, F11b).
    target = set(range(nF))
    covered: set[int] = set()
    chosen: list[str] = []
    remaining = list(range(nT))
    while covered != target and remaining:
        best, best_gain = -1, 0
        alive: list[int] = []
        for i in remaining:
            gain_i = len(rowsets[i] - covered)
            if gain_i > 0:
                alive.append(i)
                if gain_i > best_gain:      # first-index tie-break (deterministic)
                    best, best_gain = i, gain_i
        if best < 0:
            break
        covered |= rowsets[best]
        chosen.append(tests[best])
        remaining = [i for i in alive if i != best]

    # --- redundancy: tests with an identical activation profile. Counted by grouping identical
    # function-sets (O(n_tests)) rather than a dense n×n similarity matrix — the latter is
    # unbounded by the min-dim cap and OOMs on a big-suite / few-function artifact (panel R271). ---
    groups: collections.Counter[frozenset[str]] = collections.Counter(
        frozenset(covn[t]) for t in tests)
    redundant_pairs = sum(c * (c - 1) // 2 for c in groups.values() if c > 1)

    payload = {
        "modes": modes,
        "intrinsic_dimensionality": k90,
        "minimal_test_set": chosen,   # a genuine cover of every executed function (see count)
        "minimal_test_count": len(chosen),
        "redundant_test_pairs": redundant_pairs,
    }
    meta = {"tests": nT, "functions": nF, "density": round(density, 4), "solver": solver}
    if dim_lower_bound:
        # Only kk singular values exist on the sparse path; the true 90%-energy count is ≥ kk.
        meta["intrinsic_dimensionality_is_lower_bound"] = True
    return payload, meta


def _pod(cov: dict[str, list[str]] | dict[str, set[str]], k: int | None = None) -> Any:
    """Shared POD builder for `feature_map` / `outlier_tests`. Returns
    (tests, funcs, U, S, Vt, kk, density, solver, row_energy, total_energy) or raises RuntimeError
    (numpy missing / matrix too large without scipy / SVD non-convergence). Mirrors `decompose`'s
    matrix build + mean-centred SVD — kept separate so the gated `decompose` stays untouched.
    Rows are normalized (phase/param collapse) exactly like `decompose` (F4). `row_energy` is
    per-test total variance (centred on the dense path; uncentred on the scipy path — flagged by
    `solver`); `total_energy` is the TRUE full-spectrum total, not the truncated top-kk sum (F3)."""
    if not HAS_NUMPY:
        raise RuntimeError("behavioural-mode analysis needs numpy")
    covn = {tid: set(fs) for tid, fs in cov.items()} if _is_normalized(cov) else normalize(cov)
    tests = sorted(covn)
    funcs = sorted({f for fs in covn.values() for f in fs})
    fidx = {f: i for i, f in enumerate(funcs)}
    nT, nF = len(tests), len(funcs)
    smaller = min(nT, nF)
    if not HAS_SCIPY and smaller > _DENSE_CAP:
        raise RuntimeError(
            f"co-activation matrix is {nT}x{nF} (min dim {smaller} > {_DENSE_CAP}); install the "
            "'spectral' extra (pip install 'stitchgraph[spectral]') for the sparse SVD that scales")
    rowsets = [{fidx[f] for f in covn[t]} for t in tests]
    nnz = sum(len(r) for r in rowsets)
    density = nnz / float(nT * nF)
    kk = min(smaller - 1, 16 if k is None else max(2, min(k, smaller - 1)))
    try:
        if HAS_SCIPY and smaller > _DENSE_CAP:
            # CSR built directly from the row sets — no dense detour (F7), same as decompose.
            rows = np.fromiter((i for i, rs in enumerate(rowsets) for _ in rs),
                               dtype=np.int64, count=nnz)
            cols = np.fromiter((c for rs in rowsets for c in sorted(rs)),
                               dtype=np.int64, count=nnz)
            A = csr_matrix((np.ones(nnz), (rows, cols)), shape=(nT, nF))
            v0 = np.ones(min(A.shape)) / math.sqrt(min(A.shape))
            u, s, vt = svds(A, k=kk, v0=v0)
            order = np.argsort(-s)
            S, Vt, U = s[order], vt[order], u[:, order]
            solver = "scipy"
            # uncentred (svds path): per-row energy of a binary row is its nnz
            row_energy = np.array([float(len(rs)) for rs in rowsets])
            tot = float(nnz)
        else:
            M = np.zeros((nT, nF), dtype=float)
            for ti, rs in enumerate(rowsets):
                M[ti, list(rs)] = 1.0
            Mc = M - M.mean(axis=0, keepdims=True)
            U, S, Vt = np.linalg.svd(Mc, full_matrices=False)
            tot = float((S ** 2).sum())                # full spectrum, before truncation (F3)
            U, S, Vt = U[:, :kk], S[:kk], Vt[:kk]
            solver = "numpy-dense"
            row_energy = (Mc ** 2).sum(axis=1)         # centred
    except np.linalg.LinAlgError as exc:
        raise RuntimeError(f"SVD did not converge on the {nT}x{nF} co-activation matrix: {exc}") from exc
    return tests, funcs, U, S, Vt, kk, density, solver, row_energy, tot


def _is_normalized(cov: dict[str, Any]) -> bool:
    """True when the rows are already the normalized set-valued form `normalize` returns."""
    return all(isinstance(v, (set, frozenset)) for v in cov.values())


def feature_map(store: Store, coverage_path: str, k: int | None = None,
                top_funcs: int = 10, top_tests: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per behavioural mode, the feature it represents: its top-loading **functions** (full ids — the
    feature's implementation), the **files** they span, and the **tests** that most express it. The
    actionable, full-id view of `find_modes`' modes. Raises like `decompose`. Advisory, read-only."""
    cov = normalize(load_coverage(coverage_path))
    funcs_all = {f for fs in cov.values() for f in fs}
    if len(cov) < 4 or len(funcs_all) < 4:
        return ([], {"tests": len(cov), "functions": len(funcs_all), "solver": "none",
                     "reason": "need at least 4 tests and 4 executed functions"})
    tests, funcs, U, S, Vt, kk, density, solver, _, tot = _pod(cov, k)
    energy = S ** 2
    # fractions relative to the TRUE total energy, not the truncated top-kk sum (F3)
    frac = energy / tot if tot > 0 else np.zeros_like(energy)
    nmodes = kk if k is None else min(k, kk)
    features: list[dict[str, Any]] = []
    for m in range(nmodes):
        order = np.argsort(-np.abs(Vt[m]))[:top_funcs]
        fns = [funcs[i] for i in order]
        tf: collections.Counter[str] = collections.Counter()
        for fid in fns:
            tf.update(set(_toks(_leaf(fid))))
        label = " ".join(t for t, _ in tf.most_common(4)) or "(unlabelled)"
        files = sorted({_module(f) for f in fns})[:8]
        texpr = np.argsort(-np.abs(U[:, m]))[:top_tests]
        features.append({
            "mode": m + 1,
            "energy": round(float(frac[m]), 4),
            "label": label,
            "functions": fns,
            "files": files,
            "tests": [tests[i] for i in texpr.tolist()],
        })
    meta = {"tests": len(tests), "functions": len(funcs), "density": round(density, 4),
            "solver": solver, "modes": nmodes}
    return features, meta


def outlier_tests(store: Store, coverage_path: str, k: int | None = None, limit: int = 20
                  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Tests ranked by how poorly the top behavioural modes reconstruct them (residual in mode space).
    A **high residual** test exercises behaviour the mainstream modes don't capture: either a
    *unique-behaviour* test (keep it — it's the only thing covering something) or an *everything-touching
    smoke* test. Smoke is detected by **row breadth** (the fraction of all executed functions the test
    touches) — the old |U[:,0]| criterion assumed mode 1 is the 'always-on' axis, which is only true on
    the uncentred sparse path; the default dense path is mean-centred, where mode 1 is just the largest
    variance axis (review 2026-07-03, F11f). Raises like `decompose`. Advisory, read-only."""
    cov = normalize(load_coverage(coverage_path))
    funcs_all = {f for fs in cov.values() for f in fs}
    if len(cov) < 4 or len(funcs_all) < 4:
        return ([], {"tests": len(cov), "functions": len(funcs_all), "solver": "none",
                     "reason": "need at least 4 tests and 4 executed functions"})
    tests, funcs, U, S, Vt, kk, density, solver, row_energy, _ = _pod(cov, k)
    captured = ((U[:, :kk] * S[:kk]) ** 2).sum(axis=1)
    nF = len(funcs)
    breadth = np.array([len(cov[t]) / nF for t in tests]) if nF else np.zeros(len(tests))
    b_hi = float(np.quantile(breadth, 0.90)) if len(breadth) else 0.0
    rows: list[dict[str, Any]] = []
    for i, tid in enumerate(tests):
        tot_i = float(row_energy[i])
        resid = 0.0 if tot_i <= 0 else max(0.0, 1.0 - float(captured[i]) / tot_i)
        kind = "typical"
        if resid >= 0.5:
            # broad + poorly reconstructed = everything-touching smoke; narrow = unique behaviour
            kind = "smoke" if breadth[i] >= max(b_hi, 0.5) else "unique"
        rows.append({"test": tid, "residual": round(resid, 4),
                     "breadth": round(float(breadth[i]), 4), "kind": kind})
    rows.sort(key=lambda r: (-r["residual"], r["test"]))
    meta = {"tests": len(tests), "functions": len(funcs), "density": round(density, 4),
            "solver": solver}
    return rows[:limit], meta
