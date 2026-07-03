"""Envelope -> human-readable text. Shared by the CLI and the report.

Stdlib-only so it can render without any optional dependency installed.
"""

from __future__ import annotations

from ..core.envelope import Result, Urgency

_URGENCY_TAG = {Urgency.RED: "[RED]", Urgency.ORANGE: "[ORANGE]", Urgency.GREEN: "[GREEN]"}


def render_text(op_name: str, result: Result) -> str:
    lines: list[str] = []
    status = "ok" if result.ok else "no result"
    head = f"{op_name}: {status}  (confidence {result.confidence:.2f}, {result.provenance.value})"
    if result.urgency:
        head = f"{_URGENCY_TAG[result.urgency]} {head}"
    lines.append(head)

    if result.needs_review:
        lines.append("  needs review:")
        for reason in result.review_reasons:
            lines.append(f"    - {reason}")

    lines.append(_render_payload(result.result))

    if result.alternatives:
        lines.append(f"  alternatives: {len(result.alternatives)}")
    if result.meta:
        meta = ", ".join(f"{k}={v}" for k, v in result.meta.items())
        lines.append(f"  ({meta})")
    return "\n".join(lines)


def _render_payload(payload: object, indent: str = "  ") -> str:
    if payload is None:
        return f"{indent}(none)"
    if isinstance(payload, list):
        if not payload:
            return f"{indent}(empty)"
        out = "\n".join(f"{indent}- {_one(item)}" for item in payload[:50])
        if len(payload) > 50:
            # Say the output was cut — silent truncation reads as "that's everything"
            # (review 2026-07-03, F10e). --json always carries the full payload.
            out += f"\n{indent}… {len(payload) - 50} more (use --json for the full list)"
        return out
    if isinstance(payload, dict):
        return "\n".join(f"{indent}{k}: {_one(v)}" for k, v in payload.items())
    return f"{indent}{payload}"


def _one(item: object) -> str:
    if isinstance(item, dict):
        return ", ".join(f"{k}={v}" for k, v in item.items())
    return str(item)
