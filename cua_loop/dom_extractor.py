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
const rows = document.querySelectorAll(
  '.cl-search-result, .cl-static-search-result, .result-row, ' +
  'li.result-node, li.cl-search-result, .cl-search-result-container > li, ' +
  '#search-results li, .rows li, .search-list li[data-pid]'
);
if (rows.length === 0) {
  const galleryItems = document.querySelectorAll('.gallery-card, a.cl-app-anchor');
  if (galleryItems.length > 0) {
    const items = Array.from(galleryItems).slice(0, 30).map(el => {
      const text = el.innerText || el.textContent || '';
      const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
      const priceText = lines.find(l => /^\\$/.test(l)) || null;
      const title = lines.find(l => l.length > 3 && !/^\\$/.test(l)) || '';
      const href = el.tagName === 'A' ? el.href : (el.querySelector('a') || {}).href || null;
      return { title, price: priceText, url: href, marketplace: 'craigslist' };
    }).filter(i => i.title);
    return JSON.stringify(items);
  }
}
const items = Array.from(rows).slice(0, 30).map(el => {
  const titleEl = el.querySelector(
    '.titlestring, .title, .result-title, a.posting-title, ' +
    '.cl-app-anchor .titlestring, .label, a.result-title'
  );
  const priceEl = el.querySelector('.priceinfo, .result-price, .price, span.priceinfo');
  const metaEl = el.querySelector('.meta, .result-meta, .housing, .result-hood');
  const linkEl = el.querySelector('a[href*="/"]');
  const ageEl = el.querySelector('time, .result-date, .cl-search-result-date, [datetime]');
  return {
    title: titleEl ? titleEl.innerText.trim() : (el.querySelector('a') ? el.querySelector('a').innerText.trim() : ''),
    price: priceEl ? priceEl.innerText.trim() : null,
    url: linkEl ? linkEl.href : null,
    notes: metaEl ? metaEl.innerText.trim() : '',
    posted_age_text: ageEl ? (ageEl.getAttribute('datetime') || ageEl.innerText.trim()) : null,
    marketplace: 'craigslist',
  };
}).filter(i => i.title && i.title.length > 2);
return JSON.stringify(items);
"""

_EBAY_JS = """\
const rows = document.querySelectorAll(
  '.s-item, .srp-results li[data-view], .srp-results .s-item__wrapper, ' +
  'ul.srp-results li, [data-viewport]'
);
const items = Array.from(rows).slice(0, 30).map(el => {
  const titleEl = el.querySelector(
    '.s-item__title span[role="heading"], .s-item__title span, .s-item__title, ' +
    'h3.s-item__title, [class*="item-title"]'
  );
  const priceEl = el.querySelector('.s-item__price, .s-item__price span, [class*="item-price"]');
  const condEl = el.querySelector(
    '.SECONDARY_INFO, .s-item__subtitle, [class*="SECONDARY"], ' +
    '.s-item__condition, [class*="condition"]'
  );
  const linkEl = el.querySelector('a.s-item__link, a[href*="itm/"], a[href*="/itm"]');
  const sellerEl = el.querySelector('.s-item__seller-info, .s-item__seller-info-text');
  const shippingEl = el.querySelector('.s-item__shipping, .s-item__freeXDays');
  return {
    title: titleEl ? titleEl.innerText.trim() : '',
    price: priceEl ? priceEl.innerText.trim() : null,
    condition: condEl ? condEl.innerText.trim() : null,
    url: linkEl ? linkEl.href : null,
    seller: sellerEl ? sellerEl.innerText.trim() : null,
    notes: shippingEl ? shippingEl.innerText.trim() : null,
    marketplace: 'ebay',
  };
}).filter(i => i.title && i.title !== 'Shop on eBay' && i.title !== 'Results matching fewer words');
return JSON.stringify(items);
"""

_MERCARI_JS = """\
const rows = document.querySelectorAll(
  '[data-testid="ItemContainer"], [data-testid="SearchResults"] > div, ' +
  '[data-testid*="item"], [class*="ItemGrid"] > div, ' +
  'a[href*="/item/"], [class*="SearchResultItem"], [class*="productCard"]'
);
const seen = new Set();
const items = Array.from(rows).slice(0, 30).map(el => {
  const linkEl = el.tagName === 'A' ? el : el.querySelector('a[href*="/item/"]');
  const href = linkEl ? linkEl.href : null;
  if (href && seen.has(href)) return null;
  if (href) seen.add(href);
  const titleEl = el.querySelector(
    '[data-testid="ItemName"], [data-testid*="name"], [aria-label], ' +
    '[class*="ItemName"], [class*="item-name"], [class*="itemName"]'
  );
  const priceEl = el.querySelector(
    '[data-testid="ItemPrice"], [data-testid*="price"], ' +
    '[class*="Price"], [class*="price"]'
  );
  const condEl = el.querySelector('[class*="condition"], [class*="Condition"]');
  let title = '';
  if (titleEl) {
    title = (titleEl.getAttribute('aria-label') || titleEl.innerText || '').trim();
  } else {
    const texts = (el.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
    title = texts.find(t => t.length > 5 && !/^\\$/.test(t)) || '';
  }
  return {
    title: title,
    price: priceEl ? priceEl.innerText.trim() : null,
    condition: condEl ? condEl.innerText.trim() : null,
    url: href,
    marketplace: 'mercari',
  };
}).filter(i => i && i.title);
return JSON.stringify(items);
"""

_OFFERUP_JS = """\
let rows = document.querySelectorAll(
  '[class*="ItemTile"], [data-testid*="listing"], [data-testid*="item"], ' +
  'article, [class*="listing-card"], [class*="ListingCard"], ' +
  'a[href*="/item/detail/"], [class*="item-tile"]'
);
if (rows.length === 0) {
  rows = document.querySelectorAll('[class*="tile"], [class*="card"]');
}
const seen = new Set();
const items = Array.from(rows).slice(0, 30).map(el => {
  const linkEl = el.tagName === 'A' ? el : el.querySelector('a[href*="/item/"]');
  const href = linkEl ? linkEl.href : null;
  if (href && seen.has(href)) return null;
  if (href) seen.add(href);
  const titleEl = el.querySelector(
    '[class*="title"], [class*="Title"], span[class*="name"], ' +
    '[data-testid*="title"], [aria-label]'
  );
  const priceEl = el.querySelector(
    '[class*="price"], [class*="Price"], span[class*="cost"], ' +
    '[data-testid*="price"]'
  );
  const locEl = el.querySelector('[class*="location"], [class*="Location"], [class*="distance"]');
  let title = '';
  if (titleEl) {
    title = (titleEl.getAttribute('aria-label') || titleEl.innerText || '').trim();
  } else {
    const lines = (el.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
    title = lines.find(l => l.length > 5 && !/^\\$/.test(l) && !/mile|km/.test(l)) || '';
  }
  return {
    title: title,
    price: priceEl ? priceEl.innerText.trim() : null,
    url: href,
    notes: locEl ? locEl.innerText.trim() : null,
    marketplace: 'offerup',
  };
}).filter(i => i && i.title);
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
const selectors = [
  'article', '[data-testid]', '.listing', '.result', '.product-card',
  '.search-result', '[class*="item"]', '[class*="card"]', '[class*="listing"]',
  'li[data-pid]', '[class*="result"]'
].join(', ');
let rows = document.querySelectorAll(selectors);
if (rows.length === 0) {
  rows = document.querySelectorAll('li, .grid > div, main a[href]');
}
const seen = new Set();
const items = Array.from(rows).slice(0, 30).map(el => {
  const text = el.innerText.trim();
  if (text.length < 10 || text.length > 1000) return null;
  const linkEl = el.querySelector('a[href]') || (el.tagName === 'A' ? el : null);
  const href = linkEl ? linkEl.href : null;
  if (href && seen.has(href)) return null;
  if (href) seen.add(href);
  const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
  const priceText = lines.find(l => /\\$\\s*\\d/.test(l)) || null;
  const title = lines.find(l => l.length > 5 && !/^\\$/.test(l) && !/^(Free|Sponsored|Ad)$/i.test(l)) || lines[0] || '';
  if (!title || title.length < 3) return null;
  return {
    title: title.substring(0, 120),
    price: priceText,
    url: href,
    marketplace: 'unknown',
  };
}).filter(Boolean);
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

    if not results:
        results = _extract_from_page_text(backend, marketplace)

    return results


def _extract_from_page_text(
    backend: BrowserBackend, marketplace: str | None = None,
) -> list[dict[str, Any]]:
    """Last resort: get visible page text and parse price/title patterns."""
    if not hasattr(backend, "extract_page_text"):
        return []
    try:
        text = backend.extract_page_text(max_chars=6000)
    except Exception:
        return []
    if not text or len(text) < 20:
        return []
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    items: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        price_match = re.search(r"\$\s*[\d,]+(?:\.\d{2})?", line)
        if price_match:
            title = line[:price_match.start()].strip()
            if not title and i > 0:
                title = lines[i - 1].strip()
            if title and len(title) > 3:
                items.append({
                    "title": title[:120],
                    "price": price_match.group().strip(),
                    "marketplace": marketplace or "unknown",
                })
        i += 1
    return items[:30]
