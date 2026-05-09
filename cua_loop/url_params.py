"""Maximally-filtered URL generation per marketplace.

Given a ParsedQuery, produces URLs with all possible filter/sort parameters
baked in so the CUA starts on a pre-filtered results page. Fewer UI
interactions = fewer chances to fail.
"""

from __future__ import annotations

from urllib.parse import quote_plus, urlencode

from cua_loop.query_parser import ParsedQuery


def _craigslist_params(pq: ParsedQuery, location: str | None = None) -> dict[str, str]:
    from cua_loop.sites import CRAIGSLIST_SUBDOMAINS
    params: dict[str, str] = {"query": pq.keywords}
    if pq.max_price is not None:
        params["max_price"] = str(int(pq.max_price))
    params["sort"] = "priceasc"
    params["bundleDuplicates"] = "1"
    params["searchNearby"] = "1"
    if pq.max_distance_mi is not None:
        params["search_distance"] = str(int(pq.max_distance_mi))
    return params


def craigslist_filtered_url(pq: ParsedQuery, location: str | None = None) -> str:
    from cua_loop.sites import CRAIGSLIST_SUBDOMAINS
    subdomain = CRAIGSLIST_SUBDOMAINS.get((location or "").lower().strip(), "sfbay")
    params = _craigslist_params(pq, location)
    return f"https://{subdomain}.craigslist.org/search/sss?{urlencode(params)}"


def _ebay_condition_code(pq: ParsedQuery) -> str:
    """Map condition filters to eBay's LH_ItemCondition codes.

    3000 = Used, 7000 = For parts/not working, 1000 = New, 1500 = Open box,
    2000 = Certified refurbished, 2500 = Seller refurbished.
    """
    filters = {f.lower() for f in pq.condition_filters}
    if "new in box" in filters:
        return "1000"
    if any(k in filters for k in ("like new", "excellent condition", "mint condition")):
        return "3000"
    return "3000|7000"


def ebay_filtered_url(pq: ParsedQuery, location: str | None = None) -> str:
    params: dict[str, str] = {
        "_nkw": pq.keywords,
        "LH_ItemCondition": _ebay_condition_code(pq),
        "_sop": "15",  # Price + Shipping: lowest first
        "LH_BIN": "1",  # Buy It Now only (skip auctions for CUA reliability)
        "rt": "nc",     # Exclude category suggestions
    }
    if pq.max_price is not None:
        params["_udhi"] = str(int(pq.max_price))
    if pq.max_distance_mi is not None:
        params["LH_PrefLoc"] = "99"  # Within specified distance
        params["_sadis"] = str(int(pq.max_distance_mi))
    return f"https://www.ebay.com/sch/i.html?{urlencode(params, quote_via=quote_plus)}"


def mercari_filtered_url(pq: ParsedQuery, location: str | None = None) -> str:
    params: dict[str, str] = {
        "keyword": pq.keywords,
        "order": "price_asc",
        "status": "on_sale",  # Only active listings
    }
    if pq.max_price is not None:
        params["maxPrice"] = str(int(pq.max_price))
    filters = {f.lower() for f in pq.condition_filters}
    if any(k in filters for k in ("like new", "excellent condition", "mint condition")):
        params["itemCondition"] = "2"  # Like New
    elif "new in box" in filters:
        params["itemCondition"] = "1"  # New
    return f"https://www.mercari.com/search/?{urlencode(params)}"


def offerup_filtered_url(pq: ParsedQuery, location: str | None = None) -> str:
    params: dict[str, str] = {
        "q": pq.keywords,
        "sort": "price",  # Price ascending
        "delivery_param": "all",
    }
    if pq.max_price is not None:
        params["price_max"] = str(int(pq.max_price))
    if pq.max_distance_mi is not None:
        params["radius"] = str(int(pq.max_distance_mi))
    return f"https://offerup.com/search?{urlencode(params)}"


def reverb_filtered_url(pq: ParsedQuery, location: str | None = None) -> str:
    params: dict[str, str] = {
        "query": pq.keywords,
        "sort": "price|asc",
        "item_state": "used",
    }
    if pq.max_price is not None:
        params["price_max"] = str(int(pq.max_price))
    return f"https://reverb.com/marketplace?{urlencode(params)}"


def fb_marketplace_filtered_url(pq: ParsedQuery, location: str | None = None) -> str:
    params: dict[str, str] = {
        "query": pq.keywords,
        "sortBy": "price_ascend",
        "itemCondition": "used",
    }
    if pq.max_price is not None:
        params["maxPrice"] = str(int(pq.max_price))
    if pq.max_distance_mi is not None:
        params["radius"] = str(int(pq.max_distance_mi))
    return f"https://www.facebook.com/marketplace/category/search/?{urlencode(params)}"


FILTERED_URL_GENERATORS: dict[str, callable] = {
    "craigslist": craigslist_filtered_url,
    "ebay_used": ebay_filtered_url,
    "mercari": mercari_filtered_url,
    "offerup": offerup_filtered_url,
    "reverb": reverb_filtered_url,
    "fb_marketplace": fb_marketplace_filtered_url,
}


def generate_filtered_url(
    marketplace: str,
    parsed_query: ParsedQuery,
    location: str | None = None,
) -> str | None:
    gen = FILTERED_URL_GENERATORS.get(marketplace)
    if gen is None:
        return None
    return gen(parsed_query, location)


def generate_all_filtered_urls(
    parsed_query: ParsedQuery,
    location: str | None = None,
    skip_login_required: bool = False,
) -> dict[str, str]:
    from cua_loop.sites import MARKETPLACE_REGISTRY
    urls: dict[str, str] = {}
    for name, adapter in MARKETPLACE_REGISTRY.items():
        if skip_login_required and adapter.requires_login:
            continue
        gen = FILTERED_URL_GENERATORS.get(name)
        if gen:
            urls[name] = gen(parsed_query, location)
        else:
            urls[name] = adapter.generator(parsed_query.keywords, parsed_query.max_price, location)
    return urls
