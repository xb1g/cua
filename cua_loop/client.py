"""Single-attempt CUA inner loop.

Northstar drives the brain (via the Lightcone Responses API). The browser
surface is pluggable: Kernel by default, Lightcone-managed as a fallback.
Outer retry / verification logic lives in runner.py.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from rich.console import Console
from tzafon import Lightcone

from cua_loop.action_verifier import verify_action_effect
from cua_loop.backends import BrowserBackend, make_backend
from cua_loop.marketplace import check_marketplace_action_policy
from cua_loop.security import check_action_policy
from cua_loop.types import Step, Trajectory

console = Console()

MODEL = os.getenv("NORTHSTAR_MODEL", "tzafon.northstar-cua-fast")
DISPLAY_WIDTH = int(os.getenv("CUA_DISPLAY_WIDTH", "1280"))
DISPLAY_HEIGHT = int(os.getenv("CUA_DISPLAY_HEIGHT", "720"))
MAX_STEPS = int(os.getenv("CUA_MAX_STEPS", "40"))
_MARKETPLACE_MODE = os.getenv("AEGIS_MARKETPLACE_MODE", "true").lower() in {"1", "true", "yes"}

TOOLS = [
    {
        "type": "computer_use",
        "display_width": DISPLAY_WIDTH,
        "display_height": DISPLAY_HEIGHT,
        "environment": "desktop",
    }
]


def _action_to_dict(action: Any) -> dict[str, Any]:
    keys = (
        "type",
        "x",
        "y",
        "end_x",
        "end_y",
        "text",
        "keys",
        "url",
        "scroll_x",
        "scroll_y",
        "button",
        "status",
        "result",
    )
    return {k: getattr(action, k, None) for k in keys if getattr(action, k, None) is not None}


def _notify_ui(step: int, task: str, screenshot_url: str, action: Any = None, **extra: Any) -> None:
    try:
        httpx.post(
            "http://localhost:8555/update",
            json={
                "step": step,
                "task": task,
                "screenshot_url": screenshot_url,
                "action": _action_to_dict(action) if action else {},
                **extra,
            },
            timeout=0.2,
        )
    except Exception:
        pass


def _execute_action(b: BrowserBackend, action: Any) -> bool:
    """Dispatch a Northstar action onto the browser backend.

    Returns True if the loop should terminate.
    """
    t = action.type
    x = getattr(action, "x", 0) or 0
    y = getattr(action, "y", 0) or 0

    if t == "click" and getattr(action, "button", "left") == "right":
        b.right_click(x, y)
    elif t == "click":
        b.click(x, y)
    elif t == "double_click":
        b.double_click(x, y)
    elif t == "type":
        b.type(getattr(action, "text", "") or "")
    elif t in ("key", "keypress"):
        b.hotkey(*(getattr(action, "keys", []) or []))
    elif t == "scroll":
        b.scroll(0, getattr(action, "scroll_y", 0) or 0, x or 640, y or 400)
    elif t == "hscroll":
        b.scroll(getattr(action, "scroll_x", 0) or 0, 0, x or 640, y or 400)
    elif t == "drag":
        end_x = getattr(action, "end_x", x) or x
        end_y = getattr(action, "end_y", y) or y
        b.drag(x, y, end_x, end_y)
    elif t == "navigate":
        b.navigate(getattr(action, "url", "") or "")
    elif t == "wait":
        b.wait(2)
    elif t in ("terminate", "answer", "done"):
        return True
    else:
        console.print(f"[yellow]Unknown action: {t}[/yellow]")
    return False


def run_single_attempt(
    task: str,
    url: str | None = None,
    extra_context: str = "",
    kind: str = "browser",
) -> Trajectory:
    """One pass of the Northstar CUA loop. No retry. No verification."""
    lightcone = Lightcone(timeout=120.0)  # CUA round-trips can be slow; use generous timeout
    instruction = task
    if url:
        instruction = f"Go to {url}. Then: {task}"
    if extra_context:
        instruction += f"\n\nAdditional context from prior attempts:\n{extra_context}"

    traj = Trajectory(task=task, url=url)
    backend = make_backend(kind=kind)

    with backend as b:
        screenshot_url = b.screenshot_url()
        _notify_ui(0, instruction, screenshot_url, status="started")

        response = lightcone.responses.create(
            model=MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instruction},
                        {"type": "input_image", "image_url": screenshot_url, "detail": "auto"},
                    ],
                }
            ],
            tools=TOOLS,
        )

        for step_idx in range(MAX_STEPS):
            computer_call = None
            message_text: str | None = None

            for item in response.output or []:
                if item.type == "computer_call":
                    computer_call = item
                elif item.type == "message":
                    parts = []
                    for block in item.content or []:
                        if getattr(block, "text", None):
                            parts.append(block.text)
                    if parts:
                        message_text = "\n".join(parts)

            if not computer_call:
                traj.final_message = message_text
                console.print(f"[green]Done at step {step_idx}[/green]: {message_text or ''}")
                break

            action = computer_call.action
            step = Step(
                action_type=action.type,
                action_args=_action_to_dict(action),
                screenshot_url=screenshot_url,
                model_message=message_text,
            )
            traj.steps.append(step)
            console.print(f"[cyan]step {step_idx}[/cyan] {action.type} {_action_to_dict(action)}")
            _notify_ui(step_idx, instruction, screenshot_url, action, status="proposed")

            policy = check_action_policy(action, message_text)
            if policy.allowed and _MARKETPLACE_MODE:
                mp_policy = check_marketplace_action_policy(action, message_text)
                if not mp_policy.allowed:
                    policy = mp_policy
            if not policy.allowed:
                step.blocked = True
                step.block_reason = policy.reason
                traj.error = f"blocked unsafe action: {policy.reason}"
                _notify_ui(
                    step_idx,
                    instruction,
                    screenshot_url,
                    action,
                    status="blocked",
                    blocked=True,
                    block_reason=policy.reason,
                )
                console.print(f"[red]blocked unsafe action:[/red] {policy.reason}")
                break

            terminated = _execute_action(b, action)
            if terminated:
                traj.final_message = getattr(action, "result", None) or getattr(action, "text", None)
                break

            b.wait(1)
            after_screenshot_url = b.screenshot_url()
            action_check = verify_action_effect(action.type, screenshot_url, after_screenshot_url)
            step.after_screenshot_url = after_screenshot_url
            step.verification_passed = action_check.passed
            step.verification_reason = action_check.reason
            _notify_ui(
                step_idx,
                instruction,
                after_screenshot_url,
                action,
                status="verified" if action_check.passed else "needs_retry",
                verification_passed=action_check.passed,
                verification_reason=action_check.reason,
            )
            if not action_check.passed:
                console.print(f"[yellow]action verification failed:[/yellow] {action_check.reason}")
            screenshot_url = after_screenshot_url

            response = lightcone.responses.create(
                model=MODEL,
                previous_response_id=response.id,
                input=[
                    {
                        "type": "computer_call_output",
                        "call_id": computer_call.call_id,
                        "output": {
                            "type": "input_image",
                            "image_url": screenshot_url,
                            "detail": "auto",
                        },
                    }
                ],
                tools=TOOLS,
            )
        else:
            traj.error = f"hit MAX_STEPS={MAX_STEPS} without terminating"

    return traj
