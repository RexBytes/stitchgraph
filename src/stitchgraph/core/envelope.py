"""The universal result envelope (design §4).

Every operation, on every surface (library / CLI / MCP / report), returns a
`Result`. Confidence is load-bearing: it gates `needs_review`, and `urgency`
(set only on issue results) is capped by provenance so nothing low-confidence
can ever shout red (design §7).

This module is stdlib-only by design — importing the core must stay light.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")

# Confidence below this is flagged for review (design §4). `config.load_config`
# overrides it from `stitchgraph.toml [review] threshold` via set_review_threshold;
# envelope stays stdlib-only (config depends on envelope, never the reverse).
REVIEW_THRESHOLD = 0.80

# Generic fallback so `needs_review` is never True with an empty `review_reasons` — an
# unexplained review flag a consumer (LLM/CLI) can't act on (panels R19B/R20B). A specific
# reason added later via `add_reason` supersedes it.
_DEFAULT_REVIEW_REASON = (
    "low-confidence or name-based result — verify before relying on it")


def set_review_threshold(value: float) -> None:
    """Set the review-confidence threshold (called by config when a toml is loaded)."""
    global REVIEW_THRESHOLD
    REVIEW_THRESHOLD = value


class Provenance(str, Enum):
    EXTRACTED = "extracted"  # read directly from syntax/LSP, weight ~1.0
    INFERRED = "inferred"  # derived by heuristic, weight < 1.0
    AMBIGUOUS = "ambiguous"  # multiple candidates, none dominant


class ReviewCode(str, Enum):
    """Stable machine-readable codes for `review_reasons` (field review 2026-07-09,
    request 11): an agent filters on `review_codes` programmatically instead of
    string-matching prose. One code may back several prose reasons; the code set is
    the CONTRACT — never rename a member, only add. Documented in docs/design.md §4.
    """

    LOW_CONFIDENCE = "LOW_CONFIDENCE"              # confidence below the review threshold
    AMBIGUOUS_PROVENANCE = "AMBIGUOUS_PROVENANCE"  # result provenance is AMBIGUOUS
    REFUSED = "REFUSED"                            # the operation refused to answer (no result)
    HEDGED_RESULT = "HEDGED_RESULT"                # answered, but hedged — a partial/advisory result
    UNSPECIFIED = "UNSPECIFIED"                    # flagged without a specific code
    NAME_BASED_EDGE = "NAME_BASED_EDGE"            # rests on inferred/ambiguous (name-matched) edges
    NON_CALL_USES = "NON_CALL_USES"                # empty CALLS answer, but other relations touch it
    CYCLE_HEURISTIC = "CYCLE_HEURISTIC"            # cycle backed mostly/only by heuristic edges
    LSP_UNAVAILABLE = "LSP_UNAVAILABLE"            # a language server declined / is missing or broken
    COVERAGE_ABSENT = "COVERAGE_ABSENT"            # symbol/test absent from the coverage artifact
    COVERAGE_MISMATCH = "COVERAGE_MISMATCH"        # artifact ids don't line up with graph node ids
    RESOLVER_GAP = "RESOLVER_GAP"                  # executed on paths the static graph cannot see
    STATIC_ONLY = "STATIC_ONLY"                    # answer rests on static reach alone (no runtime)
    UNRESOLVED_SYMBOL = "UNRESOLVED_SYMBOL"        # a requested symbol did not resolve uniquely
    IMPLICIT_COUPLING = "IMPLICIT_COUPLING"        # co-activation without a static edge (candidate)
    PROFILE_IDENTITY = "PROFILE_IDENTITY"          # identical coverage profile != behavioural identity
    PARSE_FAILURE = "PARSE_FAILURE"                # files could not be parsed / are missing from graph
    SUPPRESSED_RESULTS = "SUPPRESSED_RESULTS"      # low-signal findings were capped/suppressed (never silent)
    NO_ENTRY_POINTS = "NO_ENTRY_POINTS"            # liveness ranking unavailable (no entry points found)


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
    review_codes: list[str] = field(default_factory=list)  # stable ReviewCode values
    urgency: Urgency | None = None  # issue results only
    alternatives: list[Any] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # needs_review fires when confidence is low OR provenance is ambiguous. The
        # `not (0.0 <= confidence <= 1.0)` clause also catches NaN and out-of-range
        # confidence (NaN < threshold is False, so a NaN would otherwise silently skip
        # review and emit invalid JSON) — defensive, no current caller hits it (panel PPP).
        low = (not (0.0 <= self.confidence <= 1.0)
               or self.confidence < REVIEW_THRESHOLD)
        if low or self.provenance is Provenance.AMBIGUOUS:
            self.needs_review = True
            # The central rules carry their own stable codes, so every centrally-
            # flagged result is machine-filterable even before an op adds a
            # specific code of its own.
            if low:
                self._add_code(ReviewCode.LOW_CONFIDENCE)
            if self.provenance is Provenance.AMBIGUOUS:
                self._add_code(ReviewCode.AMBIGUOUS_PROVENANCE)
        # Clamp a non-finite / out-of-range confidence to a finite [0,1] value so to_dict()
        # can never emit Infinity/NaN (invalid JSON per RFC 8259). needs_review is already set
        # above; here we also CORRECT the value, not merely flag it — the envelope is the
        # universal serialization chokepoint and must self-protect even a hand-built Result or
        # a future op that forwards a user-supplied float without clamping (panel R36B).
        if not math.isfinite(self.confidence):
            self.confidence = 0.0
        else:
            self.confidence = min(max(self.confidence, 0.0), 1.0)
        # An op may flag review without an explicit reason (low confidence / ambiguous
        # provenance); guarantee the contract `needs_review => review_reasons non-empty`
        # centrally so no single op can reintroduce an unexplained flag (panels R19B/R20B).
        # The codes mirror of the same contract: a flagged result must always be
        # machine-filterable (needs_review => review_codes non-empty).
        if self.needs_review:
            if not self.review_reasons:
                self.review_reasons.append(_DEFAULT_REVIEW_REASON)
            if not self.review_codes:
                self.review_codes.append(ReviewCode.UNSPECIFIED.value)
        # Provenance gates the urgency ceiling: nothing low-confidence shouts red.
        if self.urgency is Urgency.RED and self.provenance is not Provenance.EXTRACTED:
            self.urgency = Urgency.ORANGE
        # From here on, direct `res.needs_review = True` assignments (the common
        # post-construction op idiom) route through __setattr__'s backfill so the
        # contract holds on the LIBRARY surface too, not only in to_dict — a
        # consumer filtering `res.review_codes` in-process must see the same
        # answer MCP/CLI would serialize (self-review 2026-07-09).
        object.__setattr__(self, "_contract_live", True)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if (name == "needs_review" and value
                and getattr(self, "_contract_live", False)):
            if not self.review_reasons:
                self.review_reasons.append(_DEFAULT_REVIEW_REASON)
            if not self.review_codes:
                self.review_codes.append(ReviewCode.UNSPECIFIED.value)

    def add_reason(self, reason: str,
                   code: ReviewCode | str | None = None) -> Result[T]:
        if reason not in self.review_reasons:
            # A specific reason supersedes the generic fallback added in __post_init__.
            if self.review_reasons == [_DEFAULT_REVIEW_REASON]:
                self.review_reasons = []
            self.review_reasons.append(reason)
            self.needs_review = True
        if code is not None:
            self._add_code(code)
        return self

    def _add_code(self, code: ReviewCode | str) -> None:
        value = code.value if isinstance(code, ReviewCode) else str(code)
        # A specific code supersedes the generic UNSPECIFIED backfill, exactly as
        # a specific reason supersedes _DEFAULT_REVIEW_REASON in add_reason.
        if (value != ReviewCode.UNSPECIFIED.value
                and self.review_codes == [ReviewCode.UNSPECIFIED.value]):
            self.review_codes = []
        if value not in self.review_codes:
            self.review_codes.append(value)

    def to_dict(self) -> dict[str, Any]:
        # The `needs_review => review_codes non-empty` contract is upheld at
        # construction (__post_init__) and on assignment (__setattr__), so the
        # serialized codes are exactly the in-process codes — the library and
        # MCP/CLI surfaces can never disagree.
        return {
            "ok": self.ok,
            "result": _plain(self.result),
            "confidence": round(self.confidence, 3),
            "provenance": self.provenance.value,
            "needs_review": self.needs_review,
            "review_reasons": self.review_reasons,
            "review_codes": self.review_codes,
            "urgency": self.urgency.value if self.urgency else None,
            "alternatives": [_plain(a) for a in self.alternatives],
            "meta": _plain(self.meta),  # honour _plain as the chokepoint for ALL values:
            # a stray non-finite float in meta would otherwise serialize to Infinity/NaN
            # (invalid JSON). Latent today — no op puts a non-finite float in meta — but the
            # envelope must self-protect at the one serialization point (panel R38).
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

    The stable code tracks `ok`: a true refusal (no result) is REFUSED; a
    hedged partial answer (result supplied, ok=True — find_stale's
    no-entry-points candidates) is HEDGED_RESULT, so a machine consumer
    treating REFUSED as "no answer, try differently" never discards a genuine
    candidate payload (self-review 2026-07-09).
    """
    code = ReviewCode.REFUSED if result is None else ReviewCode.HEDGED_RESULT
    return Result(ok=result is not None, result=result, confidence=confidence,
                  provenance=provenance, needs_review=True,
                  review_reasons=list(reasons),
                  review_codes=[code.value], meta=meta)


def _plain(value: Any) -> Any:
    """Best-effort conversion of dataclass payloads to JSON-friendly dicts."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        # Non-finite floats serialize to Infinity/NaN (invalid JSON, RFC 8259). No current op
        # puts a non-finite float in a payload/meta (weights are sanitized on read, confidences
        # bounded), but _plain is the serialization chokepoint for ALL result/meta values, so
        # drop a stray non-finite to None rather than emit invalid JSON (panel R37B, defense).
        return value if math.isfinite(value) else None
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
