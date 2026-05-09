"""Adaptive mode cascade — per-branch extraction strategy selection.

Instead of always running full CUA → verify → retry, the cascade makes
real-time decisions per branch:

  Phase 1: CUA attempt (max 10 steps)
    → mid-loop DOM finds 5+ listings → SUCCESS
    → loop detector kills it → DOM rescue
    → CUA produces 0 actions → skip to Phase 2

  Phase 2: DOM-only extraction (no CUA, just JS)
    → navigate, wait, extract_listings()
    → 3+ listings → SUCCESS
    → 0 → Phase 3

  Phase 3: Fallback Playwright script
    → scroll_and_accumulate with pagination
    → whatever it gets is the final result
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from rich.console import Console

from cua_loop.backends import BrowserBackend, make_backend
from cua_loop.dom_extractor import extract_listings

try:
    from cua_loop.pagination import scroll_and_accumulate
except ImportError:
    scroll_and_accumulate = None  # type: ignore[assignment]

console = Console()

CASCADE_CUA_MAX_STEPS = int(os.getenv("CASCADE_CUA_STEPS", "10"))
CASCADE_DOM_MIN_LISTINGS = int(os.getenv("CASCADE_DOM_MIN", "3"))
CASCADE_EARLY_EXIT_THRESHOLD = int(os.getenv("CASCADE_EARLY_EXIT", "5"))


@dataclass
class CascadeResult:
    listings: list[dict[str, Any]] = field(default_factory=list)
    phase_used: Literal["cua", "dom_only", "fallback"] = "fallback"
    steps_used: int = 0
    duration_s: float = 0.0


def _phase1_cua(backend: BrowserBackend, url: str, task: str, marketplace: str | None) -> CascadeResult | None:
    """Phase 1: Short CUA attempt with early DOM exit."""
    from cua_loop.client import run_single_attempt

    started = time.time()
    saved_max = os.environ.get("CUA_MAX_STEPS")
    os.environ["CUA_MAX_STEPS"] = str(CASCADE_CUA_MAX_STEPS)

    try:
        traj = run_single_attempt(task=task, url=url, skip_safety=True)
    except Exception as exc:
        console.print(f"[yellow]cascade phase 1 error:[/yellow] {exc}")
        return None
    finally:
        if saved_max is None:
            os.environ.pop("CUA_MAX_STEPS", None)
        else:
            os.environ["CUA_MAX_STEPS"] = saved_max

    steps = len(traj.steps)
    duration = time.time() - started

    if traj.extracted and isinstance(traj.extracted, list) and len(traj.extracted) >= CASCADE_EARLY_EXIT_THRESHOLD:
        console.print(f"[green]cascade phase 1: CUA extracted {len(traj.extracted)} listings in {steps} steps[/green]")
        return CascadeResult(
            listings=traj.extracted,
            phase_used="cua",
            steps_used=steps,
            duration_s=duration,
        )

    if steps == 0:
        return None

    return None


def _phase2_dom_only(backend: BrowserBackend, url: str, marketplace: str | None) -> CascadeResult | None:
    """Phase 2: DOM-only extraction — no CUA, just navigate and extract."""
    started = time.time()
    try:
        backend.navigate(url)
        if hasattr(backend, "wait_for_page_load"):
            backend.wait_for_page_load()
        else:
            backend.wait(3)

        listings = extract_listings(backend, marketplace=marketplace)

        if len(listings) >= CASCADE_DOM_MIN_LISTINGS:
            console.print(f"[green]cascade phase 2: DOM extracted {len(listings)} listings[/green]")
            return CascadeResult(
                listings=listings,
                phase_used="dom_only",
                steps_used=0,
                duration_s=time.time() - started,
            )
    except Exception as exc:
        console.print(f"[yellow]cascade phase 2 error:[/yellow] {exc}")

    return None


def _phase3_fallback(backend: BrowserBackend, url: str, marketplace: str | None) -> CascadeResult:
    """Phase 3: Fallback — scroll_and_accumulate or basic extract_listings."""
    started = time.time()
    listings: list[dict[str, Any]] = []

    try:
        backend.navigate(url)
        if hasattr(backend, "wait_for_page_load"):
            backend.wait_for_page_load()
        else:
            backend.wait(3)

        if scroll_and_accumulate is not None:
            listings = scroll_and_accumulate(
                backend, marketplace=marketplace, max_pages=3, max_items=60
            )
        else:
            listings = extract_listings(backend, marketplace=marketplace)
    except Exception as exc:
        console.print(f"[yellow]cascade phase 3 error:[/yellow] {exc}")

    if listings:
        console.print(f"[green]cascade phase 3: fallback extracted {len(listings)} listings[/green]")
    else:
        console.print("[red]cascade phase 3: no listings extracted[/red]")

    return CascadeResult(
        listings=listings,
        phase_used="fallback",
        steps_used=0,
        duration_s=time.time() - started,
    )


def cascade_extract(
    url: str,
    task: str,
    marketplace: str | None = None,
    kind: str = "browser",
) -> CascadeResult:
    """Run the adaptive extraction cascade for a single branch.

    Tries CUA first (fast, 10 steps max), then DOM-only, then fallback.
    Returns the first phase that produces sufficient listings.
    """
    backend = make_backend(kind=kind)

    with backend as b:
        result = _phase1_cua(b, url, task, marketplace)
        if result is not None:
            return result

        result = _phase2_dom_only(b, url, marketplace)
        if result is not None:
            return result

        return _phase3_fallback(b, url, marketplace)
