"""Detect repeated screenshots and repeated action loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoopSignal:
    stuck: bool
    reason: str | None = None
    confidence: float = 0.0


def screenshot_hash(screenshot: str | bytes | None) -> str | None:
    if screenshot is None:
        return None
    if isinstance(screenshot, bytes):
        payload = screenshot
    else:
        payload = screenshot.encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def action_signature(action: Any) -> str:
    if isinstance(action, dict):
        payload = action
    else:
        payload = {key: getattr(action, key, None) for key in ("type", "x", "y", "text", "url", "keys")}
    return json.dumps(payload, sort_keys=True, default=str)


def detect_repeated_states(screenshots: list[str | bytes | None], window: int = 4) -> LoopSignal:
    hashes = [screenshot_hash(item) for item in screenshots[-window:] if item is not None]
    if len(hashes) >= window and len(set(hashes)) == 1:
        return LoopSignal(True, f"same screen repeated {window} times", 0.95)
    if len(hashes) >= window and len(set(hashes)) <= 2:
        return LoopSignal(True, f"screen oscillating in last {window} states", 0.7)
    return LoopSignal(False)


def detect_repeated_actions(actions: list[Any], window: int = 4) -> LoopSignal:
    signatures = [action_signature(action) for action in actions[-window:]]
    if len(signatures) >= window and len(set(signatures)) == 1:
        return LoopSignal(True, f"same action repeated {window} times", 0.95)
    return LoopSignal(False)


def detect_loop(screenshots: list[str | bytes | None], actions: list[Any], window: int = 4) -> LoopSignal:
    state_signal = detect_repeated_states(screenshots, window)
    if state_signal.stuck:
        return state_signal
    return detect_repeated_actions(actions, window)
