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


def decompose(store: Store, coverage_path: str, k: int | None = None
              ) -> tuple[dict[str, Any], dict[str, Any]]:
    """POD over the per-test co-activation matrix. Returns (payload, meta). `payload` carries the
    ranked behavioural `modes`, the `intrinsic_dimensionality` (modes to 90% energy), a
    `minimal_test_set` (greedy cover of executed functions), and `redundant_test_pairs`. Raises
    RuntimeError with a clear message when numpy is missing or the matrix is too large for the dense
    path without scipy. Advisory — computed on demand, never feeds liveness."""
    if not HAS_NUMPY:
        raise RuntimeError("behavioural-mode analysis needs numpy")
    cov = load_coverage(coverage_path)
    if len(cov) < 4:
        return ({"modes": [], "intrinsic_dimensionality": 0, "minimal_test_set": [],
                 "minimal_test_count": 0, "redundant_test_pairs": 0},
                {"tests": len(cov), "functions": 0, "density": 0.0, "solver": "none",
                 "reason": "need at least 4 tests with coverage"})

    tests = sorted(cov)
    funcs = sorted({f for fs in cov.values() for f in fs})
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

    M = np.zeros((nT, nF), dtype=float)
    for ti, tid in enumerate(tests):
        for f in cov[tid]:
            M[ti, fidx[f]] = 1.0
    density = float(M.mean())

    # rows/cols as sets for the greedy cover + redundancy (cheap, exact)
    rowsets = [set(np.where(M[i] > 0)[0].tolist()) for i in range(nT)]

    # --- POD (mean-centred SVD) ---
    kk = min(smaller - 1, 16 if k is None else max(2, min(k, smaller - 1)))
    Mc = M - M.mean(axis=0, keepdims=True)
    try:
        if HAS_SCIPY and smaller > _DENSE_CAP:
            # sparse randomized SVD on the (dense-centred) operator via svds needs a matrix; centre is
            # dense so fall back to dense here would defeat scale — use svds on the raw sparse M and
            # accept uncentred modes (mode 1 ≈ mean); deterministic v0.
            A = csr_matrix(M)
            v0 = np.ones(min(A.shape)) / math.sqrt(min(A.shape))
            u, s, vt = svds(A, k=kk, v0=v0)
            order = np.argsort(-s)
            S, Vt, U = s[order], vt[order], u[:, order]
            solver = "scipy"
        else:
            U, S, Vt = np.linalg.svd(Mc, full_matrices=False)
            U, S, Vt = U[:, :kk], S[:kk], Vt[:kk]
            solver = "numpy-dense"
    except np.linalg.LinAlgError as exc:  # SVD non-convergence — surface as a clean refuse
        raise RuntimeError(f"SVD did not converge on the {nT}x{nF} co-activation matrix: {exc}") from exc

    energy = (S ** 2)
    tot = float(energy.sum())
    # Guard degenerate energy: when every test has an identical profile the mean-centred matrix is
    # all-zero (every singular value 0), so there is *no* behavioural variance. Report intrinsic
    # dimensionality 0 rather than letting searchsorted(all-zeros, 0.90) return len(cum)+1 — which
    # would (wrongly) exceed the number of modes actually computed (panel R272).
    nmodes = kk if k is None else min(k, kk)
    frac = energy / tot if tot > 0 else np.zeros_like(energy)
    cum = np.cumsum(frac)
    if tot <= 0 or not len(cum):
        k90 = 0
    else:
        # clamp to the number of modes actually reported (defense-in-depth: even a direct
        # decompose(k=1) call — unreachable via the public API — keeps k90 <= nmodes; panel R273)
        k90 = min(int(np.searchsorted(cum, 0.90)) + 1, nmodes)
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
    target = set(range(nF))
    covered: set[int] = set()
    chosen: list[str] = []
    remaining = list(range(nT))
    while covered != target:
        best = max(remaining, key=lambda i: len(rowsets[i] - covered))
        gain = len(rowsets[best] - covered)
        if gain == 0:
            break
        covered |= rowsets[best]
        chosen.append(tests[best])
        remaining.remove(best)

    # --- redundancy: tests with an identical activation profile. Counted by grouping identical
    # function-sets (O(n_tests)) rather than a dense n×n similarity matrix — the latter is
    # unbounded by the min-dim cap and OOMs on a big-suite / few-function artifact (panel R271). ---
    groups: collections.Counter[frozenset[str]] = collections.Counter(
        frozenset(cov[t]) for t in tests)
    redundant_pairs = sum(c * (c - 1) // 2 for c in groups.values() if c > 1)

    payload = {
        "modes": modes,
        "intrinsic_dimensionality": k90,
        "minimal_test_set": chosen,   # a genuine cover of every executed function (see count)
        "minimal_test_count": len(chosen),
        "redundant_test_pairs": redundant_pairs,
    }
    meta = {"tests": nT, "functions": nF, "density": round(density, 4), "solver": solver}
    return payload, meta


def _pod(cov: dict[str, list[str]], k: int | None = None) -> Any:
    """Shared POD builder for `feature_map` / `outlier_tests`. Returns
    (tests, funcs, U, S, Vt, kk, density, solver, row_energy) or raises RuntimeError (numpy missing /
    matrix too large without scipy / SVD non-convergence). Mirrors `decompose`'s matrix build + mean-
    centred SVD — kept separate so the gated `decompose` stays untouched. `row_energy` is per-test
    total variance (centred on the dense path; uncentred on the scipy path — flagged by `solver`)."""
    if not HAS_NUMPY:
        raise RuntimeError("behavioural-mode analysis needs numpy")
    tests = sorted(cov)
    funcs = sorted({f for fs in cov.values() for f in fs})
    fidx = {f: i for i, f in enumerate(funcs)}
    nT, nF = len(tests), len(funcs)
    smaller = min(nT, nF)
    if not HAS_SCIPY and smaller > _DENSE_CAP:
        raise RuntimeError(
            f"co-activation matrix is {nT}x{nF} (min dim {smaller} > {_DENSE_CAP}); install the "
            "'spectral' extra (pip install 'stitchgraph[spectral]') for the sparse SVD that scales")
    M = np.zeros((nT, nF), dtype=float)
    for ti, tid in enumerate(tests):
        for f in cov[tid]:
            M[ti, fidx[f]] = 1.0
    density = float(M.mean())
    kk = min(smaller - 1, 16 if k is None else max(2, min(k, smaller - 1)))
    Mc = M - M.mean(axis=0, keepdims=True)
    try:
        if HAS_SCIPY and smaller > _DENSE_CAP:
            A = csr_matrix(M)
            v0 = np.ones(min(A.shape)) / math.sqrt(min(A.shape))
            u, s, vt = svds(A, k=kk, v0=v0)
            order = np.argsort(-s)
            S, Vt, U = s[order], vt[order], u[:, order]
            solver = "scipy"
            row_energy = (M ** 2).sum(axis=1)          # uncentred (svds path)
        else:
            U, S, Vt = np.linalg.svd(Mc, full_matrices=False)
            U, S, Vt = U[:, :kk], S[:kk], Vt[:kk]
            solver = "numpy-dense"
            row_energy = (Mc ** 2).sum(axis=1)         # centred
    except np.linalg.LinAlgError as exc:
        raise RuntimeError(f"SVD did not converge on the {nT}x{nF} co-activation matrix: {exc}") from exc
    return tests, funcs, U, S, Vt, kk, density, solver, row_energy


def feature_map(store: Store, coverage_path: str, k: int | None = None,
                top_funcs: int = 10, top_tests: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per behavioural mode, the feature it represents: its top-loading **functions** (full ids — the
    feature's implementation), the **files** they span, and the **tests** that most express it. The
    actionable, full-id view of `find_modes`' modes. Raises like `decompose`. Advisory, read-only."""
    cov = load_coverage(coverage_path)
    funcs_all = {f for fs in cov.values() for f in fs}
    if len(cov) < 4 or len(funcs_all) < 4:
        return ([], {"tests": len(cov), "functions": len(funcs_all), "solver": "none",
                     "reason": "need at least 4 tests and 4 executed functions"})
    tests, funcs, U, S, Vt, kk, density, solver, _ = _pod(cov, k)
    energy = S ** 2
    tot = float(energy.sum())
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
    smoke* test (distinguished by a high load on mode 1, the 'always-on' axis). Raises like `decompose`.
    Advisory, read-only."""
    cov = load_coverage(coverage_path)
    funcs_all = {f for fs in cov.values() for f in fs}
    if len(cov) < 4 or len(funcs_all) < 4:
        return ([], {"tests": len(cov), "functions": len(funcs_all), "solver": "none",
                     "reason": "need at least 4 tests and 4 executed functions"})
    tests, funcs, U, S, Vt, kk, density, solver, row_energy = _pod(cov, k)
    captured = ((U[:, :kk] * S[:kk]) ** 2).sum(axis=1)
    mode1 = np.abs(U[:, 0]) if U.shape[1] else np.zeros(len(tests))
    m1_hi = float(np.quantile(mode1, 0.90)) if len(mode1) else 0.0
    rows: list[dict[str, Any]] = []
    for i, tid in enumerate(tests):
        tot_i = float(row_energy[i])
        resid = 0.0 if tot_i <= 0 else max(0.0, 1.0 - float(captured[i]) / tot_i)
        kind = "typical"
        if resid >= 0.5:
            kind = "smoke" if mode1[i] >= m1_hi else "unique"
        rows.append({"test": tid, "residual": round(resid, 4),
                     "mode1_load": round(float(mode1[i]), 4), "kind": kind})
    rows.sort(key=lambda r: (-r["residual"], r["test"]))
    meta = {"tests": len(tests), "functions": len(funcs), "density": round(density, 4),
            "solver": solver}
    return rows[:limit], meta
