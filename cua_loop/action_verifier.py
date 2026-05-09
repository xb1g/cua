"""Cheap per-action verification signals for the AEGIS action loop."""

from __future__ import annotations

from dataclasses import dataclass


MUTATING_ACTIONS = {"click", "double_click", "type", "key", "keypress", "scroll", "hscroll", "drag", "navigate"}


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
