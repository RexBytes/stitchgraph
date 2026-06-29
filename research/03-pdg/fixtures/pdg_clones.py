"""Fixtures for the PDG (program-dependence-graph) body matrix.

`collect_direct` and `collect_tmp` are a Type-3 clone pair: same computation, but the second
factors the condition and the result through TEMP VARIABLES (`flag`, `ident`) and splits the
attribute reads onto their own lines. That changes the *statement order and token sequence*
enough that the experiment-02 normalised-AST SequenceMatcher drops — yet the data/control
DEPENDENCE structure (r.active gates the body; r.id flows into append) is the same. The PDG
fingerprint, being order- and temp-var-tolerant, should rate them more similar than the AST
token sequence does.

`scale_list` is unrelated and should score low against the pair.
"""


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


def scale_list(values, factor):
    scaled = []
    for v in values:
        scaled.append(v * factor)
    total = sum(scaled)
    return total


# --- order-invariance pair: two INDEPENDENT computations, interleaved differently ---
# interleave_a / interleave_b compute the same (total, names) but emit the two independent
# blocks in a different ORDER. The AST token *sequence* therefore diverges (the block moves),
# yet the dependence graph is identical -> the PDG fingerprint should stay ~1.0 while the
# experiment-02 AST-token ratio drops. This is the clean case PDG wins by construction.

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
