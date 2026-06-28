# stitchgraph v2.1.29 — Python abstract / Protocol interface methods (#70, #86)

A bodyless interface-method declaration is a contract fulfilled by overrides, never called by name —
so it should not be reported as dead code.

## The bug

```python
from typing import Protocol, TypeVar
T = TypeVar("T")

class Repo(Protocol[T]):          # subscripted Protocol base
    def get(self, i: int) -> T: ...
    def put(self, x: T) -> None: ...   # flagged dead

def consume(r: Repo) -> None: r.get(1)
```

Two compounding gaps:
- **#70** — `_is_abstract_class` used `_name_of` on each base, which returns None for an
  `ast.Subscript` (`Protocol[T]`, `Generic[T]`), so `Repo` was not recognized as abstract.
- **#86** — a bodyless abstract / Protocol method had no root, so an uncalled one (`Repo.put`) was
  flagged dead though it is an interface contract.

## The fix

- `_is_abstract_class` now unwraps a subscripted base via `_base_name`, recognizing
  `class Repo(Protocol[T])` / `class C(ABC, Generic[T])`.
- A bodyless abstract / Protocol method (`def m(self): ...` under `@abstractmethod` or inside a
  Protocol/ABC) is rooted `callback`.

**Cardinal-safe:** rooting only adds a root. A method with a real body — a concrete default in an
ABC — that is genuinely uncalled still flags dead, as does the private helper it alone reaches.
Precision is preserved; only bodyless contracts are spared.

## Resolved without a code change

**#71** — `_framework_classes` resolves externally-subclassed base names across files, and a
name collision can over-mask (keep a class live that a precise analysis would not). Over-masking is
the cardinal-SAFE direction. Tightening it would *un-mask*, risking a live framework-only-reachable
class being flagged dead — the cardinal sin. The current behavior is therefore a deliberate
precision boundary, left intentionally.

## Compatibility

No API or schema change; indexes rebuild cleanly. The differential streaming oracle confirms the
streamed graph stays byte-identical to the in-memory one.

## Quality gate

Full suite — 553 tests (subscripted-Protocol recognition unit-pin; uncalled Protocol/abstract
contract spared; concrete uncalled ABC method + its helper still flag) + ruff + mypy clean;
differential oracle suite (27) green; mutation meta-oracle on `_is_abstract_class` (2/2 killed).
Two-round full-diversity multi-model adversarial review — no in-scope cardinal, no crash.
