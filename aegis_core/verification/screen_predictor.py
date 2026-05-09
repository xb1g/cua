"""Predict and verify expected screen changes after CUA actions."""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal


MutationExpectation = Literal["should_change", "may_change", "should_not_change"]

MUTATING_ACTIONS = {"click", "double_click", "type", "key", "keypress", "scroll", "hscroll", "drag", "navigate"}
PASSIVE_ACTIONS = {"wait", "screenshot", "noop"}


@dataclass(frozen=True)
class ScreenPrediction:
    expectation: MutationExpectation
    reason: str


@dataclass(frozen=True)
class ScreenComparison:
    changed: bool
    confidence: float
    reason: str


@dataclass(frozen=True)
class ScreenVerdict:
    ok: bool
    confidence: float
    reason: str
    changed: bool


def action_type(action: Any) -> str:
    if isinstance(action, dict):
        return str(action.get("type", ""))
    return str(getattr(action, "type", ""))


def predict_expected_observation(action: Any, prev_screenshot: str | bytes | None = None) -> ScreenPrediction:
    kind = action_type(action)
    if kind in PASSIVE_ACTIONS:
        return ScreenPrediction("may_change", f"{kind} can leave the screen unchanged")
    if kind in MUTATING_ACTIONS:
        return ScreenPrediction("should_change", f"{kind} should produce a visible state change")
    if kind in {"terminate", "answer", "done"}:
        return ScreenPrediction("should_not_change", "terminal action should not require screen mutation")
    return ScreenPrediction("may_change", f"unknown action type: {kind}")


def _fingerprint(screenshot: str | bytes | None) -> str | None:
    if screenshot is None:
        return None
    if isinstance(screenshot, bytes):
        return hashlib.sha256(screenshot).hexdigest()
    if screenshot.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(screenshot, timeout=3) as response:
                return hashlib.sha256(response.read()).hexdigest()
        except Exception:
            return hashlib.sha256(screenshot.encode("utf-8")).hexdigest()
    return hashlib.sha256(screenshot.encode("utf-8")).hexdigest()


def compare_screens(prev_screenshot: str | bytes | None, next_screenshot: str | bytes | None) -> ScreenComparison:
    before = _fingerprint(prev_screenshot)
    after = _fingerprint(next_screenshot)
    if not before or not after:
        return ScreenComparison(False, 0.2, "missing screenshot for comparison")
    if before == after:
        return ScreenComparison(False, 0.9, "screen fingerprint unchanged")
    return ScreenComparison(True, 0.85, "screen fingerprint changed")


def verify_screen_change(action: Any, prev_screenshot: str | bytes | None, next_screenshot: str | bytes | None) -> ScreenVerdict:
    prediction = predict_expected_observation(action, prev_screenshot)
    comparison = compare_screens(prev_screenshot, next_screenshot)

    if prediction.expectation == "should_change" and not comparison.changed:
        return ScreenVerdict(False, comparison.confidence, f"expected change but {comparison.reason}", comparison.changed)
    if prediction.expectation == "should_not_change" and comparison.changed:
        return ScreenVerdict(False, comparison.confidence, f"unexpected change: {comparison.reason}", comparison.changed)
    return ScreenVerdict(True, comparison.confidence, prediction.reason, comparison.changed)
