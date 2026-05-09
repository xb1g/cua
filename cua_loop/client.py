"""Single-attempt CUA inner loop.

Northstar drives the brain (via the Lightcone Responses API). The browser
surface is pluggable: Kernel by default, Lightcone-managed as a fallback.
Outer retry / verification logic lives in runner.py.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from rich.console import Console
from cua_loop.action_verifier import LoopBreaker, verify_action_effect
from cua_loop.approval import approval_event, approval_result
from cua_loop.models import get_model_provider, ModelProvider
from cua_loop.validator import get_validator, ValidationResult
from cua_loop.session_handoff import (
    HandoffContext,
    HANDOFF_TRIGGER_THRESHOLD,
    build_handoff_instruction,
    get_current_url,
    make_handoff_backend,
    snapshot_kernel_session,
    _summarize_steps,
)
from cua_loop.backends import BrowserBackend, make_backend
from cua_loop.dom_extractor import extract_listings
from cua_loop.element_annotator import format_element_map, get_interactive_elements
try:
    from cua_loop.pagination import scroll_and_accumulate, _detect_marketplace_from_url
except ImportError:
    scroll_and_accumulate = None  # type: ignore[assignment]
    _detect_marketplace_from_url = None  # type: ignore[assignment]
from cua_loop.marketplace import check_marketplace_action_policy
from cua_loop.security import check_action_policy
from cua_loop.types import Step, Trajectory

console = Console()

_provider: ModelProvider | None = None

def _get_provider() -> ModelProvider:
    global _provider
    if _provider is None:
        _provider = get_model_provider()
    return _provider

DISPLAY_WIDTH = int(os.getenv("CUA_DISPLAY_WIDTH", "1280"))
DISPLAY_HEIGHT = int(os.getenv("CUA_DISPLAY_HEIGHT", "720"))
MAX_STEPS = int(os.getenv("CUA_MAX_STEPS", "40"))
APPROVAL_TIMEOUT = int(os.getenv("AEGIS_APPROVAL_TIMEOUT", "60"))


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes"}

SYSTEM_PROMPT = """\
You are a precise web scraping agent controlling a browser. Follow these rules strictly.

NAVIGATION:
- Prefer keyboard shortcuts over mouse clicks whenever possible.
- Use Ctrl+L to focus the address bar, then type or paste URLs directly.
- Use Tab to move between interactive elements and Enter to activate them.
- Use Ctrl+F to find text on the page.
- Only use mouse clicks when there is no keyboard alternative.
- Dismiss cookie banners, popups, and overlays by pressing Escape.

CLICKING DISCIPLINE:
- Before clicking, confirm the target element is fully loaded and visible on screen.
- If a click produces no visible change, do NOT repeat the same click. Instead try: \
pressing Enter, using Tab to reach the element, scrolling to reveal it, or using a keyboard shortcut.
- Never click the same coordinates more than twice. If it fails twice, switch strategy.

PAGE LOADING:
- After any navigation or click that loads a new page, wait for content to appear before acting.
- Look for loading spinners, skeleton screens, or partial content as signs the page is not ready.
- Do not act on a page that is still loading.

DATA EXTRACTION:
- When you find search results or listing data, extract structured data immediately.
- For each listing extract: title, price, condition, location, URL, seller name, posted date.
- Report extracted data as a JSON array in your final answer.
- Read prices carefully: "$1,200" is twelve hundred, not one hundred twenty.

