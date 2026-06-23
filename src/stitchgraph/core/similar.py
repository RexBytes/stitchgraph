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

from .model import NodeKind, Relation
from .store import Store

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
    """Return [(node_id, score)] most similar to the snippet, best first."""
    query = _vector(tokenise(snippet))
    if not query:
        return []
    # Precompute callee name lists per source node (cheap join).
    callees: dict[str, list[str]] = {}
    for edge in store.resolved_edges(Relation.CALLS):
        if edge.dst_id:
            callees.setdefault(edge.src, []).append(edge.dst_symbol)

    scored: list[tuple[str, float]] = []
    for node in store.all_nodes_full():
        if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
            continue
        s = _cosine(query, _vector(_node_tokens(store, node, callees)))
        if s > 0:
            scored.append((node.id, s))
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return scored[:limit]
