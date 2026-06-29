"""Demo for the graph-diff oracle prototype.

Three scenarios, in increasing difficulty:

  1. BASELINE — index the same source twice. A correct diff must be empty (the oracle is
     not flagging phantom deltas). This is the regression-guard for the primitive itself.

  2. PERTURBED (id mode, same language) — two tiny Python fixtures that differ by one
     dropped call + one renamed function. The diff must *locate* exactly those deltas.
     This is the #3 use-case: "does the actual graph match the planned one?"

  3. TRANSLATION (leaf mode, cross-language) — the same toy program in Python and in
     JavaScript. id mode is hopeless (names/paths differ); leaf mode asks the real
     question: did the JS translation preserve the call/def *shape* of the Python original?
     Per §2 this is an ORACLE signal, not proof — residual deltas are extractor asymmetry
     as much as translation error, and the output says so.

Run: python research/graphdiff/demo.py
"""
from __future__ import annotations

import pathlib
import tempfile

from graphdiff import diff, index, summarize

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures"


def _write(d: pathlib.Path, files: dict[str, str]) -> str:
    for name, body in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return str(d)


def scenario_baseline() -> None:
    print("=" * 70)
    print("1. BASELINE — same source indexed twice (expect EQUIVALENT)")
    print("=" * 70)
    a = index("src/stitchgraph")
    b = index("src/stitchgraph")
    print(summarize(diff(a, b, mode="id")))
    print()


def scenario_perturbed() -> None:
    print("=" * 70)
    print("2. PERTURBED — drop a call + rename a fn (expect located deltas)")
    print("=" * 70)
    orig = {
        "app.py": (
            "def helper():\n    return 1\n\n"
            "def log(x):\n    return x\n\n"
            "def run():\n    log(helper())\n    return helper()\n"
        )
    }
    # v2: run() no longer calls log(); helper() renamed to compute()
    edited = {
        "app.py": (
            "def compute():\n    return 1\n\n"
            "def log(x):\n    return x\n\n"
            "def run():\n    return compute()\n"
        )
    }
    with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
        a = index(_write(pathlib.Path(da), orig))
        b = index(_write(pathlib.Path(db), edited))
        print(summarize(diff(a, b, mode="id")))
    print()


def scenario_translation() -> None:
    print("=" * 70)
    print("3. TRANSLATION — same program, Python vs JS (leaf mode oracle)")
    print("=" * 70)
    py = {
        "prog.py": (
            "def parse(s):\n    return s.strip()\n\n"
            "def validate(s):\n    return parse(s)\n\n"
            "def main():\n    return validate('x')\n"
        )
    }
    js = {
        "prog.js": (
            "function parse(s){ return s.trim(); }\n"
            "function validate(s){ return parse(s); }\n"
            "function main(){ return validate('x'); }\n"
        )
    }
    # a *restructured* translation: validate() folded into a class method, parse kept free.
    # This is the realistic case — a faithful translation rarely preserves module shape, so
    # qualified names diverge (Validator.validate vs validate) even when behaviour matches.
    js_restructured = {
        "prog.js": (
            "function parse(s){ return s.trim(); }\n"
            "class Validator {\n  validate(s){ return parse(s); }\n}\n"
            "function main(){ return new Validator().validate('x'); }\n"
        )
    }
    with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db, \
            tempfile.TemporaryDirectory() as dc:
        a = index(_write(pathlib.Path(da), py))
        b = index(_write(pathlib.Path(db), js))
        c = index(_write(pathlib.Path(dc), js_restructured))

        print("-- 3a. literal translation, id mode (top-level names coincide) --")
        print(summarize(diff(a, b, mode="id")))
        print("\n-- 3b. literal translation, leaf mode (shape preserved) --")
        print(summarize(diff(a, b, mode="leaf")))

        print("\n-- 3c. RESTRUCTURED translation, id mode (names diverge → noisy) --")
        print(summarize(diff(a, c, mode="id")))
        print("\n-- 3d. RESTRUCTURED translation, leaf mode (oracle: what changed?) --")
        print(summarize(diff(a, c, mode="leaf")))
    print()


if __name__ == "__main__":
    scenario_baseline()
    scenario_perturbed()
    scenario_translation()