EFFICIENCY:
- Never navigate through menus or homepages if you can reach the target via direct URL.
- Skip ads, promotional banners, and sponsored content.
- If you are stuck on a page for more than 2 actions, try pressing Escape and then a different approach.\
"""

def _get_tools() -> list[dict[str, Any]]:
    return _get_provider().get_tools(DISPLAY_WIDTH, DISPLAY_HEIGHT)

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


def _notify_ui(step: int, task: str, screenshot_url: str, action: Any = None, channel: str = "", **extra: Any) -> None:
    try:
        url = "http://localhost:8555/update"
        if channel:
            url += f"?channel={channel}"
        httpx.post(
            url,
            json={
                "step": step,
                "task": task,
                "screenshot_url": screenshot_url,
                "action": _action_to_dict(action) if action else {},
                **extra,
            },
            timeout=5.0,
        )
    except Exception:
        pass




def _request_approval(step_idx: int, instruction: str, screenshot_url: str,
                      action: Any, policy_reason: str, channel: str = "") -> bool:
    """Broadcast approval request and block until human responds or timeout."""
    approval_event.clear()
    action_dict = _action_to_dict(action)
    _notify_ui(
        step_idx, instruction, screenshot_url, action,
        channel=channel,
        status="approval_needed",
        blocked=True,
        block_reason=policy_reason,
        approval_pending=action_dict,
    )
    console.print(f"[yellow]awaiting human approval ({APPROVAL_TIMEOUT}s timeout)...[/yellow]")
    got_response = approval_event.wait(timeout=APPROVAL_TIMEOUT)
    if got_response and approval_result.get("approved"):
        console.print("[green]action approved by human[/green]")
        return True
    if got_response:
        console.print("[red]action denied by human[/red]")
    else:
        console.print("[red]approval timed out -- denying by default[/red]")
    return False


_ADDRESS_BAR_Y_THRESHOLD = 55
_STUCK_WINDOW = 3
_STUCK_FORCE_RECOVERY_THRESHOLD = 4


def _action_signature(action: Any) -> tuple[str, int, int]:
    return (
        action.type,
        getattr(action, "x", 0) or 0,
        getattr(action, "y", 0) or 0,
    )


def _is_stuck(history: list[tuple[str, int, int]]) -> bool:
    if len(history) < _STUCK_WINDOW:
        return False
    window = history[-_STUCK_WINDOW:]
    return len(set(window)) == 1


def _denorm(mx: int, my: int) -> tuple[int, int]:
    """Convert model coordinates (0-999 space) to screen pixel coordinates."""
    return int(mx * DISPLAY_WIDTH / 999), int(my * DISPLAY_HEIGHT / 999)


def _try_parse_json(text: str) -> Any:
    """Extract JSON array or object from the model's final message."""
    json_match = re.search(r"\[[\s\S]*\]", text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if isinstance(data, list) and len(data) > 0:
                return data
        except (json.JSONDecodeError, ValueError):
            pass
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if isinstance(data, dict):
                return [data]
        except (json.JSONDecodeError, ValueError):
            pass
    if len(text) > 50 and any(w in text.lower() for w in ("$", "price", "listing", "found")):
        return _llm_extract_listings(text)
    return None


def _llm_extract_listings(text: str) -> list[dict] | None:
    """Use MiniMax to convert natural-language listing descriptions to JSON."""
    try:
        from openai import OpenAI
        api_key = os.getenv("MINIMAX_API_KEY", "")
        base_url = os.getenv("VERIFIER_BASE_URL", "https://api.minimaxi.com/v1")
        model = os.getenv("VERIFIER_MODEL", "MiniMax-M2.7-highspeed")
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": (
                    "Extract product listings from the text below into a JSON array. "
                    "Each item: {\"title\": str, \"price\": str or null, \"condition\": str or null, "
                    "\"url\": str or null, \"seller\": str or null, \"location\": str or null}. "
                    "Respond with ONLY the JSON array, no explanation."
                )},
                {"role": "user", "content": text[:3000]},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        data = json.loads(raw)
        if isinstance(data, list) and len(data) > 0:
            return data
    except Exception:
        pass
    return None


