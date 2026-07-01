"""White-box differential oracle: the Bash STATEMENT/PDG read-projection vs. the VFG sibling.

The Bash companion to the PHP/Ruby/Java/C#/C++/Rust differential oracles — and the LAST language of
the §5c sweep. The recurring §5c defect class is ONE bug: the PDG's read/write projection
(`collect`/`bind_place` in `structure_bash._build_pdg`) silently diverging from the VFG
(`_build_vfg`), which walks the same tree-sitter CST independently. This oracle cross-checks the two
builders over a generated corpus so the whole class regresses loudly.

Bash is the outlier: **command-oriented** and with NO declared parameter list (shell functions read
positional `$1…` as free variables). So the name-attributable variable cannot be a PARAM — instead we
SEED it with a first assignment `v=$SEED` and detect a read via a data edge from that seed:
  * VFG: `v=$SEED` makes node 0 the FREE node for `SEED` (created first, before any body node) and
    copy-propagates it into `v`; `v` is read iff node 0 acquires an outgoing edge.
  * PDG: ENTRY is node 0 (empty — no params); the seed assignment is node 1; `v` is read iff any
    `(1, _, 'D')` edge exists.

Hard invariant (the exact direction the recurring bugs violated):  VFG-reads(v) ⟹ PDG-reads(v).
The reverse (PDG reads more than the VFG) is allowed — the VFG copy-propagates / discards dead values
(e.g. a prefix `FOO=$v cmd` binds `FOO` without an edge when `FOO` is unused).

Bash-specific hazard this pins: a LITERAL command name (`v` as a bare command) is a free callee, NOT
a variable read, in BOTH builders — while a *dynamic* command name (`$v arg`) reads `v` in both.
"""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_language_pack")

from stitchgraph.core import structure_bash  # noqa: E402


def _pdg_reads_v(body: str) -> bool:
    src = f"f() {{\n  v=$SEED\n{body}\n}}"
    _labels, edges = structure_bash.pdg_source(src)["f"]
    return any(s == 1 and k == "D" for s, _d, k in edges)  # node 1 = the `v=$SEED` seed assignment


def _vfg_reads_v(body: str) -> bool:
    src = f"f() {{\n  v=$SEED\n{body}\n}}"
    _labels, edges = structure_bash.vfg_source(src)["f"]
    return any(s == 0 for s, _d, _k in edges)  # node 0 = SEED's FREE node (created first)


_WRAPPERS = {
    "id": lambda x: x,
    "arith": lambda x: f"$(( {x} + 1 ))",
    "default": lambda x: f"${{{x[1:]}:-0}}",   # ${v:-0}
    "braces": lambda x: f"${{{x[1:]}}}",       # ${v}
    "string": lambda x: f'"val {x} end"',
    "cmdsub": lambda x: f"$(helper {x})",
    "concat": lambda x: f'"pre"{x}',
    "array": lambda x: f"( {x} two )",
}


def _body_from(expr: str) -> str:
    # assign the wrapped expression, then read it back through a copy (threads copy-propagation).
    return f"  r={expr}\n  s=$r\n  echo $s"


@pytest.mark.parametrize("name", sorted(_WRAPPERS))
def test_single_wrapper_read_not_dropped(name):
    expr = _WRAPPERS[name]("$v")
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread $v through {name} (copy-prop/dead-value)")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in wrapper {name!r}: {expr}"


# NB: `default`/`braces` accept only a bare `$v` (they slice the var name) — single-wrapper only.
_COMPOSE = ["arith", "string", "cmdsub", "concat", "array"]


@pytest.mark.parametrize("outer,inner", list(itertools.product(_COMPOSE, _COMPOSE)))
def test_composed_wrappers_read_not_dropped(outer, inner):
    expr = _WRAPPERS[outer](_WRAPPERS[inner]("$v"))
    body = _body_from(expr)
    if not _vfg_reads_v(body):
        pytest.skip(f"VFG does not thread $v through {outer}∘{inner}")
    assert _pdg_reads_v(body), f"PDG DROPPED a read in {outer}∘{inner}: {expr}"


# --- statement-position reads: $v read inside a command / control header --------------------------

@pytest.mark.parametrize("body", [
    "  echo $v",
    "  echo $(( $v + 1 ))",
    "  [[ $v -gt 0 ]]",
    "  if [[ $v ]]; then a; fi",
    "  while [[ $v ]]; do b; done",
    "  for e in $v two; do use $e; done",
    "  case $v in x) p;; esac",
    "  $v somearg",                 # dynamic command name — reads v to determine callee
    "  cmd > $v",                   # redirect destination reads v
    "  echo $(helper $v)",
    '  echo "interp $v"',
])
def test_statement_position_reads(body):
    assert _vfg_reads_v(body), f"(sanity) VFG should read $v: {body}"
    assert _pdg_reads_v(body), f"PDG dropped a statement-position read: {body}"


# --- store side: a binding must reach a later use --------------------------------------------------

def test_assignment_reaches_use():
    _l, e = structure_bash.pdg_source(
        "f() {\n  a=$P\n  b=$a\n  echo $b\n}")["f"]
    # ENTRY=0, a=$P is 1, b=$a is 2, echo $b is 3.
    assert (1, 2, "D") in e, "assignment did not reach its use"
    assert (2, 3, "D") in e, "second assignment did not reach its use"


# --- precision side: a LITERAL command name must NOT be read as a variable by EITHER builder -------

_NONVALUE_V = {
    "bare_command": "  v",           # `v` is a command NAME, not the variable $v
    "bare_command_args": "  v a b",  # still a command name
    "plain_word_arg": "  echo v",    # `v` is a literal word argument
}


@pytest.mark.parametrize("name", sorted(_NONVALUE_V))
def test_command_name_not_read_by_either_builder(name):
    body = _NONVALUE_V[name]
    # NB: a literal command name / word is a free callee or constant — neither builder reads the var.
    assert not _pdg_reads_v(body), f"{name}: PDG read a literal command name as the variable"
    assert not _vfg_reads_v(body), f"{name}: VFG read a literal command name as the variable"
