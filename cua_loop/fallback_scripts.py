"""Fallback programmatic extraction -- deterministic Playwright scripts.

When the CUA fails after max_attempts, this module navigates to the search URL,
waits for page load, scrolls to accumulate results, and extracts via DOM.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from rich.console import Console

from cua_loop.backends import make_backend
from cua_loop.dom_extractor import extract_listings
from cua_loop.pagination import scroll_and_accumulate

console = Console()

_FALLBACK_ENABLED = os.getenv("AEGIS_FALLBACK_EXTRACTION", "true").lower() in {"1", "true", "yes"}


def _detect_marketplace(url: str) -> str | None:
    if not url:
        return None
    host = urlparse(url).hostname or ""
    if "craigslist" in host:
        return "craigslist"
    if "ebay" in host:
        return "ebay"
    if "mercari" in host:
        return "mercari"
    if "offerup" in host:
        return "offerup"
    if "reverb" in host:
        return "reverb"
    if "facebook" in host:
        return "fb"
    return None


def run_fallback_extraction(
    url: str,
    marketplace: str | None = None,
    kind: str = "browser",
    max_pages: int = 2,
    max_items: int = 40,
) -> list[dict[str, Any]]:
    """Navigate to URL and extract listings deterministically. No LLM."""
    if not _FALLBACK_ENABLED:
        return []
    if not url:
        return []

    if marketplace is None:
        marketplace = _detect_marketplace(url)

    console.print(f"[yellow]fallback extraction:[/yellow] {marketplace or 'generic'} -> {url[:80]}")

    backend = make_backend(kind=kind)
    try:
        with backend as b:
            b.navigate(url)
            if hasattr(b, "wait_for_page_load"):
                b.wait_for_page_load()
            else:
                b.wait(3)

            _dismiss_overlays(b)
            _wait_for_listings(b, marketplace)

            listings = scroll_and_accumulate(
                b, marketplace=marketplace, max_pages=max_pages, max_items=max_items
            )

            if listings:
                console.print(f"[green]fallback extracted {len(listings)} listings[/green]")
            else:
                console.print("[yellow]fallback extraction: no listings found[/yellow]")

            return listings
    except Exception as exc:
        console.print(f"[red]fallback extraction failed:[/red] {exc}")
        return []


_LISTING_WAIT_SELECTORS: dict[str, str] = {
    "craigslist": ".cl-static-search-result, .result-row, li.result-node, .cl-search-result",
    "ebay": ".s-item, .srp-results li",
    "mercari": "[data-testid='ItemContainer'], [data-testid='SearchResults']",
    "offerup": "[class*='ItemTile'], article",
    "reverb": ".rc-listing-card, .grid-card",
}


def _wait_for_listings(backend: BrowserBackend, marketplace: str | None) -> None:
    sel = _LISTING_WAIT_SELECTORS.get(marketplace or "")
    if not sel:
        return
    poll_js = f"""
    for (let i = 0; i < 16; i++) {{
        if (document.querySelector({sel!r})) return 'found';
        await new Promise(r => setTimeout(r, 500));
    }}
    return 'timeout';
    """
    try:
        if hasattr(backend, "_exec_pw"):
            backend._exec_pw(f"return await page.evaluate(async () => {{ {poll_js} }});")
        elif hasattr(backend, "execute_js"):
            backend.execute_js(poll_js)
        else:
            import time
            time.sleep(3)
    except Exception:
        pass


def _dismiss_overlays(backend) -> None:
    """Try to dismiss cookie banners and popups."""
    dismiss_js = """\
    const dismissers = document.querySelectorAll(
      '[class*="cookie"] button, [class*="consent"] button, ' +
      '[class*="banner"] button[class*="close"], [class*="modal"] button[class*="close"], ' +
      '[aria-label="Close"], [aria-label="Dismiss"], button[class*="dismiss"]'
    );
    for (const btn of dismissers) {
      if (btn.offsetParent !== null) { btn.click(); break; }
    }
    return 'done';
    """
    try:
        backend.execute_js(dismiss_js)
    except Exception:
        pass
