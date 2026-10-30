"""Conservative intake checks for instructions and explicit threats in free text."""

from __future__ import annotations

import re

_INJECTION = (
    re.compile(r"\b(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|above|system|developer)\s+instructions\b", re.I),
    re.compile(r"\b(?:reveal|print|show|leak)\s+(?:the\s+|your\s+)?(?:system|developer|hidden)\s+prompt\b", re.I),
    re.compile(r"\b(?:you are now|act as|pretend to be)\s+(?:the\s+)?(?:system|developer|assistant)\b", re.I),
    re.compile(r"\byou are now\s+(?:an?\s+)?(?:unrestricted|unfiltered|uncensored|jailbroken)\s+assistant\b", re.I),
    re.compile(r"(?:<\|im_start\|>|\[/?INST\]|<\|system\|>|</?system>)", re.I),
    re.compile(r"\b(?:bypass|disable)\s+(?:the\s+|all\s+)?(?:safety|guardrails?|verification|filters?)\b", re.I),
)
_TOXICITY = (
    re.compile(r"\b(?:i|we)\s+(?:will|am going to)\s+(?:kill|murder|hurt)\s+(?:you|them|the staff|the officer)\b", re.I),
    re.compile(r"\b(?:kill|murder)\s+(?:all|every)\s+(?:bank|company|office)\s+(?:staff|employees|workers)\b", re.I),
)


def check_free_text(fields: dict[str, str | None]) -> list[dict[str, str]]:
    """Return field-level errors; anger about a service is not a rejection reason."""
    errors: list[dict[str, str]] = []
    for field, value in fields.items():
        if not value:
            continue
        if any(pattern.search(value) for pattern in _INJECTION):
            errors.append({"field": field, "message": "instruction-like content is not allowed", "type": "guardrail:injection"})
        if any(pattern.search(value) for pattern in _TOXICITY):
            errors.append({"field": field, "message": "explicit threat is not allowed", "type": "guardrail:toxicity"})
    return errors
