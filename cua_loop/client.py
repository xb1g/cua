"""Single-attempt CUA inner loop around Lightcone Northstar.

Returns a Trajectory. Outer retry / verification logic lives in runner.py.
"""

from __future__ import annotations

import os
import httpx
from typing import Any

from rich.console import Console
from tzafon import Lightcone

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

def _notify_ui(step: int, task: str, screenshot_url: str, action: Any = None):
    try:
        httpx.post("http://localhost:8000/update", json={
            "step": step,
            "task": task,
            "screenshot_url": screenshot_url,
            "action": _action_to_dict(action) if action else {}
        }, timeout=0.2)
    except Exception:
        pass

def _execute_action(computer: Any, action: Any) -> bool:
    """Dispatch a Northstar action onto the Lightcone computer.

    Returns True if the loop should terminate.
    """
    t = action.type
    if t == "click" and getattr(action, "button", "left") == "right":
        computer.right_click(action.x, action.y)
    elif t == "click":
        computer.click(action.x, action.y)
    elif t == "double_click":
        computer.double_click(action.x, action.y)
    elif t == "type":
        computer.type(action.text)
    elif t in ("key", "keypress"):
        computer.hotkey(*action.keys)
    elif t == "scroll":
        computer.scroll(0, action.scroll_y or 0, action.x or 640, action.y or 400)
    elif t == "hscroll":
        computer.scroll(action.scroll_x or 0, 0, action.x or 640, action.y or 400)
    elif t == "drag":
        computer.drag(action.x, action.y, action.end_x, action.end_y)
    elif t == "navigate":
        computer.navigate(action.url)
    elif t == "wait":
        computer.wait(2)
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
    client = Lightcone()
    instruction = task
    if url:
        instruction = f"Go to {url}. Then: {task}"
    if extra_context:
        instruction += f"\n\nAdditional context from prior attempts:\n{extra_context}"

    traj = Trajectory(task=task, url=url)

    with client.computer.create(kind=kind) as computer:
        screenshot = computer.screenshot()
        screenshot_url = computer.get_screenshot_url(screenshot)
        _notify_ui(0, instruction, screenshot_url)

        response = client.responses.create(
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
            traj.steps.append(
                Step(
                    action_type=action.type,
                    action_args=_action_to_dict(action),
                    screenshot_url=screenshot_url,
                    model_message=message_text,
                )
            )
            console.print(f"[cyan]step {step_idx}[/cyan] {action.type} {_action_to_dict(action)}")
            _notify_ui(step_idx, instruction, screenshot_url, action)

            terminated = _execute_action(computer, action)
            if terminated:
                traj.final_message = getattr(action, "result", None) or getattr(action, "text", None)
                break

            computer.wait(1)
            screenshot = computer.screenshot()
            screenshot_url = computer.get_screenshot_url(screenshot)

            response = client.responses.create(
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
