"""Unit tests for the structural (body-level) fingerprint (core/structure.py).

The behaviour was validated in research/04-expr-dfg; these pin the contract in src: structural
clones (renamed locals, reordered independent statements, temp-variable factoring) score high,
unrelated functions score low, and the fingerprinter never raises on odd input. Stdlib-only, so
this runs in the core (no-extras) CI job.
"""
from __future__ import annotations

import collections

from stitchgraph.core import structure


def _fps(src: str) -> dict:
    return structure.fingerprint_source(src)


def _sim(src_a: str, name_a: str, src_b: str, name_b: str) -> float:
    a, b = _fps(src_a)[name_a], _fps(src_b)[name_b]
    return structure.similarity(a, b)


# --- the validated clone cases (from research/04-expr-dfg) -------------------------

CLONES = '''
def sum_even_squares(items):
    total = 0
    for x in items:
        if x % 2 == 0:
            total = total + x * x
    return total

def accumulate_even(data):
    acc = 0
    for v in data:
        if v % 2 == 0:
            acc = acc + v * v
    return acc

def split_csv(line):
    fields = line.split(",")
    cleaned = []
    for f in fields:
        cleaned.append(f.strip())
    return cleaned
'''

TEMPVAR = '''
def collect_direct(data):
    result = []
    for item in data:
        if item.active:
            result.append(item.id)
    return result

def collect_tmp(rows):
    out = []
    for r in rows:
        flag = r.active
        if flag:
            ident = r.id
            out.append(ident)
    return out
'''

REORDER = '''
def interleave_a(data):
    nums = [x.n for x in data]
    total = 0
    for v in nums:
        total = total + v
    names = sorted(d.name for d in data)
    return total, names

def interleave_b(data):
    names = sorted(d.name for d in data)
    nums = [x.n for x in data]
    total = 0
    for v in nums:
        total = total + v
    return total, names
'''


def test_renamed_locals_are_an_exact_clone():
    assert _sim(CLONES, "sum_even_squares", CLONES, "accumulate_even") >= 0.99


def test_unrelated_function_scores_low():
    sim = _sim(CLONES, "sum_even_squares", CLONES, "split_csv")
    assert sim < 0.5


def test_tempvar_factoring_recovered_by_copy_propagation():
    # collect_tmp introduces temp vars (flag, ident) the statement-PDG would penalise; copy
    # propagation folds them so this is a near-exact clone (research/04: 1.00).
    assert _sim(TEMPVAR, "collect_direct", TEMPVAR, "collect_tmp") >= 0.95


def test_reordered_independent_blocks_are_order_invariant():
    assert _sim(REORDER, "interleave_a", REORDER, "interleave_b") >= 0.99


def test_augassign_equals_explicit_rebind():
    # R162 (opus, MEDIUM): `x += e` semantically reads x, so its value-flow graph must match
    # `x = x + e`. A bare Name target has ctx=Store; the walker used to ev() it (Name gated on
    # Load -> None) and silently drop the read edge, making the accumulator idiom diverge (0.50).
    aug = ("def f(items):\n    total = 0\n    for item in items:\n"
           "        total += item.cost\n    return total\n")
    explicit = ("def f(items):\n    total = 0\n    for item in items:\n"
                "        total = total + item.cost\n    return total\n")
    assert _sim(aug, "f", explicit, "f") >= 0.99


def test_augassign_attribute_and_subscript_targets_match_explicit():
    # The Attribute/Subscript target paths were already correct (their ev branches aren't
    # ctx-gated); pin them so the Name fix doesn't regress the consistency it restores.
    assert _sim("def f(o):\n    o.x += 1\n", "f", "def f(o):\n    o.x = o.x + 1\n", "f") >= 0.99
    assert _sim("def f(d, k):\n    d[k] += 1\n", "f",
                "def f(d, k):\n    d[k] = d[k] + 1\n", "f") >= 0.99


def test_augassign_read_edge_carries_signal():
    # The read-edge fix must not over-merge into a no-op. An accumulator that READS itself
    # (`total += x`, a BINOP folding in the prior total) is a different value-flow shape from one
    # that OVERWRITES (`total = x`, no read of total). The restored read edge is what separates
    # them; without it both would collapse. This pins that the edge actually carries signal.
    accumulate = ("def f(xs):\n    total = 0\n    for x in xs:\n        total += x\n    return total\n")
    overwrite = ("def f(xs):\n    total = 0\n    for x in xs:\n        total = x\n    return total\n")
    assert _sim(accumulate, "f", overwrite, "f") < 1.0


def test_self_similarity_is_one():
    fp = _fps(CLONES)["sum_even_squares"]
    assert abs(structure.similarity(fp, fp) - 1.0) < 1e-9


