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
from collections.abc import Callable, Sequence

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


def find_similar(store: Store, snippet: str, limit: int = 10) -> list[tuple[str, float]]:
    """Return [(node_id, score)] most similar to the snippet, best first.

    Uses the dense embedder if one is registered (or model2vec auto-loads), else
    falls back to token cosine — identical interface, better ranking with a model.
    """
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
