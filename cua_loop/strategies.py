"""Strategy diversification for wide-scaling branches.

Each branch gets a different search strategy so parallel CUA attempts
explore the marketplace differently. One might sort by price, another
by newest, another browses by category. This maximizes the chance that
at least one branch lands on the best results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class SearchStrategy:
    name: str
    modify_instruction: Callable[[str, str], str]
    modify_url: Callable[[str], str] | None = None
    priority: int = 0


def _add_url_params(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    for k, v in params.items():
        existing[k] = [v]
    new_query = urlencode({k: v[0] for k, v in existing.items()})
    return urlunparse(parsed._replace(query=new_query))


def _keyword_search(task: str, url: str) -> str:
    return task


def _category_browse(task: str, url: str) -> str:
    return (
        f"{task}\n\n"
        "STRATEGY: Instead of using the search bar, navigate to the most relevant "
        "category page first, then browse listings within that category. "
        "Look for category links in the sidebar or navigation menu."
    )


def _price_filter_first(task: str, url: str) -> str:
    return (
        f"{task}\n\n"
        "STRATEGY: Before scrolling or reading results, apply the price filter first. "
        "Look for a 'Price' dropdown, 'Max price' input, or sort-by-price option. "
        "Set the maximum price constraint, THEN read the filtered results."
    )


def _sort_newest(task: str, url: str) -> str:
    return (
        f"{task}\n\n"
        "STRATEGY: Sort results by 'Newest first' or 'Most recent' before extracting. "
        "Look for a sort dropdown and select the newest/most-recent option. "
        "Fresh listings are more likely to still be available."
    )


def _sort_newest_url(url: str) -> str:
    host = urlparse(url).hostname or ""
    if "craigslist" in host:
        return _add_url_params(url, {"sort": "date"})
    if "ebay" in host:
        return _add_url_params(url, {"_sop": "10"})
    if "mercari" in host:
        return _add_url_params(url, {"sortBy": "1"})
    if "offerup" in host:
        return _add_url_params(url, {"sort": "-posted"})
    if "reverb" in host:
        return _add_url_params(url, {"sort": "published_at|desc"})
    return url


def _sort_cheapest(task: str, url: str) -> str:
    return (
        f"{task}\n\n"
        "STRATEGY: Sort results by price (lowest first) before extracting. "
        "This surfaces the best deals at the top of the results page."
    )


def _sort_cheapest_url(url: str) -> str:
    host = urlparse(url).hostname or ""
    if "craigslist" in host:
        return _add_url_params(url, {"sort": "priceasc"})
    if "ebay" in host:
        return _add_url_params(url, {"_sop": "15"})
    if "mercari" in host:
        return _add_url_params(url, {"sortBy": "2"})
    if "offerup" in host:
        return _add_url_params(url, {"sort": "price_asc"})
    if "reverb" in host:
        return _add_url_params(url, {"sort": "price|asc"})
    return url


def _broad_then_narrow(task: str, url: str) -> str:
    return (
        f"{task}\n\n"
        "STRATEGY: Start with a broad search (fewer keywords), scan the results, "
        "then refine with additional filter terms if too many irrelevant results appear. "
        "Cast a wide net first, then narrow down."
    )


def _broad_then_narrow_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    query_keys = ["query", "q", "_nkw", "keyword"]
    for key in query_keys:
        if key in params:
            words = params[key][0].split()
            if len(words) > 2:
                params[key] = [" ".join(words[:2])]
            break
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


STRATEGY_REGISTRY: list[SearchStrategy] = [
    SearchStrategy(
        name="sort_cheapest",
        modify_instruction=_sort_cheapest,
        modify_url=_sort_cheapest_url,
        priority=10,
    ),
    SearchStrategy(
        name="keyword_search",
        modify_instruction=_keyword_search,
        modify_url=None,
        priority=8,
    ),
    SearchStrategy(
        name="sort_newest",
        modify_instruction=_sort_newest,
        modify_url=_sort_newest_url,
        priority=6,
    ),
    SearchStrategy(
        name="price_filter_first",
        modify_instruction=_price_filter_first,
        modify_url=None,
        priority=4,
    ),
    SearchStrategy(
        name="broad_then_narrow",
        modify_instruction=_broad_then_narrow,
        modify_url=_broad_then_narrow_url,
        priority=2,
    ),
    SearchStrategy(
        name="category_browse",
        modify_instruction=_category_browse,
        modify_url=None,
        priority=1,
    ),
]


def assign_strategies(branch_count: int) -> list[SearchStrategy]:
    """Assign a strategy to each branch, cycling through the registry by priority."""
    sorted_strategies = sorted(STRATEGY_REGISTRY, key=lambda s: s.priority, reverse=True)
    assignments = []
    for i in range(branch_count):
        assignments.append(sorted_strategies[i % len(sorted_strategies)])
    return assignments


def apply_strategy(
    strategy: SearchStrategy, task: str, url: str
) -> tuple[str, str]:
    """Apply a strategy to a task+url pair. Returns (modified_task, modified_url)."""
    modified_task = strategy.modify_instruction(task, url)
    modified_url = strategy.modify_url(url) if strategy.modify_url else url
    return modified_task, modified_url