def test_similarity_never_exceeds_one():
    # R153 (opus, LOW): float rounding could push a cosine to 1.0000000002; clamp guarantees <= 1.0.
    for name in ("sum_even_squares", "accumulate_even", "split_csv"):
        fp = _fps(CLONES)[name]
        assert structure.similarity(fp, fp) <= 1.0


def test_operator_distinguishes_add_from_subtract():
    # R153 (opus, MEDIUM): a deposit (+) and a withdraw (-) must NOT be structural clones.
    src = '''
def deposit(bal, amt):
    bal = bal + amt
    return bal

def withdraw(bal, amt):
    bal = bal - amt
    return bal
'''
    assert _sim(src, "deposit", src, "withdraw") < 0.95


def test_operator_distinguishes_lt_from_gt():
    src = '''
def below(a, b):
    if a < b:
        return a
    return b

def above(a, b):
    if a > b:
        return a
    return b
'''
    assert _sim(src, "below", src, "above") < 0.95


# --- robustness / contract --------------------------------------------------------

def test_syntax_error_returns_empty_not_raises():
    assert structure.fingerprint_source("def oops(:\n  pass") == {}


def test_empty_module_returns_empty():
    assert structure.fingerprint_source("") == {}


def test_methods_and_nested_keyed_by_qualname():
    src = '''
class Service:
    def handle(self, req):
        return self.process(req)

    def process(self, req):
        def inner(x):
            return x + 1
        return inner(req)
'''
    fps = _fps(src)
    assert "Service.handle" in fps
    assert "Service.process" in fps
    assert "Service.process.inner" in fps


def test_similarity_empty_fingerprints_is_zero():
    empty: collections.Counter[str] = collections.Counter()
    nonempty = _fps(CLONES)["sum_even_squares"]
    assert structure.similarity(empty, nonempty) == 0.0
    assert structure.similarity(empty, empty) == 0.0


def test_fingerprint_trivial_function_does_not_crash():
    src = "def f():\n    pass\n"
    fp = _fps(src)["f"]
    assert isinstance(fp, collections.Counter)


def test_match_statement_contributes_to_fingerprint():
    # R158 (opus): match/case bodies must be walked — two functions with different case bodies
    # must NOT fingerprint identically.
    a = '''
def handle(cmd):
    match cmd:
        case "add":
            return register(cmd)
        case "del":
            return remove(cmd)
        case _:
            return None
'''
    b = '''
def handle(cmd):
    match cmd:
        case "add":
            return 0
        case "del":
            return 0
        case _:
            return 0
'''
    fa = structure.fingerprint_source(a)["handle"]
    fb = structure.fingerprint_source(b)["handle"]
    assert fa  # non-empty: the match body contributes value-flow nodes
    assert structure.similarity(fa, fb) < 0.95  # different case bodies are not identical


def test_deep_but_valid_expression_does_not_raise():
    # R156 (opus CRITICAL): a long `a + a + ...` chain parses fine but overflows the recursive
    # value-flow walk. fingerprint_source must degrade (return a dict), never raise RecursionError.
    deep = "def f():\n    return " + " + ".join(["a"] * 4000) + "\n"
    fps = structure.fingerprint_source(deep)
    assert isinstance(fps, dict)  # no traceback escaped


def test_control_flow_nested_defs_are_fingerprinted():
    # R155 (opus): defs inside if/try/for/with must be fingerprinted (qualname has no control-flow
    # level, matching the extractor), else they're invisible to find_similar/graph_diff.
    src = '''
import typing
def f(c):
    if typing.TYPE_CHECKING:
        def g(x):
            return x + 1
    try:
        def h(y):
            return y * 2
    except Exception:
        pass
    for _ in range(c):
        def k(z):
            return z - 3
'''
    fps = _fps(src)
    assert "f.g" in fps
    assert "f.h" in fps
    assert "f.k" in fps


def test_trystar_body_not_dropped():
    # R153 (sonnet N1): except* (PEP 654) must be walked like try/except, not silently skipped.
    src = '''
def with_star(items):
    try:
        for x in items:
            handle(x)
    except* ValueError:
        cleanup()
'''
    fp = _fps(src)["with_star"]
    # the try body (the for-loop + calls) must contribute, so the fingerprint is non-trivial
    assert sum(fp.values()) > 5


def test_async_function_supported():
    src = '''
async def fetch_a(client):
    data = await client.get()
    return data.json()

async def fetch_b(conn):
    resp = await conn.get()
    return resp.json()
'''
    assert _sim(src, "fetch_a", src, "fetch_b") >= 0.99
