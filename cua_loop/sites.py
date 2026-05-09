"""Marketplace URL generators for Bargain Radar multi-site fan-out."""

from __future__ import annotations

from typing import Callable
from urllib.parse import quote_plus, urlencode

from pydantic import BaseModel, ConfigDict


class MarketplaceAdapter(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    generator: Callable[..., str]
    requires_login: bool = False
    bot_detection_level: str = "low"


CRAIGSLIST_SUBDOMAINS: dict[str, str] = {
    "sf": "sfbay",
    "san francisco": "sfbay",
    "bay area": "sfbay",
    "sfbay": "sfbay",
    "los angeles": "losangeles",
    "la": "losangeles",
    "new york": "newyork",
    "nyc": "newyork",
    "chicago": "chicago",
    "seattle": "seattle",
    "portland": "portland",
    "austin": "austin",
    "denver": "denver",
    "boston": "boston",
}


def craigslist_url(query: str, max_price: float | None = None, location: str | None = None) -> str:
    subdomain = CRAIGSLIST_SUBDOMAINS.get((location or "").lower().strip(), "sfbay")
    params: dict[str, str] = {"query": query}
    if max_price is not None:
        params["max_price"] = str(int(max_price))
    return f"https://{subdomain}.craigslist.org/search/sss?{urlencode(params)}"


def offerup_url(query: str, max_price: float | None = None, location: str | None = None) -> str:
    params: dict[str, str] = {"q": query}
    if max_price is not None:
        params["price_max"] = str(int(max_price))
    return f"https://offerup.com/search?{urlencode(params)}"


def mercari_url(query: str, max_price: float | None = None, location: str | None = None) -> str:
    params: dict[str, str] = {"keyword": query}
    if max_price is not None:
        params["maxPrice"] = str(int(max_price))
    return f"https://www.mercari.com/search/?{urlencode(params)}"


def ebay_used_url(query: str, max_price: float | None = None, location: str | None = None) -> str:
    params: dict[str, str] = {
        "_nkw": query,
        "LH_ItemCondition": "3000|7000",
    }
    if max_price is not None:
        params["_udhi"] = str(int(max_price))
    return f"https://www.ebay.com/sch/i.html?{urlencode(params, quote_via=quote_plus)}"


def reverb_url(query: str, max_price: float | None = None, location: str | None = None) -> str:
    params: dict[str, str] = {"query": query}
    if max_price is not None:
        params["price_max"] = str(int(max_price))
    return f"https://reverb.com/marketplace?{urlencode(params)}"


def fb_marketplace_url(query: str, max_price: float | None = None, location: str | None = None) -> str:
    params: dict[str, str] = {"query": query}
    if max_price is not None:
        params["maxPrice"] = str(int(max_price))
    return f"https://www.facebook.com/marketplace/category/search/?{urlencode(params)}"


MARKETPLACE_REGISTRY: dict[str, MarketplaceAdapter] = {
    "craigslist": MarketplaceAdapter(
        generator=craigslist_url,
        requires_login=False,
        bot_detection_level="low",
    ),
    "offerup": MarketplaceAdapter(
        generator=offerup_url,
        requires_login=False,
        bot_detection_level="high",
    ),
    "mercari": MarketplaceAdapter(
        generator=mercari_url,
        requires_login=False,
        bot_detection_level="medium",
    ),
    "ebay_used": MarketplaceAdapter(
        generator=ebay_used_url,
        requires_login=False,
        bot_detection_level="high",
    ),
    "reverb": MarketplaceAdapter(
        generator=reverb_url,
        requires_login=False,
        bot_detection_level="low",
    ),
    "fb_marketplace": MarketplaceAdapter(
        generator=fb_marketplace_url,
        requires_login=True,
        bot_detection_level="high",
    ),
}


def generate_all_urls(
    query: str,
    max_price: float | None = None,
    location: str | None = None,
    skip_login_required: bool = False,
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for name, adapter in MARKETPLACE_REGISTRY.items():
        if skip_login_required and adapter.requires_login:
            continue
        urls[name] = adapter.generator(query, max_price, location)
    return urls
