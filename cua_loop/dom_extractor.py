"""Hybrid DOM extraction via KERNEL's Playwright bridge.

Instead of relying solely on Northstar's visual screenshot parsing (error-prone),
this module injects JS into the page DOM to directly extract structured listing
data. 100% accurate for supported marketplace layouts vs visual parsing.

Usage: call extract_listings(backend) after the CUA has navigated to a results
page. Returns a list of dicts ready for coerce_marketplace_listing().
"""

from __future__ import annotations

import json
from typing import Any

from cua_loop.backends import BrowserBackend

# ── Per-marketplace extraction scripts ───────────────────────────────────────
# Each returns a JSON array of {title, price, condition, url, seller, posted_age_text, ...}

_CRAIGSLIST_JS = """\
const rows = document.querySelectorAll('.cl-static-search-result, .result-row, li.result-node');
const items = Array.from(rows).slice(0, 30).map(el => {
  const titleEl = el.querySelector('.titlestring, .result-title, a.posting-title');
  const priceEl = el.querySelector('.priceinfo, .result-price');
  const metaEl = el.querySelector('.meta, .result-meta');
  const linkEl = el.querySelector('a[href]');
  return {
    title: titleEl ? titleEl.innerText.trim() : '',
    price: priceEl ? priceEl.innerText.trim() : null,
    url: linkEl ? linkEl.href : null,
    notes: metaEl ? metaEl.innerText.trim() : '',
    marketplace: 'craigslist',
  };
}).filter(i => i.title);
return JSON.stringify(items);
"""

_EBAY_JS = """\
const rows = document.querySelectorAll('.s-item, .srp-results li[data-view]');
const items = Array.from(rows).slice(0, 30).map(el => {
  const titleEl = el.querySelector('.s-item__title, .s-item__title span');
  const priceEl = el.querySelector('.s-item__price');
  const condEl = el.querySelector('.SECONDARY_INFO, .s-item__subtitle');
  const linkEl = el.querySelector('a.s-item__link');
  const sellerEl = el.querySelector('.s-item__seller-info, .s-item__seller-info-text');
  return {
    title: titleEl ? titleEl.innerText.trim() : '',
    price: priceEl ? priceEl.innerText.trim() : null,
    condition: condEl ? condEl.innerText.trim() : null,
    url: linkEl ? linkEl.href : null,
    seller: sellerEl ? sellerEl.innerText.trim() : null,
    marketplace: 'ebay',
  };
}).filter(i => i.title && i.title !== 'Shop on eBay');
return JSON.stringify(items);
"""

_MERCARI_JS = """\
const rows = document.querySelectorAll('[data-testid="ItemContainer"], [data-testid="SearchResults"] > div');
const items = Array.from(rows).slice(0, 30).map(el => {
  const titleEl = el.querySelector('[data-testid="ItemName"], [aria-label]');
  const priceEl = el.querySelector('[data-testid="ItemPrice"], [class*="Price"]');
  const linkEl = el.querySelector('a[href*="/item/"]');
  return {
    title: titleEl ? (titleEl.getAttribute('aria-label') || titleEl.innerText).trim() : '',
    price: priceEl ? priceEl.innerText.trim() : null,
    url: linkEl ? linkEl.href : null,
    marketplace: 'mercari',
  };
}).filter(i => i.title);
return JSON.stringify(items);
"""

_OFFERUP_JS = """\
const rows = document.querySelectorAll('[class*="ItemTile"], [data-testid*="listing"], article');
const items = Array.from(rows).slice(0, 30).map(el => {
  const titleEl = el.querySelector('[class*="title"], span[class*="Title"]');
  const priceEl = el.querySelector('[class*="price"], span[class*="Price"]');
  const linkEl = el.querySelector('a[href*="/item/"]');
  return {
    title: titleEl ? titleEl.innerText.trim() : '',
    price: priceEl ? priceEl.innerText.trim() : null,
    url: linkEl ? linkEl.href : null,
    marketplace: 'offerup',
  };
}).filter(i => i.title);
return JSON.stringify(items);
"""

_REVERB_JS = """\
const rows = document.querySelectorAll('.rc-listing-card, [class*="ListingCard"], .grid-card');
const items = Array.from(rows).slice(0, 30).map(el => {
  const titleEl = el.querySelector('[class*="title"], .rc-listing-card__title');
  const priceEl = el.querySelector('[class*="price"], .rc-listing-card__price');
  const condEl = el.querySelector('[class*="condition"]');
  const linkEl = el.querySelector('a[href*="/item/"]');
  return {
    title: titleEl ? titleEl.innerText.trim() : '',
    price: priceEl ? priceEl.innerText.trim() : null,
    condition: condEl ? condEl.innerText.trim() : null,
    url: linkEl ? linkEl.href : null,
    marketplace: 'reverb',
  };
}).filter(i => i.title);
return JSON.stringify(items);
"""

_FB_MARKETPLACE_JS = """\
const rows = document.querySelectorAll('[class*="x1lliihq"] a[href*="/marketplace/item/"]');
const seen = new Set();
const items = Array.from(rows).slice(0, 30).map(el => {
  const container = el.closest('[class*="x1lliihq"]') || el;
  const texts = container.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
  const href = el.href;
  if (seen.has(href)) return null;
  seen.add(href);
  const priceText = texts.find(t => /^\\$/.test(t)) || null;
  const title = texts.find(t => !/^\\$/.test(t) && t.length > 5) || texts[0] || '';
  return { title, price: priceText, url: href, marketplace: 'fb' };
}).filter(Boolean);
return JSON.stringify(items);
"""

_GENERIC_JS = """\
const selectors = 'article, [data-testid], .listing, .result, .product-card, .search-result';
const rows = document.querySelectorAll(selectors);
const items = Array.from(rows).slice(0, 20).map(el => {
  const text = el.innerText.trim();
  const linkEl = el.querySelector('a[href]');
  const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
  const priceText = lines.find(l => /\\$\\d/.test(l)) || null;
  const title = lines.find(l => l.length > 5 && !/^\\$/.test(l)) || lines[0] || '';
  return {
    title: title,
    price: priceText,
    url: linkEl ? linkEl.href : null,
    marketplace: 'unknown',
  };
}).filter(i => i.title);
return JSON.stringify(items);
"""

MARKETPLACE_EXTRACTORS: dict[str, str] = {
    "craigslist": _CRAIGSLIST_JS,
    "ebay": _EBAY_JS,
    "mercari": _MERCARI_JS,
    "offerup": _OFFERUP_JS,
    "reverb": _REVERB_JS,
    "fb": _FB_MARKETPLACE_JS,
}


def _run_js(backend: BrowserBackend, js_code: str) -> list[dict[str, Any]]:
    """Execute JS via the backend's Playwright bridge, parse the JSON result."""
    if not hasattr(backend, "execute_js"):
        return []
    try:
        raw = backend.execute_js(js_code)
    except Exception:
        return []
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def extract_listings(
    backend: BrowserBackend,
    marketplace: str | None = None,
) -> list[dict[str, Any]]:
    """Extract structured listing data from the current page via DOM injection.

    Tries the marketplace-specific extractor first. Falls back to a generic
    extractor if no marketplace is specified or the specific one returns nothing.
    """
    results: list[dict[str, Any]] = []

    if marketplace:
        key = marketplace.lower().replace("facebook", "fb").replace("facebook_marketplace", "fb")
        js = MARKETPLACE_EXTRACTORS.get(key)
        if js:
            results = _run_js(backend, js)

    if not results:
        results = _run_js(backend, _GENERIC_JS)

    return results
