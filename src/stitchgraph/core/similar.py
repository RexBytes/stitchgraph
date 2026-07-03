"""Semantic-ish retrieval — 'where's the code that does X' (design §1, find_similar).

A self-contained, dependency-free backend: each node is featurised into a bag of
tokens from its name, docstring, and the names of what it calls; a query snippet
is featurised the same way; ranking is TF cosine similarity.

This is the structural/lexical proxy. A real local code-embedding model
(sentence-transformers / a GGUF model) + an ANN index (sqlite-vec, hnswlib) is a
drop-in behind the same `featurise` / `score` contract — that's the M4 upgrade.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from functools import partial
from pathlib import Path

from . import (
    structure,
    structure_bash,
    structure_cpp,
    structure_csharp,
    structure_go,
    structure_java,
    structure_js,
    structure_php,
    structure_ruby,
    structure_rust,
)
from .model import NodeKind, Relation
from .store import Store

# Optional dense-embedding backend. Inject any `texts -> vectors` callable
# (sentence-transformers, model2vec, an API) and find_similar switches to cosine
# over real embeddings. Unset -> the token-similarity default below.
_EMBEDDER: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None


def set_embedder(fn: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None) -> None:
    """Register (or clear) the dense embedding backend used by find_similar."""
    global _EMBEDDER
    _EMBEDDER = fn


_M2V_TRIED = False


def _try_model2vec() -> bool:
    """Best-effort: wire model2vec as the embedder if installed + a model loads.
    Attempted at most once (loading can hit the network)."""
    global _M2V_TRIED
    if _EMBEDDER is not None:
        return True
    if _M2V_TRIED:
        return False
    _M2V_TRIED = True
    try:
        from model2vec import StaticModel

        from .config import load_config
        model = load_config().embed_model or "minishlab/potion-base-8M"
        m = StaticModel.from_pretrained(model)
        set_embedder(lambda texts: m.encode(list(texts)).tolist())
        return True
    except Exception:  # noqa: BLE001 — no model / offline -> token fallback
        return False

# Tokens too generic to carry signal.
_STOP = {"self", "cls", "return", "def", "class", "the", "a", "an", "of", "to",
         "and", "or", "if", "is", "in", "for", "with", "value", "result", "none"}


def tokenise(text: str) -> list[str]:
    """Split identifiers (snake_case / camelCase) and words into lowercase tokens."""
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        for piece in raw.split("_"):
            for sub in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", piece):
                t = sub.lower()
                if len(t) > 1 and t not in _STOP:
                    out.append(t)
    return out


def _vector(tokens: list[str]) -> Counter[str]:
    return Counter(tokens)


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    if dot == 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def _node_tokens(store: Store, node, callees: dict[str, list[str]]) -> list[str]:
    toks = tokenise(node.name)
    if node.summary:
        toks += tokenise(node.summary)
    toks += [t for callee in callees.get(node.id, []) for t in tokenise(callee)]
    return toks


def find_similar(store: Store, snippet: str, limit: int = 10,
                 mode: str = "semantic") -> list[tuple[str, float]]:
    """Return [(node_id, score)] most similar to the snippet, best first.

    mode="semantic" (default): token/dense similarity over name + docstring + callees — uses the
    dense embedder if one is registered (or model2vec auto-loads), else token cosine.
    mode="structure": body-shape similarity (`structure.py` for Python, `structure_js.py` for
    JS/TS/TSX, `structure_go.py` for Go, `structure_rust.py` for Rust, `structure_cpp.py` for C/C++,
    `structure_java.py` for Java, `structure_csharp.py` for C#, `structure_ruby.py` for Ruby,
    `structure_php.py` for PHP, `structure_bash.py` for Bash) — ranks stored functions by how
    structurally like the snippet's function they are. Advisory; the snippet's language is
    auto-detected and ranked same-language only (the tree-sitter languages need the extra).
    """
    if mode == "structure":
        return find_similar_structure(store, snippet, limit)
    limit = max(0, limit)  # a negative limit must bound to nothing, not slice from the end
    callees: dict[str, list[str]] = {}
    for edge in store.resolved_edges(Relation.CALLS):
        if edge.dst_id:
            callees.setdefault(edge.src, []).append(edge.dst_symbol)
    code = [n for n in store.all_nodes_full()
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS)]
    if not code:
        return []

    if _EMBEDDER is not None or _try_model2vec():
        return _dense(snippet, store, code, callees, limit)

    query = _vector(tokenise(snippet))
    if not query:
        return []
    scored = [(n.id, _cosine(query, _vector(_node_tokens(store, n, callees))))
              for n in code]
    scored = [s for s in scored if s[1] > 0]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored[:limit]


def _dense(snippet, store, code, callees, limit) -> list[tuple[str, float]]:
    embed = _EMBEDDER
    assert embed is not None
    texts = [" ".join(_node_tokens(store, n, callees)) or n.name for n in code]
    vecs = embed([snippet, *texts])
    q = vecs[0]
    scored = [(code[i].id, _dot_cos(q, vecs[i + 1])) for i in range(len(code))]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return [s for s in scored if s[1] > 0][:limit]


def _dot_cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _python_fn_fingerprints(store: Store) -> Iterator[tuple[str, Counter[str]]]:
    """Yield (node_id, structural fingerprint) for every stored Python function/method.

    Node ids are `path::qualname` and node files are relative to the indexed root (stored as
    meta), so we read each Python file once, fingerprint all its functions, and map back by the
    qualname in the id — the same qualname scheme `structure.fingerprint_source` produces. Files
    that moved/can't be read are skipped (advisory, never raises)."""
    root = store.get_meta("root") or "."
    by_path: dict[str, list[tuple[str, str]]] = {}
    for n in store.all_nodes_full():
        if n.kind not in (NodeKind.FUNCTION, NodeKind.METHOD):
            continue
        path, sep, qual = n.id.partition("::")
        if not sep or not path.endswith(".py"):
            continue
        by_path.setdefault(path, []).append((n.id, qual.split("#", 1)[0]))
    for path, items in by_path.items():
        try:
            src = Path(root, path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fps = structure.fingerprint_source(src)
        for node_id, qual in items:
            fp = fps.get(qual)
            if fp is not None:
                yield node_id, fp


def _ts_fn_fingerprints(store: Store, mod) -> Iterator[tuple[str, Counter[str]]]:
    """Yield (node_id, structural fingerprint) for every stored function/method whose file
    extension belongs to `mod` (a tree-sitter structure_* frontend) — the shared body of the
    nine per-language iterators this replaced (review 2026-07-03, D2 stage 4). The grammar is
    chosen per file extension (the JS family needs this — a .ts file fingerprints with the
    TypeScript grammar; single-grammar languages ignore it). Node ids are `path::qualname`
    with files relative to the indexed root, so each file is read once and mapped back by
    qualname. Requires the tree-sitter extra; without it `mod.fingerprint_source` returns {}
    and nothing yields (advisory, never raises)."""
    root = store.get_meta("root") or "."
    by_path: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for n in store.all_nodes_full():
        if n.kind not in (NodeKind.FUNCTION, NodeKind.METHOD):
            continue
        path, sep, qual = n.id.partition("::")
        if not sep:
            continue
        lang = mod._lang_for_ext(Path(path).suffix)
        if lang is None:
            continue
        by_path.setdefault((path, lang), []).append((n.id, qual.split("#", 1)[0]))
    for (path, lang), items in by_path.items():
        try:
            src = Path(root, path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fps = mod.fingerprint_source(src, lang=lang)
        for node_id, qual in items:
            fp = fps.get(qual)
            if fp is not None:
                yield node_id, fp


_js_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_js)
_go_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_go)
_rust_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_rust)
_cpp_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_cpp)
_java_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_java)
_csharp_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_csharp)
_ruby_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_ruby)
_php_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_php)
_bash_fn_fingerprints = partial(_ts_fn_fingerprints, mod=structure_bash)


