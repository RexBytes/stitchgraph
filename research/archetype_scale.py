#!/usr/bin/env python3
"""Research spike (IDEAS.md §2, SCALE-UP): does the archetype signal hold on a bigger,
more varied corpus — and can stitchgraph's cross-language BOUNDARY signals (routes / SQL /
events / ORM) sharpen the name fingerprint?

Extends `archetype_fingerprint.py` (which established, n=10, that TOPOLOGY tracks language while
NAMES_TFIDF tracks archetype language-invariantly at ~6/10). Here we:

  1. roughly DOUBLE the corpus (more archetypes × py/js), to see whether NAMES_TFIDF's edge over
     TOPOLOGY is stable or a small-n artefact;
  2. add a BOUNDARY fingerprint built from what stitchgraph *uniquely* extracts — Route nodes,
     SQL READS/WRITES, Event EMITS/HANDLES, ORM MAPS_TO, plus the kind mix — and a COMBINED
     (NAMES_TFIDF ⊕ scaled BOUNDARY) to test IDEAS §2's "augment with boundary signals" idea.

Fingerprints compared, each by (a) same-archetype-cross-language vs same-language-cross-archetype
mean cosine, and (b) leave-one-out nearest-neighbour archetype accuracy:
  TOPOLOGY · NAMES_TFIDF · BOUNDARY · COMBINED.

Idempotent + robust: reuses the cached _corpus, downloads only what's missing, and analyses
whatever actually indexed (a registry hiccup just shrinks n, never crashes).

Run:  PYTHONPATH=src python research/archetype_scale.py
Exploratory research, NOT part of the stitchgraph package.
"""
from __future__ import annotations

import collections
import itertools
import math
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import stitchgraph as sg
from stitchgraph.core.model import NodeKind, Relation
from stitchgraph.core.reach import fan_in, fan_out

# archetype -> {package: language}. The first 5 archetypes are the original n=10 spike corpus
# (cached); the rest are the scale-up. py = pip sdist, js = npm pack.
CORPUS = {
    "click": ("cli", "py"), "commander": ("cli", "js"),
    "flask": ("web", "py"), "express": ("web", "js"),
    "requests": ("http", "py"), "axios": ("http", "js"),
    "jinja2": ("template", "py"), "handlebars": ("template", "js"),
    "arrow": ("date", "py"), "dayjs": ("date", "js"),
    # --- scale-up ---
    "marshmallow": ("validation", "py"), "yup": ("validation", "js"),
    "loguru": ("logging", "py"), "winston": ("logging", "js"),
    "markdown": ("markdown", "py"), "marked": ("markdown", "js"),
    "peewee": ("orm", "py"), "sequelize": ("orm", "js"),
    "redis": ("cache", "py"), "ioredis": ("cache", "js"),
    "pygments": ("lexer", "py"), "prismjs": ("lexer", "js"),
}
ROOT = Path(__file__).resolve().parent / "_corpus"


def fetch() -> dict[str, Path]:
    dl, src = ROOT / "dl", ROOT / "src"
    dl.mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)
    for name, (_arch, lang) in CORPUS.items():
        if any(src.glob(f"{name}*")):
            continue
        if lang == "py":
            subprocess.run([sys.executable, "-m", "pip", "download", "--no-binary", ":all:",
                            "--no-deps", "-d", str(dl), name], capture_output=True)
        else:
            subprocess.run(["npm", "pack", name], cwd=dl, capture_output=True)
    for arc in list(dl.glob("*.tar.gz")) + list(dl.glob("*.tgz")):
        stem = arc.name.rsplit(".tar.gz", 1)[0].rsplit(".tgz", 1)[0]
        out = src / stem
        if out.exists():
            continue
        out.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(arc) as t:
                for m in t.getmembers():
                    parts = Path(m.name).parts
                    if len(parts) > 1:
                        m.name = str(Path(*parts[1:]))
                        t.extract(m, out)  # noqa: S202 - trusted registry tarball
        except (tarfile.TarError, OSError):
            continue
    dirs = {}
    for name in CORPUS:
        hit = next((d for d in src.glob(f"{name}*") if d.is_dir()), None)
        if hit:
            dirs[name] = hit
    return dirs


def _toks(name: str) -> list[str]:
    out: list[str] = []
    for p in re.split(r"[._\-]", name):
        out += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", p)
    return [t.lower() for t in out if len(t) > 1]


def _gini(xs: list[float]) -> float:
    xs = sorted(x for x in xs if x >= 0)
    if not xs or sum(xs) == 0:
        return 0.0
    cum = list(itertools.accumulate(xs))
    n = len(xs)
    return (n + 1 - 2 * sum(cum) / cum[-1]) / n