def _execute_action(b: BrowserBackend, action: Any) -> bool:
    """Dispatch a Northstar action onto the browser backend.

    Returns True if the loop should terminate.
    """
    t = action.type
    # Raw model coordinates (0–999 space) — must be denormalized before use.
    mx = getattr(action, "x", 0) or 0
    my = getattr(action, "y", 0) or 0
    x, y = _denorm(mx, my)

    if t == "click" and y < _ADDRESS_BAR_Y_THRESHOLD and getattr(action, "button", "left") == "left":
        b.hotkey("ctrl", "l")
    elif t == "click" and getattr(action, "button", "left") == "right":
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
    channel: str = "",
    skip_safety: bool = False,
    _handoff_ctx: HandoffContext | None = None,
) -> Trajectory:
    """One pass of the CUA loop. No retry. No verification."""
    provider = _get_provider()
    instruction = task
    if url:
        instruction = f"You are already on {url}. {task}"
    instruction = f"{KEYBOARD_BIAS_PROMPT}\n\n{instruction}"
    if extra_context:
        instruction += f"\n\nAdditional context from prior attempts:\n{extra_context}"

    traj = Trajectory(task=task, url=url)
    handoff_ctx = _handoff_ctx  # passed in when we are the "resumed" instance
    if handoff_ctx and handoff_ctx.profile_name:
        backend = make_handoff_backend(None, handoff_ctx, kind=kind)
    else:
        backend = make_backend(kind=kind)
    loop_breaker = LoopBreaker()

    with backend as b:
        if url:
            b.navigate(url)
            if hasattr(b, "wait_for_page_load"):
                b.wait_for_page_load()
            else:
                b.wait(2)
            if hasattr(b, "execute_js"):
                try:
                    actual_url = b.execute_js("return window.location.href;")
                    from urllib.parse import urlparse
                    expected_domain = urlparse(url).hostname or ""
                    actual_domain = urlparse(actual_url).hostname or ""
                    if expected_domain and actual_domain and expected_domain not in actual_domain and actual_domain not in expected_domain:
                        traj.error = f"navigation failed: landed on {actual_domain} instead of {expected_domain}"
                        console.print(f"[red]early abort:[/red] {traj.error}")
                        return traj
                except Exception:
                    pass
        screenshot_url = b.screenshot_url()
        _notify_ui(0, instruction, screenshot_url, channel=channel, status="started")

        first_content: list[dict[str, Any]] = [
            {"type": "input_text", "text": instruction},
            {"type": "input_image", "image_url": screenshot_url, "detail": "auto"},
        ]

        if _flag("AEGIS_PAGE_TEXT_INJECTION"):
            try:
                page_text = b.execute_js(
                    "return document.body ? document.body.innerText.substring(0, 4000) : '';"
                )
                if page_text and len(page_text.strip()) > 20:
                    first_content.append({
                        "type": "input_text",
                        "text": f"[PAGE TEXT CONTENT]\n{page_text.strip()[:4000]}\n[END PAGE TEXT]",
                    })
            except Exception:
                pass

        if _flag("AEGIS_ELEMENT_ANNOTATIONS"):
            try:
                elements = get_interactive_elements(b)
                if elements:
                    elem_text = format_element_map(elements)
                    first_content.append({
                        "type": "input_text",
                        "text": elem_text,
                    })
            except Exception:
                pass

        response = provider.create_response(
            input_messages=[{"role": "user", "content": first_content}],
            tools=_get_tools(),
        )

        action_history: list[tuple[str, int, int]] = []

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
                if message_text:
                    traj.extracted = _try_parse_json(message_text)
                console.print(f"[green]Done at step {step_idx}[/green]: {message_text or ''}")
                break

            action = computer_call.action
            if action is None:
                traj.error = "model returned computer_call with no action"
                console.print(f"[red]step {step_idx}: no action in computer_call[/red]")
                break

            step = Step(
                action_type=action.type,
                action_args=_action_to_dict(action),
                screenshot_url=screenshot_url,
                model_message=message_text,
            )
            traj.steps.append(step)
            console.print(f"[cyan]step {step_idx}[/cyan] {action.type} {_action_to_dict(action)}")
            _notify_ui(step_idx, instruction, screenshot_url, action, channel=channel, status="proposed")

            if not skip_safety:
                policy = check_action_policy(action, message_text)
                if policy.allowed and _flag("AEGIS_MARKETPLACE_MODE"):
                    mp_policy = check_marketplace_action_policy(action, message_text)
                    if not mp_policy.allowed:
                        policy = mp_policy
                if not policy.allowed:
                    step.blocked = True
                    step.block_reason = policy.reason
                    approved = _request_approval(
                        step_idx, instruction, screenshot_url,
                        action, policy.reason, channel=channel,
                    )
                    if approved:
                        step.blocked = False
                        step.block_reason = None
                        _notify_ui(
                            step_idx, instruction, screenshot_url, action,
                            channel=channel, status="approved", blocked=False,
                        )
                    else:
                        traj.error = f"blocked unsafe action (denied): {policy.reason}"
                        _notify_ui(
                            step_idx, instruction, screenshot_url, action,
                            channel=channel, status="denied",
                            blocked=True, block_reason=policy.reason,
                        )
                        console.print(f"[red]blocked unsafe action:[/red] {policy.reason}")
                        break

            sig = _action_signature(action)
            action_history.append(sig)

            stuck = _is_stuck(action_history)
            if stuck and len(action_history) >= _STUCK_FORCE_RECOVERY_THRESHOLD:
                validator = get_validator()
                guidance = validator.guide_when_stuck(
                    task=task,
                    history=[{"type": s.action_type, "args": s.action_args} for s in traj.steps],
                    current_url=traj.url,
                )
                console.print(f"[yellow]validator guidance:[/yellow] {guidance.advice} (confidence={guidance.confidence:.2f})")
                console.print("[red]stuck: forcing Escape + scroll recovery[/red]")
                b.hotkey("Escape")
                b.scroll(0, 3, 640, 400)
                action_history.clear()

            loop_breaker.record(action)
            loop_check = loop_breaker.check()
            if not loop_check.passed:
                if _flag("AEGIS_DOM_EXTRACTION"):
                    marketplace_name = _detect_marketplace_from_url(traj.url) if _detect_marketplace_from_url else None
                    rescue_listings = extract_listings(b, marketplace=marketplace_name)
                    if len(rescue_listings) >= 3:
                        traj.extracted = rescue_listings
                        console.print(f"[green]loop detected but DOM rescue: {len(rescue_listings)} listings on page[/green]")
                        break

                step.blocked = True
                step.block_reason = loop_check.reason
                traj.error = f"loop detected: {loop_check.reason}"
                _notify_ui(
                    step_idx, instruction, screenshot_url, action,
                    status="loop_detected", blocked=True,
                    block_reason=loop_check.reason, channel=channel,
                )
                console.print(f"[red]loop detected:[/red] {loop_check.reason}")

                # ── Session handoff: clone browser state to a fresh instance ──
                prior_handoffs = handoff_ctx.handoff_count if handoff_ctx else 0
                if prior_handoffs < HANDOFF_TRIGGER_THRESHOLD:
                    current_url = get_current_url(b)
                    import uuid, time as _time
                    profile_name = f"aegis-handoff-{int(_time.time())}-{uuid.uuid4().hex[:6]}"
                    console.print(
                        f"[bold yellow]handoff #{prior_handoffs + 1}: "
                        f"snapshotting session → profile {profile_name!r}[/bold yellow]"
                    )
                    snapshot_ok = snapshot_kernel_session(b, profile_name) if hasattr(b, "_kernel") else False
                    new_ctx = HandoffContext(
                        profile_name=profile_name if snapshot_ok else None,
                        current_url=current_url or traj.url,
                        steps_taken=len(traj.steps),
                        stuck_reason=loop_check.reason,
                        prior_step_summary=_summarize_steps(traj.steps),
                        handoff_count=prior_handoffs + 1,
                    )
                    handoff_instruction = build_handoff_instruction(task, new_ctx)
                    console.print("[bold yellow]handoff: resuming on fresh browser...[/bold yellow]")
                    # Resume recursively — the new instance gets the restored browser + context
                    resumed = run_single_attempt(
                        task=task,
                        url=new_ctx.current_url,
                        extra_context=handoff_instruction,
                        kind=kind,
                        channel=channel,
                        skip_safety=skip_safety,
                        _handoff_ctx=new_ctx,
                    )
                    # Merge trajectories: prepend our steps to the resumed run
                    resumed.steps = traj.steps + resumed.steps
                    return resumed

                break

            terminated = _execute_action(b, action)
            if terminated:
                traj.final_message = getattr(action, "result", None) or getattr(action, "text", None)
                break

            if hasattr(b, "wait_for_page_load"):
                b.wait_for_page_load()
            else:
                b.wait(1)
            after_screenshot_url = b.screenshot_url()
            action_check = None
            if not skip_safety:
                action_check = verify_action_effect(action.type, screenshot_url, after_screenshot_url)
                step.after_screenshot_url = after_screenshot_url
                step.verification_passed = action_check.passed
                step.verification_reason = action_check.reason
                _notify_ui(
                    step_idx,
                    instruction,
                    after_screenshot_url,
                    action,
                    channel=channel,
                    status="verified" if action_check.passed else "needs_retry",
                    verification_passed=action_check.passed,
                    verification_reason=action_check.reason,
                )
                if not action_check.passed:
                    console.print(f"[yellow]action verification failed:[/yellow] {action_check.reason}")

            if _flag("AEGIS_LLM_VALIDATION"):
                validator = get_validator()
                val = validator.validate_action(
                    task=task,
                    action_type=action.type,
                    action_args=_action_to_dict(action),
                    screenshot_url=after_screenshot_url,
                    history=[{"type": s.action_type, "args": s.action_args} for s in traj.steps],
                )
                if not val.passed and val.guidance:
                    console.print(f"[yellow]validator:[/yellow] {val.reason} — {val.guidance}")
                    call_output_input.append({
                        "role": "user",
                        "content": f"[SYSTEM VALIDATOR FEEDBACK] {val.reason}. {val.guidance}",
                    })
            else:
                step.after_screenshot_url = after_screenshot_url
                _notify_ui(step_idx, instruction, after_screenshot_url, action, channel=channel, status="running")
            screenshot_url = after_screenshot_url

            call_output_input: list[dict[str, Any]] = [
                {
                    "type": "computer_call_output",
                    "call_id": computer_call.call_id,
                    "output": {
                        "type": "input_image",
                        "image_url": screenshot_url,
                        "detail": "auto",
                    },
                }
            ]
            if not skip_safety and action_check is not None and not action_check.passed and action.type in ("click", "double_click"):
                ax = getattr(action, "x", 0) or 0
                ay = getattr(action, "y", 0) or 0
                call_output_input.append({
                    "role": "user",
                    "content": (
                        f"Your last {action.type} at ({ax},{ay}) had no visible effect. "
                        "Try a different approach: use keyboard navigation (Tab then Enter), "
                        "scroll to reveal the element, or use a keyboard shortcut instead."
                    ),
                })
            if stuck:
                call_output_input.append({
                    "role": "user",
                    "content": (
                        "WARNING: You appear stuck — your last actions were identical with no page change. "
                        "You MUST try a completely different approach: press Escape, scroll the page, "
                        "use keyboard navigation (Tab/Enter), or navigate to a different URL."
                    ),
                })
            if _flag("AEGIS_ELEMENT_ANNOTATIONS"):
                loop_elements = get_interactive_elements(b)
                loop_el_map = format_element_map(loop_elements)
                if loop_el_map:
                    call_output_input.append({
                        "role": "user",
                        "content": loop_el_map,
                    })

            if _flag("AEGIS_DOM_EXTRACTION") and (step_idx > 0 and (step_idx % 5 == 0 or action.type == "scroll")):
                marketplace_name = _detect_marketplace_from_url(traj.url) if _detect_marketplace_from_url else None
                mid_listings = extract_listings(b, marketplace=marketplace_name)
                if len(mid_listings) >= 5:
                    call_output_input.append({
                        "role": "user",
                        "content": (
                            f"[SYSTEM: {len(mid_listings)} listings detected on this page via DOM extraction. "
                            "You may terminate now — the data will be captured automatically after you stop. "
                            "Say 'done' or use the terminate action.]"
                        ),
                    })
                    console.print(f"[blue]mid-loop DOM check:[/blue] {len(mid_listings)} listings found — signaling model to terminate")

            response = provider.create_response(
                input_messages=call_output_input,
                tools=_get_tools(),
                previous_response_id=response.id,
            )
        else:
            traj.error = f"hit MAX_STEPS={MAX_STEPS} without terminating"

        if _flag("AEGIS_DOM_EXTRACTION") and not traj.error:
            if scroll_and_accumulate is not None and _detect_marketplace_from_url is not None:
                marketplace_name = _detect_marketplace_from_url(traj.url)
                dom_listings = scroll_and_accumulate(
                    b, marketplace=marketplace_name, max_pages=3, max_items=60
                )
            else:
                dom_listings = extract_listings(b, marketplace=None)
            if dom_listings:
                if traj.extracted and isinstance(traj.extracted, list):
                    traj.extracted.extend(dom_listings)
                else:
                    traj.extracted = dom_listings
                console.print(f"[green]DOM extraction:[/green] {len(dom_listings)} listings extracted")

    return traj