def find_similar_structure(store: Store, snippet: str,
                           limit: int = 10) -> list[tuple[str, float]]:
    """Rank stored functions/methods by *structural* (body-shape) similarity to the snippet, which
    must be function source. Advisory. The snippet's language is auto-detected — Python first, else
    the JS/TS family, else Go, else Rust, else Java, else C#, else Ruby, else PHP, else Bash, else
    C/C++ — and it is ranked only against stored functions of the SAME language (a body fingerprint's
    topology tracks its extractor, so cross-language scores are not comparable). Some grammars are
    permissive supersets of others, so the auto-detect can mis-sniff one bare snippet for a related
    language: the JS/TS grammar parses a bare Java/C#/C++/PHP `function`/`class` snippet, and the
    C/C++ grammar parses a bare Bash/PHP `name() { ... }` snippet — so Bash and PHP are tried before
    C/C++. This only affects the advisory snippet auto-detect, never the extension-keyed `graph_diff`
    body layer.
    Empty if the snippet has no parseable function or (for the tree-sitter
    languages) the extra is absent. The largest function in the snippet is used as the query."""
    limit = max(0, limit)
    q_fps = structure.fingerprint_source(snippet)
    corpus = _python_fn_fingerprints
    if not q_fps:
        for lang in ("typescript", "tsx", "javascript"):  # TS/TSX parse a superset; try then JS
            q_fps = structure_js.fingerprint_source(snippet, lang=lang)
            if q_fps:
                corpus = _js_fn_fingerprints
                break
    if not q_fps:
        q_fps = structure_go.fingerprint_source(snippet)
        if q_fps:
            corpus = _go_fn_fingerprints
    if not q_fps:
        q_fps = structure_rust.fingerprint_source(snippet)
        if q_fps:
            corpus = _rust_fn_fingerprints
    if not q_fps:
        q_fps = structure_java.fingerprint_source(snippet)
        if q_fps:
            corpus = _java_fn_fingerprints
    if not q_fps:
        q_fps = structure_csharp.fingerprint_source(snippet)
        if q_fps:
            corpus = _csharp_fn_fingerprints
    if not q_fps:
        q_fps = structure_ruby.fingerprint_source(snippet)
        if q_fps:
            corpus = _ruby_fn_fingerprints
    if not q_fps:
        q_fps = structure_php.fingerprint_source(snippet)
        if q_fps:
            corpus = _php_fn_fingerprints
    if not q_fps:
        q_fps = structure_bash.fingerprint_source(snippet)  # before C/C++ (cpp parses `t(){…}` as C)
        if q_fps:
            corpus = _bash_fn_fingerprints
    if not q_fps:
        q_fps = structure_cpp.fingerprint_source(snippet)  # C/C++ last (the cpp grammar is permissive)
        if q_fps:
            corpus = _cpp_fn_fingerprints
    if not q_fps:
        return []
    query = max(q_fps.values(), key=lambda c: sum(c.values()))
    scored = [(nid, structure.similarity(query, fp)) for nid, fp in corpus(store)]
    scored = [s for s in scored if s[1] > 0]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored[:limit]
