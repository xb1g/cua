"""Inline verification middleware for Stream A CUA loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from aegis_core.verification.loop_breaker import detect_loop
from aegis_core.verification.on_track import score_progress
from aegis_core.verification.retry_policy import RetryStrategy, decide_retry_strategy
from aegis_core.verification.screen_predictor import verify_screen_change


@dataclass(frozen=True)
class Verdict:
    on_track: bool
    confidence: float
    drift_reason: str | None
    retry_strategy: RetryStrategy | None
    reason: str = ""


@dataclass
class StepState:
    goal: str
    screenshot: str | bytes | None = None
    recent_steps: list[Any] = field(default_factory=list)
    recent_screenshots: list[str | bytes | None] = field(default_factory=list)
    recent_actions: list[Any] = field(default_factory=list)
    failed_step_count: int = 0
    blocked: bool = False


def _drift_reason(screen_ok: bool, screen_changed: bool, progress_drift: bool, loop_stuck: bool) -> str | None:
    if loop_stuck:
        return "stuck_loop"
    if progress_drift:
        return "wrong_page"
    if not screen_ok and screen_changed:
        return "unexpected_change"
    if not screen_ok:
        return "modal_blocking"
    return None


def verify_step(state: StepState, action: Any, next_state: StepState) -> Verdict:
    screen = verify_screen_change(action, state.screenshot, next_state.screenshot)
    progress = score_progress(state.goal, next_state.recent_steps)
    loop = detect_loop(next_state.recent_screenshots, next_state.recent_actions)

    drift_reason = _drift_reason(screen.ok, screen.changed, progress.drift, loop.stuck)
    on_track = drift_reason is None and not state.blocked and not next_state.blocked
    confidence = round(min(screen.confidence, max(progress.score, loop.confidence if loop.stuck else progress.score)), 3)
    if loop.stuck:
        confidence = max(confidence, loop.confidence)

    retry = decide_retry_strategy(
        drift_reason,
        confidence,
        failed_step_count=next_state.failed_step_count,
        blocked=state.blocked or next_state.blocked,
    )
    reason = "; ".join(part for part in (screen.reason, progress.reason, loop.reason, retry.reason) if part)
    return Verdict(on_track, confidence, drift_reason, retry.strategy, reason)
