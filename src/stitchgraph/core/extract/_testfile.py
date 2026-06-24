"""Shared test-file heuristic for ALL extractors (Python `ast` + tree-sitter).

A single source of truth so the extractors can't disagree on what counts as a test
file — a divergence here (Python checked only the filename, tree-sitter also checked
directories) caused a cardinal false-dead: a shared test base class in `tests/
conftest.py` got no `test` role, so a thin subclass inheriting its tests was flagged
dead (Panel CC). Conventional test layouts only; ambiguous `testing`/`specs` dirs are
excluded because they are plausible *production* directories (Panel Y).
"""

from __future__ import annotations


def is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].lower()
    parts = rel.lower().split("/")
    if {"test", "tests", "spec", "__tests__"} & set(parts):
        return True
    # `_spec.` catches Ruby/JS RSpec/Jasmine `foo_spec.rb`; `conftest.py` is pytest's
    # shared-fixture/base module; `_tests.` some C#/JS layouts.
    return (name.startswith("test_") or name == "conftest.py"
            or any(p in name for p in ("_test.", ".test.", "_spec.", ".spec.", "_tests.")))
