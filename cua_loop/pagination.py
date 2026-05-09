"""Scroll-and-accumulate: JS-driven pagination for marketplace extraction.

Replaces error-prone CUA "click Next page" with deterministic scroll/navigate
loops. Handles infinite-scroll sites (OfferUp, Mercari) and URL-paginated
sites (Craigslist, eBay, Reverb).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from cua_loop.backends import BrowserBackend
from cua_loop.dom_extractor import extract_listings


@dataclass(frozen=True)
class PaginationStrategy:
    type: Literal["infinite_scroll", "url_pagination"]
    page_param: str | None = None
    offset_param: str | None = None
    page_size: int = 25


MARKETPLACE_PAGINATION: dict[str, PaginationStrategy] = {
    "craigslist": PaginationStrategy(type="url_pagination", offset_param="s", page_size=120),
    "ebay": PaginationStrategy(type="url_pagination", page_param="_pgn", page_size=60),
    "reverb": PaginationStrategy(type="url_pagination", page_param="page", page_size=25),
    "mercari": PaginationStrategy(type="infinite_scroll"),
    "offerup": PaginationStrategy(type="infinite_scroll"),
    "fb": PaginationStrategy(type="infinite_scroll"),
}


def _item_key(item: dict[str, Any]) -> str:
    url = item.get("url")
    if url:
        return url
    title = (item.get("title") or "").strip().lower()
    price = str(item.get("price") or "")
    return hashlib.md5(f"{title}:{price}".encode()).hexdigest()


def _build_page_url(base_url: str, strategy: PaginationStrategy, page_index: int) -> str:
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if strategy.page_param:
        params[strategy.page_param] = [str(page_index + 1)]
    elif strategy.offset_param:
        params[strategy.offset_param] = [str(page_index * strategy.page_size)]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def _detect_marketplace_from_url(url: str | None) -> str | None:
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


def _infinite_scroll_accumulate(
    backend: BrowserBackend,
    marketplace: str | None,
    max_scrolls: int = 5,
    max_items: int = 60,
) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for _ in range(max_scrolls):
        page_items = extract_listings(backend, marketplace=marketplace)
        new_count = 0
        for item in page_items:
            key = _item_key(item)
            if key not in seen_keys:
                seen_keys.add(key)
                all_items.append(item)
                new_count += 1
        if new_count == 0:
            break
        if len(all_items) >= max_items:
            break
        try:
            backend.execute_js("window.scrollBy(0, window.innerHeight);")
            backend.wait_for_page_load(timeout_ms=3000)
        except Exception:
            break

    return all_items[:max_items]


def _paginated_accumulate(
    backend: BrowserBackend,
    base_url: str,
    marketplace: str,
    strategy: PaginationStrategy,
    max_pages: int = 3,
    max_items: int = 60,
) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for page_idx in range(max_pages):
        if page_idx > 0:
            next_url = _build_page_url(base_url, strategy, page_idx)
            try:
                backend.navigate(next_url)
                backend.wait_for_page_load()
            except Exception:
                break

        page_items = extract_listings(backend, marketplace=marketplace)
        new_count = 0
        for item in page_items:
            key = _item_key(item)
            if key not in seen_keys:
                seen_keys.add(key)
                all_items.append(item)
                new_count += 1
        if new_count == 0:
            break
        if len(all_items) >= max_items:
            break

    return all_items[:max_items]


def scroll_and_accumulate(
    backend: BrowserBackend,
    marketplace: str | None = None,
    max_pages: int = 3,
    max_items: int = 60,
) -> list[dict[str, Any]]:
    """Accumulate listings across pages/scrolls for a marketplace."""
    if marketplace is None:
        try:
            current_url = backend.execute_js("return window.location.href;")
            marketplace = _detect_marketplace_from_url(current_url)
        except Exception:
            pass

    strategy = MARKETPLACE_PAGINATION.get(marketplace or "")

    if strategy is None or strategy.type == "infinite_scroll":
        return _infinite_scroll_accumulate(
            backend, marketplace, max_scrolls=max_pages + 2, max_items=max_items
        )

    try:
        base_url = backend.execute_js("return window.location.href;")
    except Exception:
        return _infinite_scroll_accumulate(backend, marketplace, max_items=max_items)

    return _paginated_accumulate(
        backend, base_url, marketplace, strategy,
        max_pages=max_pages, max_items=max_items,
    )
