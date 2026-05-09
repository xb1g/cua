"""Progress scoring for recent CUA steps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProgressScore:
    score: float
    reason: str
    drift: bool


POSITIVE_WORDS = ("found", "extracted", "filtered", "sorted", "opened", "loaded", "selected", "verified", "price")
NEGATIVE_WORDS = ("error", "blocked", "failed", "captcha", "login", "unavailable", "timeout", "wrong", "stuck")


def _step_text(step: Any) -> str:
    if isinstance(step, dict):
        return " ".join(str(v or "") for v in step.values()).lower()
    return " ".join(
        str(getattr(step, field, "") or "")
        for field in ("action_type", "model_message", "verification_reason", "block_reason")
    ).lower()


def score_progress(goal: str, recent_steps: list[Any], threshold: float = 0.45) -> ProgressScore:
    if not recent_steps:
        return ProgressScore(0.5, "no recent steps yet", False)

    text = "\n".join(_step_text(step) for step in recent_steps[-6:])
    goal_terms = [term for term in re.findall(r"[a-z0-9]+", goal.lower()) if len(term) > 3]
    matched_terms = sum(1 for term in set(goal_terms) if term in text)
    term_score = matched_terms / max(len(set(goal_terms)), 1)
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    failed_checks = text.count("verification_passed false") + text.count("screen fingerprint unchanged")

    score = 0.35 + term_score * 0.35 + min(positive, 4) * 0.06 - min(negative + failed_checks, 5) * 0.12
    score = max(0.0, min(1.0, score))
    if score < threshold:
        return ProgressScore(round(score, 3), "low progress signal in recent steps", True)
    return ProgressScore(round(score, 3), "recent steps appear aligned with goal", False)


def drift_for_k_steps(scores: list[ProgressScore], k: int = 3, threshold: float = 0.45) -> bool:
    if len(scores) < k:
        return False
    return all(score.score < threshold for score in scores[-k:])
