"""Lightweight AEGIS safety checks before executing CUA actions."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any


PROMPT_INJECTION_PATTERNS = (
    r"ignore (all )?(previous|prior) instructions",
    r"disregard (the )?(system|developer|previous) instructions",
    r"you are now",
    r"reveal (your )?(system prompt|instructions|secrets)",
    r"send (money|payment|funds)",
)

DANGEROUS_ACTION_PATTERNS = (
    r"\b(delete|remove|drop|destroy|wipe)\b",
    r"\b(purchase|buy|checkout|order|subscribe)\b",
    r"\b(send|transfer|wire|pay)\b.*\b(money|funds|payment|cash|crypto)\b",
    r"\bsubmit\b.*\b(password|payment|card|ssn|secret|token)\b",
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


def _text_from_action(action: Any) -> str:
    parts: list[str] = [str(getattr(action, "type", ""))]
    for field in ("text", "url", "result"):
        value = getattr(action, field, None)
        if value:
            parts.append(str(value))
    keys = getattr(action, "keys", None)
    if keys:
        parts.append(" ".join(str(k) for k in keys))
    return " ".join(parts).lower()


def detect_prompt_injection(*texts: str | None) -> str | None:
    haystack = "\n".join(t or "" for t in texts).lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, haystack):
            return f"prompt injection pattern matched: {pattern}"
    return None


def check_action_policy(action: Any, model_message: str | None = None) -> PolicyDecision:
    if os.getenv("AEGIS_ALLOW_DANGEROUS_ACTIONS", "").lower() in {"1", "true", "yes"}:
        return PolicyDecision(True)

    injection = detect_prompt_injection(model_message, _text_from_action(action))
    if injection:
        return PolicyDecision(False, injection)

    action_text = _text_from_action(action)
    for pattern in DANGEROUS_ACTION_PATTERNS:
        if re.search(pattern, action_text):
            return PolicyDecision(False, f"dangerous action pattern matched: {pattern}")

    return PolicyDecision(True)
