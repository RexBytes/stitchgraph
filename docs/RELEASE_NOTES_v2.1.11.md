# stitchgraph v2.1.11 — Python implicit-invocation surface (generics, enum hooks, pytest hooks)

Three Python cardinal false-positives, found by combining **real-codebase dogfooding** (sqlalchemy,
werkzeug) with a **doc-driven manual pass** over the Python language/library reference. Each is a
live symbol the previous extractor confidently flagged dead at confidence ≥ 0.5.

## The bugs

### 1. Subscripted generic base class — missing INHERITS edge
`class Sub(Base[K, V])` (a *subscripted generic* base) recorded **no** INHERITS edge. The base
expression is an `ast.Subscript` whose `.value` holds the real name; `_name_of` returned `None` for
it, so the edge was dropped — and with it, the polymorphic-override path. A live override of a base
template method (`Base.compute` → `self._hook()`, overridden by `Sub._hook`) was reached by nothing
and flagged dead. Confirmed on sqlalchemy / werkzeug `Mixin(Base[K, V])` patterns.

### 2. Enum machinery hooks (`_missing_`, `_generate_next_value_`)
These are **single-underscore** (so not dunders) but are invoked *by name* by the enum metaclass —
`_missing_` on a failed value lookup (`Color(x)`), `_generate_next_value_` by `auto()`. The existing
`__x__` dunder rule and the IPython-hook set both missed them, so a live enum's hooks (and the
helpers they alone reach) were false-flagged dead.

### 3. pytest plugin hooks in `conftest.py` (`pytest_*`)
pytest discovers and calls `pytest_configure`, `pytest_collection_modifyitems`, … *by name* from
`conftest.py` / plugin modules — there is no in-tree call site. `conftest.py` was correctly
classified as a test file, but the `test` role was granted only to `test*`-prefixed names, so the
`pytest_*` hooks (and their callees) were flagged dead.

## The fixes

1. New `_base_name` helper unwraps `ast.Subscript` to its `.value` before resolving the base name, so
   `Base[K, V]` and `mod.Base[K, V]` resolve to `Base`. The INHERITS edge (and external-base
   detection) now fires; the polymorphic-override pass reaches the subclass override.
   `Generic`/`Iterator`/`Iterable` are already in `_PLAIN_BASES`, so `class Foo(Generic[T])` is *not*
   misclassified as a framework base.
2. `_ENUM_HOOKS` (`_missing_`, `_generate_next_value_`) added to `_is_protocol_method`, so the
   existing class→method seed (`_seed_protocol_dunders`) ties them to their class — live when the
   class is reachable, dead when it isn't (cardinal-safe, scoped to the class).
3. New `_is_pytest_hook` helper roots module-level `pytest_*` functions in test files with the
   `callback` role.

All three are reachability-*adding* changes — cardinal-safe by construction: they can only make more
code live, never flag live code dead. A dead enum's hooks and a dead subscripted-base subclass's
methods still stay dead (asserted in the regression tests).

## Compatibility

No API or schema change; indexes rebuild cleanly.

## Quality gate

Full suite (incl. four new regression tests: subscripted-generic override, enum hooks live/dead,
pytest conftest hooks, plus three helper unit tests) + ruff + mypy clean; differential oracle suite
green; mutation meta-oracle over `_base_name`, `_is_protocol_method`, `_is_pytest_hook` (all mutants
killed); two-round full-diversity multi-model adversarial review.
