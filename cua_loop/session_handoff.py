"""Session handoff — rescue a stuck model by cloning browser state.

When Northstar enters a loop and can't recover via in-loop nudges, we:
1. Snapshot the current Kernel browser session into a named Profile
   (by marking it save_changes=True, then deleting it so Kernel flushes cookies/storage).
2. Spin up a *new* Kernel browser that restores from that profile.
3. Resume the CUA loop on the fresh browser with a new Lightcone conversation
   thread but with full context about prior steps injected into the first message.

This lets a "second model instance" inherit the exact browser state (logged-in
cookies, page URL, DOM state) from the stuck one without any manual intervention.

Only KernelBackend supports snapshot/restore — LightconeBackend falls back to a
plain restart (no state transfer).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

console = Console()

# How many loop_detected / stuck events before we trigger a handoff
HANDOFF_TRIGGER_THRESHOLD = int(__import__("os").getenv("CUA_HANDOFF_THRESHOLD", "1"))


@dataclass
class HandoffContext:
    """Everything the resumed loop needs to know about prior progress."""
    profile_name: str | None          # Kernel profile name to restore from (None = no state)
    current_url: str | None           # URL the browser was on when stuck
    steps_taken: int = 0              # How many steps the stuck model completed
    stuck_reason: str = ""            # Why handoff was triggered
    prior_step_summary: str = ""      # Human-readable summary injected into next attempt
    handoff_count: int = 0            # How many times we've handed off so far


def _summarize_steps(steps: list[Any]) -> str:
    """Build a compact summary of completed steps for context injection."""
    if not steps:
        return "No actions taken before getting stuck."
    lines = []
    for i, s in enumerate(steps[-10:]):  # last 10 steps only
        args = s.action_args or {}
        coords = ""
        if "x" in args and "y" in args:
            coords = f" at ({args['x']},{args['y']})"
        text_part = ""
        if args.get("text"):
            text_part = f" text={args['text'][:40]!r}"
        url_part = ""
        if args.get("url"):
            url_part = f" url={args['url'][:60]!r}"
        lines.append(f"  step {i+1}: {s.action_type}{coords}{text_part}{url_part}")
    return "Prior steps before handoff:\n" + "\n".join(lines)


def snapshot_kernel_session(backend: Any, profile_name: str) -> bool:
    """
    Snapshot the current KernelBackend browser state into a named Kernel Profile.

    Strategy:
    - We need the browser to be created with save_changes=True. Because Kernel
      doesn't allow changing save_changes on an existing session, we use the
      PATCH /browsers/{id} endpoint to load a newly-created profile into the
      session (only allowed if the session has no profile yet).
    - Then we call `_kernel.browsers.delete(id=...)` which flushes state.
    - The __exit__ cleanup in KernelBackend will try to delete again but will
      get a 404 — that's fine, we catch it there.

    Returns True if snapshot succeeded, False if not supported / failed.
    """
    try:
        from kernel import Kernel

        k = Kernel()

        # 1. Create the profile
        try:
            profile = k.profiles.create(name=profile_name)
            console.print(f"[blue]handoff:[/blue] created profile {profile_name!r} ({profile.id})")
        except Exception as e:
            # Conflict (already exists) is fine — we'll use it.
            if "conflict" in str(e).lower() or "409" in str(e):
                console.print(f"[yellow]handoff:[/yellow] profile {profile_name!r} already exists, reusing")
            else:
                raise

        # 2. Load the profile into the current session with save_changes=True
        sid = backend._sid
        try:
            k.browsers.update(
                id=sid,
                profile={"name": profile_name, "save_changes": True},
            )
            console.print(f"[blue]handoff:[/blue] attached profile to session {sid!r} with save_changes=True")
        except Exception as e:
            # If the session already has a profile this will fail —
            # we still attempt the delete so partial state may be saved.
            console.print(f"[yellow]handoff:[/yellow] could not attach profile to session: {e}")

        # 3. Delete the session — Kernel flushes cookies/storage into the profile on delete.
        k.browsers.delete(id=sid)
        console.print(f"[blue]handoff:[/blue] deleted session {sid!r} — browser state flushed to profile")

        # Mark the backend's session ID as gone so its __exit__ won't double-delete.
        backend._sid = None
        return True

    except Exception as e:
        console.print(f"[red]handoff snapshot failed:[/red] {e}")
        return False


def get_current_url(backend: Any) -> str | None:
    """Try to get the current page URL from the backend."""
    try:
        if hasattr(backend, "execute_js"):
            return backend.execute_js("return window.location.href;")
    except Exception:
        pass
    return None


def build_handoff_instruction(
    original_instruction: str,
    ctx: HandoffContext,
) -> str:
    """
    Build the instruction string for the fresh model instance.

    Injects:
    - What has already been done (step summary)
    - Why the handoff happened
    - The current URL to navigate to first
    - The original task goal
    """
    parts = [
        f"HANDOFF #{ctx.handoff_count}: You are taking over from a previous agent instance "
        f"that got stuck. The browser session state (cookies, localStorage) has been "
        f"transferred to you.",
        "",
        f"ORIGINAL TASK: {original_instruction}",
        "",
        f"WHY HANDOFF: {ctx.stuck_reason}",
        "",
        ctx.prior_step_summary,
        "",
        "INSTRUCTIONS FOR YOU:",
        "- Do NOT repeat what the previous agent was doing (it was stuck).",
        "- Try a completely different approach.",
        "- If the previous agent was clicking repeatedly, use keyboard navigation instead.",
        "- If you see a page that looks like progress has been made, continue from there.",
        "- Complete the original task.",
    ]
    if ctx.current_url:
        parts.insert(2, f"CURRENT URL: {ctx.current_url}")
    return "\n".join(parts)


def make_handoff_backend(
    original_backend: Any,
    ctx: HandoffContext,
    kind: str = "browser",
) -> Any:
    """
    Create a new backend that restores browser state from the snapshot profile.

    Returns a fresh backend instance (not yet entered via __enter__).
    For KernelBackend: passes the profile name so the new session starts with cookies.
    For LightconeBackend: returns a plain new backend (no state transfer possible).
    """
    from cua_loop.backends import make_backend

    if ctx.profile_name and hasattr(original_backend, "_kernel"):
        # KernelBackend — restore from profile
        from cua_loop.backends.kernel_backend import KernelBackend

        class RestoredKernelBackend(KernelBackend):
            """KernelBackend that starts a new session pre-loaded with a profile."""

            def __enter__(self) -> "RestoredKernelBackend":
                from kernel import Kernel
                self._kernel = Kernel()
                browser = self._kernel.browsers.create(
                    profile={"name": ctx.profile_name}
                )
                self._browser = browser
                self._sid = browser.session_id
                console.print(
                    f"[green]handoff:[/green] new session {self._sid!r} restored from profile {ctx.profile_name!r}"
                )
                return self

        return RestoredKernelBackend()

    # Fallback — plain new backend, no state transfer
    console.print("[yellow]handoff:[/yellow] non-Kernel backend — no state transfer, starting fresh")
    return make_backend(kind=kind)
