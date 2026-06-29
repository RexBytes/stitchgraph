"""Fixtures for the body-matrix clone detector.

sum_even_squares and accumulate_even are SEMANTIC CLONES: identical logic, different variable
names, and ZERO helper calls. The call-fingerprint detector (experiment.py) needs >=3 callees,
so it is structurally BLIND to this pair. The body matrix should rank them an exact clone
(identical normalised AST). split_csv is unrelated and should score low against the pair.
"""


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
