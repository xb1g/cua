"""Retry strategy decisions for failed step verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RetryStrategy = Literal["resample", "rephrase", "escalate", "abort"]


@dataclass(frozen=True)
class RetryDecision:
    strategy: RetryStrategy | None
    reason: str


def decide_retry_strategy(
    drift_reason: str | None,
    confidence: float,
    failed_step_count: int = 0,
    blocked: bool = False,
) -> RetryDecision:
    if blocked:
        return RetryDecision("abort", "security policy blocked the action")
    if drift_reason == "stuck_loop":
        return RetryDecision("rephrase", "loop detected; rephrase the next prompt with explicit escape instructions")
    if drift_reason == "modal_blocking":
        return RetryDecision("resample", "modal may be dismissed with a new sampled action")
    if drift_reason == "wrong_page":
        return RetryDecision("rephrase", "agent appears on the wrong page")
    if drift_reason == "unexpected_change" and confidence >= 0.75:
        return RetryDecision("resample", "screen changed in an unexpected way")
    if failed_step_count >= 3:
        return RetryDecision("escalate", "multiple low-confidence failures in a row")
    if confidence < 0.35:
        return RetryDecision("resample", "low-confidence verifier result")
    return RetryDecision(None, "no retry needed")
