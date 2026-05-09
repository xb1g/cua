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

from cua_loop.action_verifier import LoopBreaker, verify_action_effect
from cua_loop.backends import BrowserBackend, make_backend
from cua_loop.security import check_action_policy
from cua_loop.types import Step, Trajectory

console = Console()

MODEL = os.getenv("NORTHSTAR_MODEL", "tzafon.northstar-cua-fast")
DISPLAY_WIDTH = int(os.getenv("CUA_DISPLAY_WIDTH", "1280"))
DISPLAY_HEIGHT = int(os.getenv("CUA_DISPLAY_HEIGHT", "720"))
MAX_STEPS = int(os.getenv("CUA_MAX_STEPS", "40"))

TOOLS = [
    {
        "type": "computer_use",
        "display_width": DISPLAY_WIDTH,
        "display_height": DISPLAY_HEIGHT,
        "environment": "desktop",
    }
]

# Strong steering toward keyboard navigation — clicks are imprecise (model
# emits coordinates in a 0–999 grid, so even after denormalization there is
# ±5–10px of noise in screen space). Keyboard is reliable; clicks are not.
KEYBOARD_BIAS_PROMPT = (
    "IMPORTANT — strongly prefer keyboard over clicking. Clicks miss small "
    "targets. Keyboard always works.\n"
    "Keyboard playbook:\n"
    "- Cmd-L (or Ctrl-L) to focus the URL bar, then type a URL and press Enter.\n"
    "- Tab / Shift-Tab to traverse focusable elements; Enter or Space to "
    "activate the focused one.\n"
    "- '/' for site search on most sites; type query, then Enter.\n"
    "- 'j' / 'k' to move down / up feeds (HN, Reddit, Gmail, GitHub).\n"
    "- Cmd-F (or Ctrl-F) to find any visible text on the page: type a UNIQUE "
    "snippet, press Enter to land on the match, press Escape to dismiss the "
    "find bar, then activate with Tab+Enter or click only as a last resort.\n"
    "- For form fields: Tab between inputs, type the value, never click into a "
    "field if Tab can reach it.\n"
    "Only click when there is no keyboard path. When you do click, click in "
    "the middle of the visible target, not the edge."
)


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


def _notify_ui(step: int, task: str, screenshot_url: str, action: Any = None, agent_id: str = "agent_0", **extra: Any) -> None:
    try:
        httpx.post(
            "http://localhost:8555/update",
            json={
                "agent_id": agent_id,
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


def _denorm(model_x: int | float, model_y: int | float) -> tuple[int, int]:
    """Convert Northstar 0–999 model coordinates to pixel coordinates.

    Northstar always outputs in a fixed 0–999 grid regardless of actual
    screen resolution.  The backend expects pixel coordinates, so we scale
    here before dispatching any click / drag / scroll with a position.
    """
    px = int(model_x / 1000 * DISPLAY_WIDTH)
    py = int(model_y / 1000 * DISPLAY_HEIGHT)
    return px, py


def _execute_action(b: BrowserBackend, action: Any) -> bool:
    """Dispatch a Northstar action onto the browser backend.

    Returns True if the loop should terminate.
    """
    t = action.type
    # Raw model coordinates (0–999 space) — must be denormalized before use.
    mx = getattr(action, "x", 0) or 0
    my = getattr(action, "y", 0) or 0
    x, y = _denorm(mx, my)

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
        sx, sy = _denorm(mx or 500, my or 500)  # use centre fallback in model space
        b.scroll(0, getattr(action, "scroll_y", 0) or 0, sx, sy)
    elif t == "hscroll":
        sx, sy = _denorm(mx or 500, my or 500)
        b.scroll(getattr(action, "scroll_x", 0) or 0, 0, sx, sy)
    elif t == "drag":
        end_mx = getattr(action, "end_x", mx) or mx
        end_my = getattr(action, "end_y", my) or my
        end_x, end_y = _denorm(end_mx, end_my)
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
    agent_id: str = "agent_0",
) -> Trajectory:
    """One pass of the Northstar CUA loop. No retry. No verification."""
    lightcone = Lightcone(timeout=120.0)  # CUA round-trips can be slow; use generous timeout
    instruction = task
    if url:
        instruction = f"Go to {url}. Then: {task}"
    if extra_context:
        instruction += f"\n\nAdditional context from prior attempts:\n{extra_context}"
    # Prepend the keyboard-bias playbook so it is the first thing Northstar reads.
    if os.getenv("CUA_KEYBOARD_BIAS", "1") != "0":
        instruction = f"{KEYBOARD_BIAS_PROMPT}\n\n---\n\nTask:\n{instruction}"

    traj = Trajectory(task=task, url=url)
    backend = make_backend(kind=kind)
    loop_breaker = LoopBreaker()

    with backend as b:
        screenshot_url = b.screenshot_url()
        _notify_ui(0, instruction, screenshot_url, status="started", agent_id=agent_id)

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
            _notify_ui(step_idx, instruction, screenshot_url, action, status="proposed", agent_id=agent_id)

            policy = check_action_policy(action, message_text)
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
                    agent_id=agent_id,
                )
                console.print(f"[red]blocked unsafe action:[/red] {policy.reason}")
                break

            loop_breaker.record(action)
            loop_check = loop_breaker.check()
            if not loop_check.passed:
                step.blocked = True
                step.block_reason = loop_check.reason
                traj.error = f"loop detected: {loop_check.reason}"
                _notify_ui(
                    step_idx,
                    instruction,
                    screenshot_url,
                    action,
                    status="loop_detected",
                    blocked=True,
                    block_reason=loop_check.reason,
                )
                console.print(f"[red]loop detected:[/red] {loop_check.reason}")
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
                agent_id=agent_id,
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
