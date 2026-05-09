"""Cheap per-action verification signals for the AEGIS action loop."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


MUTATING_ACTIONS = {"click", "double_click", "type", "key", "keypress", "scroll", "hscroll", "drag", "navigate"}

LOOP_WINDOW = 4
CONSECUTIVE_LIMIT = 5
COORDINATE_RADIUS = 30
MAX_SAME_TYPE_RATIO = 0.75


@dataclass(frozen=True)
class ActionVerification:
    passed: bool
    reason: str


def verify_action_effect(action_type: str, before_screenshot_url: str | None, after_screenshot_url: str | None) -> ActionVerification:
    if action_type not in MUTATING_ACTIONS:
        return ActionVerification(True, "non-mutating action")
    if not before_screenshot_url or not after_screenshot_url:
        return ActionVerification(False, "missing before/after screenshot")
    if before_screenshot_url == after_screenshot_url:
        return ActionVerification(False, "screen did not appear to change")
    return ActionVerification(True, "screen changed after action")


# ---------------------------------------------------------------------------
# Loop breaker
# ---------------------------------------------------------------------------

@dataclass
class _ActionRecord:
    action_type: str
    x: int | None = None
    y: int | None = None
    text: str | None = None
    keys: str | None = None


class LoopBreaker:
    """Detects when the agent is stuck repeating similar actions."""

    def __init__(self, window: int = LOOP_WINDOW, radius: int = COORDINATE_RADIUS):
        self._history: list[_ActionRecord] = []
        self._window = window
        self._radius = radius

    def record(self, action: Any) -> None:
        keys_raw = getattr(action, "keys", None)
        keys_str = "+".join(str(k) for k in keys_raw) if keys_raw else None
        rec = _ActionRecord(
            action_type=str(getattr(action, "type", "")),
            x=getattr(action, "x", None),
            y=getattr(action, "y", None),
            text=getattr(action, "text", None),
            keys=keys_str,
        )
        self._history.append(rec)

    def _consecutive_identical(self) -> int:
        if len(self._history) < 2:
            return 1
        count = 1
        last = self._history[-1]
        for prev in reversed(self._history[:-1]):
            if (prev.action_type == last.action_type
                    and prev.x == last.x and prev.y == last.y
                    and prev.text == last.text and prev.keys == last.keys):
                count += 1
            else:
                break
        return count

    def check(self) -> ActionVerification:
        consecutive = self._consecutive_identical()
        if consecutive >= CONSECUTIVE_LIMIT:
            last = self._history[-1]
            loc = f" at ({last.x},{last.y})" if last.x is not None else ""
            keys_info = f" keys={last.keys}" if last.keys else ""
            return ActionVerification(
                False,
                f"stuck: {last.action_type}{loc}{keys_info} repeated {consecutive} times consecutively",
            )

        if len(self._history) < self._window:
            return ActionVerification(True, "not enough history")

        window = self._history[-self._window:]

        types = [r.action_type for r in window]
        most_common = max(set(types), key=types.count)
        ratio = types.count(most_common) / len(types)

        if ratio < MAX_SAME_TYPE_RATIO:
            return ActionVerification(True, "action variety OK")

        same_type = [r for r in window if r.action_type == most_common]

        if most_common in ("click", "double_click") and all(r.x is not None and r.y is not None for r in same_type):
            xs = [r.x for r in same_type]
            ys = [r.y for r in same_type]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            if all(math.hypot(r.x - cx, r.y - cy) <= self._radius for r in same_type):
                return ActionVerification(
                    False,
                    f"click loop: {len(same_type)} clicks near ({int(cx)},{int(cy)}) in last {self._window} steps",
                )

        if most_common == "type":
            texts = [r.text for r in same_type if r.text]
            if len(texts) >= 2 and len(set(texts)) == 1:
                return ActionVerification(False, f"type loop: repeated '{texts[0][:40]}' {len(texts)} times")

        if ratio == 1.0:
            return ActionVerification(
                False,
                f"action loop: {most_common} repeated {len(same_type)} times in last {self._window} steps",
            )

        return ActionVerification(True, "no loop detected")
