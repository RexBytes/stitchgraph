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


def test_self_similarity_is_one():
    fp = _fps(CLONES)["sum_even_squares"]
    assert abs(structure.similarity(fp, fp) - 1.0) < 1e-9


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
