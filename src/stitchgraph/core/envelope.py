"""The universal result envelope (design §4).

Every operation, on every surface (library / CLI / MCP / report), returns a
`Result`. Confidence is load-bearing: it gates `needs_review`, and `urgency`
(set only on issue results) is capped by provenance so nothing low-confidence
can ever shout red (design §7).

This module is stdlib-only by design — importing the core must stay light.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")

# Confidence below this is flagged for review (design §4). Overridable via config.
REVIEW_THRESHOLD = 0.80


class Provenance(str, Enum):
    EXTRACTED = "extracted"  # read directly from syntax/LSP, weight ~1.0
    INFERRED = "inferred"  # derived by heuristic, weight < 1.0
    AMBIGUOUS = "ambiguous"  # multiple candidates, none dominant


class Urgency(str, Enum):
    GREEN = "green"  # informational / safe cleanup / low risk
    ORANGE = "orange"  # anomaly, ambiguous, or moderate impact — look closer
    RED = "red"  # live, high-impact, high-confidence — fix now


@dataclass
class Result(Generic[T]):
    """Outcome of one operation. See design §4 / §7."""

    ok: bool
    result: T | None = None
    confidence: float = 1.0
    provenance: Provenance = Provenance.EXTRACTED
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    urgency: Urgency | None = None  # issue results only
    alternatives: list[Any] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # needs_review fires when confidence is low OR provenance is ambiguous.
        if self.confidence < REVIEW_THRESHOLD or self.provenance is Provenance.AMBIGUOUS:
            self.needs_review = True
        # Provenance gates the urgency ceiling: nothing low-confidence shouts red.
        if self.urgency is Urgency.RED and self.provenance is not Provenance.EXTRACTED:
            self.urgency = Urgency.ORANGE

    def add_reason(self, reason: str) -> Result[T]:
        if reason not in self.review_reasons:
            self.review_reasons.append(reason)
            self.needs_review = True
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "result": _plain(self.result),
            "confidence": round(self.confidence, 3),
            "provenance": self.provenance.value,
            "needs_review": self.needs_review,
            "review_reasons": self.review_reasons,
            "urgency": self.urgency.value if self.urgency else None,
            "alternatives": [_plain(a) for a in self.alternatives],
            "meta": self.meta,
        }


def ok(result: T, *, confidence: float = 1.0,
       provenance: Provenance = Provenance.EXTRACTED, **meta: Any) -> Result[T]:
    """Construct a successful result."""
    return Result(ok=True, result=result, confidence=confidence,
                  provenance=provenance, meta=meta)


def refuse(*reasons: str, confidence: float = 0.0,
           provenance: Provenance = Provenance.AMBIGUOUS,
           result: Any = None, **meta: Any) -> Result[Any]:
    """Construct a refuse-when-unsure result: ok but flagged for review.

    Used when an operation can't answer confidently — the single most valuable
    contract for an LLM consumer (design principle 4).
    """
    return Result(ok=result is not None, result=result, confidence=confidence,
                  provenance=provenance, needs_review=True,
                  review_reasons=list(reasons), meta=meta)


def _plain(value: Any) -> Any:
    """Best-effort conversion of dataclass payloads to JSON-friendly dicts."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(value)
    return str(value)