def features(path: Path):
    """Return (topology-dict, name-token-Counter, boundary-dict) for one indexed package."""
    with sg.Store(":memory:") as store:
        sg.reindex(store, str(path))
        nodes = store.all_nodes_full()
        fi, fo = fan_in(store), fan_out(store)
        names: collections.Counter[str] = collections.Counter()
        for n in nodes:
            for t in _toks(n.name):
                names[t] += 1
        n_nodes = max(1, len(nodes))
        kinds = collections.Counter(n.kind.value for n in nodes)
        fos = [fo.get(n.id, 0) for n in nodes]
        fis = [fi.get(n.id, 0) for n in nodes]
        n_edges = sum(fos)
        topo = {
            "edge_density": n_edges / n_nodes,
            "mean_fanout": sum(fos) / n_nodes,
            "max_fanout_share": (max(fos) / n_edges) if n_edges else 0.0,
            "leaf_frac": sum(1 for x in fos if x == 0) / n_nodes,
            "root_frac": sum(1 for x in fis if x == 0) / n_nodes,
            "fanin_gini": _gini(fis),
            "fanout_gini": _gini(fos),
            "class_frac": kinds.get("Class", 0) / n_nodes,
            "method_frac": kinds.get("Method", 0) / n_nodes,
            "func_frac": kinds.get("Function", 0) / n_nodes,
        }
        # BOUNDARY: what stitchgraph uniquely extracts — cross-language semantic signals, per-node
        # normalised so a big and a small package with the same *shape* read alike.
        rel_counts: collections.Counter[str] = collections.Counter()
        for e in store.resolved_edges():
            rel_counts[e.relation.value] += 1
        route_nodes = sum(1 for n in nodes if n.kind == NodeKind.ROUTE) \
            if hasattr(NodeKind, "ROUTE") else 0
        boundary = {
            "route_frac": route_nodes / n_nodes,
            "sql_frac": (rel_counts.get(Relation.READS.value, 0)
                         + rel_counts.get(Relation.WRITES.value, 0)) / n_nodes,
            "event_frac": (rel_counts.get(Relation.EMITS.value, 0)
                           + rel_counts.get(Relation.HANDLES.value, 0)) / n_nodes,
            "maps_to_frac": rel_counts.get(getattr(Relation, "MAPS_TO", Relation.REFERENCES).value, 0)
            / n_nodes if hasattr(Relation, "MAPS_TO") else 0.0,
            "inherits_frac": rel_counts.get(Relation.INHERITS.value, 0) / n_nodes,
            "class_frac": kinds.get("Class", 0) / n_nodes,
            "method_frac": kinds.get("Method", 0) / n_nodes,
            "func_frac": kinds.get("Function", 0) / n_nodes,
        }
        return topo, names, boundary


def _cosine(a: collections.Counter, b: collections.Counter) -> float:
    keys = set(a) | set(b)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if not keys or da == 0 or db == 0:
        return 0.0
    return sum(a.get(k, 0) * b.get(k, 0) for k in keys) / (da * db)


def _zscorer(dicts, feats):
    mean = {f: sum(d[f] for d in dicts) / len(dicts) for f in feats}
    std = {f: (sum((d[f] - mean[f]) ** 2 for d in dicts) / len(dicts)) ** 0.5 or 1.0 for f in feats}
    return lambda t: collections.Counter({f: (t[f] - mean[f]) / std[f] for f in feats})


def main() -> int:
    dirs = fetch()
    data = {}
    for name, path in dirs.items():
        arch, lang = CORPUS[name]
        topo, names, boundary = features(path)
        data[name] = (arch, lang, topo, names, boundary)
        print(f"indexed {name:12} ({arch}/{lang})")
    names_l = list(data)
    n = len(names_l)
    archs = {data[k][0] for k in names_l}
    print(f"\ncorpus: {n} packages, {len(archs)} archetypes, "
          f"{sum(1 for k in names_l if data[k][1] == 'py')}py/"
          f"{sum(1 for k in names_l if data[k][1] == 'js')}js")

    topo_feats = list(next(iter(data.values()))[2])
    bnd_feats = list(next(iter(data.values()))[4])
    z_topo = _zscorer([d[2] for d in data.values()], topo_feats)
    z_bnd = _zscorer([d[4] for d in data.values()], bnd_feats)

    df: collections.Counter[str] = collections.Counter()
    for _, _, _, nm, _ in data.values():
        df.update(set(nm))
    idf = lambda t: math.log((n + 1) / (df[t] + 1)) + 1
    tfidf = lambda nm: collections.Counter({t: (1 + math.log(c)) * idf(t) for t, c in nm.items()})

    def _l2norm(c: collections.Counter) -> collections.Counter:
        d = math.sqrt(sum(v * v for v in c.values())) or 1.0
        return collections.Counter({k: v / d for k, v in c.items()})

    def combined(nm, bnd, w_bnd=1.0):
        # L2-normalise names and boundary separately, then concatenate (namespaced keys) so
        # neither dominates by raw magnitude; boundary weighted by w_bnd.
        c = collections.Counter({f"n::{k}": v for k, v in _l2norm(tfidf(nm)).items()})
        for k, v in _l2norm(z_bnd(bnd)).items():
            c[f"b::{k}"] = v * w_bnd
        return c

    fps = {
        "TOPOLOGY": {k: z_topo(v[2]) for k, v in data.items()},
        "NAMES_TFIDF": {k: tfidf(v[3]) for k, v in data.items()},
        "BOUNDARY": {k: z_bnd(v[4]) for k, v in data.items()},
        "COMBINED(names+boundary)": {k: combined(v[3], v[4], 0.6) for k, v in data.items()},
    }
    for label, vecs in fps.items():
        sim = lambda a, b: _cosine(vecs[a], vecs[b])
        sa, sl, cr = [], [], []
        for a, b in itertools.combinations(names_l, 2):
            s = sim(a, b)
            (sa if data[a][0] == data[b][0] and data[a][1] != data[b][1]
             else sl if data[a][1] == data[b][1] else cr).append(s)
        correct = sum(
            data[max((b for b in names_l if b != a), key=lambda b: sim(a, b))][0] == data[a][0]
            for a in names_l)
        m = lambda xs: sum(xs) / len(xs) if xs else 0.0
        print(f"\n=== {label} ===")
        print(f"  same-archetype (cross-language): {m(sa):+.3f}")
        print(f"  same-language  (cross-archetype): {m(sl):+.3f}")
        print(f"  cross-everything (baseline):      {m(cr):+.3f}")
        print(f"  nearest-neighbour archetype accuracy: {correct}/{n}  (chance ~= 1/{len(archs) - 1})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
