#!/usr/bin/env python3
"""Research spike (IDEAS.md §2): does a codebase's stitchgraph graph reveal *what
the application is* — and does that signal survive across languages?

Builds a small multi-language corpus from PyPI (sdists) + npm (tarballs), indexes
each package with stitchgraph, and compares three fingerprints:

  - TOPOLOGY   : language-agnostic structural metrics (degree distribution, hub
                 concentration, kind mix). Hypothesis from IDEAS.md §2.
  - NAMES_RAW  : bag of identifier tokens (cosine).
  - NAMES_TFIDF: identifier tokens with language-generic vocabulary down-weighted.

Finding (see research/README.md): TOPOLOGY tracks *language/extractor*, not
application archetype (0/10 nearest-neighbour archetype accuracy). NAMES_TFIDF
captures archetype and is *language-invariant* (~6/10). The viable path to
"identify what the package does" is semantic-name, not topology — and is exactly
what stitchgraph's pluggable `find_similar` embedder could do better than TF-IDF.

Run:  PYTHONPATH=src python research/archetype_fingerprint.py
Needs: stitchgraph[all,dev] installed; network access to pypi + npm registries.
This is exploratory research, NOT part of the stitchgraph package.
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
from stitchgraph.core.model import Relation
from stitchgraph.core.reach import fan_in, fan_out

# archetype -> {language: (package, distribution-stem)}. Distribution stems are how
# pip/npm name the extracted dir; adjust if registry versions move.
CORPUS = {
    "click": ("cli", "py"), "commander": ("cli", "js"),
    "flask": ("web", "py"), "express": ("web", "js"),
    "requests": ("http", "py"), "axios": ("http", "js"),
    "jinja2": ("template", "py"), "handlebars": ("template", "js"),
    "arrow": ("date", "py"), "dayjs": ("date", "js"),
}
ROOT = Path(__file__).resolve().parent / "_corpus"


def fetch() -> dict[str, Path]:
    """Download + extract each package; return name -> source dir. Idempotent."""
    dl, src = ROOT / "dl", ROOT / "src"
    dl.mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)
    for name, (_arch, lang) in CORPUS.items():
        if any(src.glob(f"{name}*")):
            continue
        if lang == "py":
            subprocess.run([sys.executable, "-m", "pip", "download", "--no-binary",
                            ":all:", "--no-deps", "-d", str(dl), name],
                           capture_output=True)
        else:
            subprocess.run(["npm", "pack", name], cwd=dl, capture_output=True)
    for arc in list(dl.glob("*.tar.gz")) + list(dl.glob("*.tgz")):
        stem = arc.name.rsplit(".tar.gz", 1)[0].rsplit(".tgz", 1)[0]
        out = src / stem
        if out.exists():
            continue
        out.mkdir(parents=True, exist_ok=True)
        with tarfile.open(arc) as t:
            for m in t.getmembers():
                parts = Path(m.name).parts
                if len(parts) > 1:  # strip the leading package/ dir
                    m.name = str(Path(*parts[1:]))
                    t.extract(m, out)  # noqa: S202 - trusted registry tarball
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
    """Return (topology-dict, name-token-Counter) for one indexed package."""
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
        return topo, names


def _cosine(a: collections.Counter, b: collections.Counter) -> float:
    keys = set(a) | set(b)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if not keys or da == 0 or db == 0:
        return 0.0
    return sum(a.get(k, 0) * b.get(k, 0) for k in keys) / (da * db)


def main() -> int:
    dirs = fetch()
    data = {}
    for name, path in dirs.items():
        arch, lang = CORPUS[name]
        topo, names = features(path)
        data[name] = (arch, lang, topo, names)
        print(f"indexed {name:11} ({arch}/{lang})")

    feats = list(next(iter(data.values()))[2])
    mean = {f: sum(d[2][f] for d in data.values()) / len(data) for f in feats}
    std = {f: (sum((d[2][f] - mean[f]) ** 2 for d in data.values()) / len(data)) ** 0.5
           or 1.0 for f in feats}
    zvec = lambda t: collections.Counter({f: (t[f] - mean[f]) / std[f] for f in feats})

    df: collections.Counter[str] = collections.Counter()
    for _, _, _, nm in data.values():
        df.update(set(nm))
    n_docs = len(data)
    idf = lambda t: math.log((n_docs + 1) / (df[t] + 1)) + 1
    tfidf = lambda nm: collections.Counter(
        {t: (1 + math.log(c)) * idf(t) for t, c in nm.items()})

    fps = {
        "TOPOLOGY": {k: zvec(v[2]) for k, v in data.items()},
        "NAMES_RAW": {k: v[3] for k, v in data.items()},
        "NAMES_TFIDF": {k: tfidf(v[3]) for k, v in data.items()},
    }
    names = list(data)
    for label, vecs in fps.items():
        sim = lambda a, b: _cosine(vecs[a], vecs[b])
        sa, sl, cr = [], [], []
        for a, b in itertools.combinations(names, 2):
            s = sim(a, b)
            (sa if data[a][0] == data[b][0] and data[a][1] != data[b][1]
             else sl if data[a][1] == data[b][1] else cr).append(s)
        correct = sum(
            data[max((b for b in names if b != a), key=lambda b: sim(a, b))][0]
            == data[a][0] for a in names)
        m = lambda xs: sum(xs) / len(xs) if xs else 0.0
        print(f"\n=== {label} ===")
        print(f"  same-archetype (cross-language): {m(sa):+.3f}")
        print(f"  same-language  (cross-archetype): {m(sl):+.3f}")
        print(f"  cross-everything (baseline):      {m(cr):+.3f}")
        print(f"  nearest-neighbour archetype accuracy: {correct}/{len(names)}"
              f"  (chance ~= 1/{len(names) - 1})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
