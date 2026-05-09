"""E-commerce listing validation and ranking for the AEGIS demo."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class ProductListing(BaseModel):
    title: str
    url: str | None = None
    price: float | None = None
    shipping: float = 0.0
    availability: str | None = None
    condition: str | None = None
    rating: float | None = None
    review_count: int = 0
    seller: str | None = None
    notes: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class ListingScore(BaseModel):
    listing: ProductListing
    score: float
    accepted: bool
    reasons: list[str] = Field(default_factory=list)


NEGATIVE_PATTERNS = (
    r"\bout of stock\b",
    r"\bsold out\b",
    r"\bunavailable\b",
    r"\bparts only\b",
    r"\bfor parts\b",
)

REFURB_PATTERNS = (
    r"\brefurbished\b",
    r"\brenewed\b",
    r"\bopen box\b",
    r"\bpre-owned\b",
    r"\bused\b",
)

SPONSORED_PATTERNS = (
    r"\bsponsored\b",
    r"\bad\b",
    r"\bpromoted\b",
)


def parse_budget(query: str) -> float | None:
    match = re.search(r"(?:under|below|less than|<=?)\s*\$?([0-9][0-9,]*(?:\.\d+)?)", query, re.I)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def required_specs(query: str) -> list[str]:
    specs: list[str] = []
    for pattern in (r"\b\d+\s?gb\b", r"\b\d+\s?tb\b", r"\b\d+\s?inch\b", r"\b\d+\s?\"", r"\bssd\b", r"\bram\b"):
        specs.extend(m.group(0).lower().replace(" ", "") for m in re.finditer(pattern, query, re.I))
    return sorted(set(specs))


def _haystack(listing: ProductListing) -> str:
    return " ".join(
        str(part or "")
        for part in (
            listing.title,
            listing.availability,
            listing.condition,
            listing.seller,
            listing.notes,
            listing.raw,
        )
    ).lower()


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def score_listing(listing: ProductListing, query: str) -> ListingScore:
    score = 100.0
    reasons: list[str] = []
    accepted = True
    text = _haystack(listing)
    budget = parse_budget(query)
    total_price = (listing.price or 0.0) + listing.shipping

    if listing.price is None:
        accepted = False
        score -= 50
        reasons.append("missing price")
    elif budget is not None and total_price > budget:
        accepted = False
        score -= 60
        reasons.append(f"over budget after shipping: ${total_price:.2f} > ${budget:.2f}")
    elif budget is not None:
        score += max(0.0, budget - total_price) / max(budget, 1.0) * 20
        reasons.append(f"within budget: ${total_price:.2f}")

    if _matches_any(NEGATIVE_PATTERNS, text):
        accepted = False
        score -= 80
        reasons.append("not currently buyable")

    wants_new = bool(re.search(r"\bnew\b", query, re.I))
    if wants_new and _matches_any(REFURB_PATTERNS, text):
        accepted = False
        score -= 45
        reasons.append("condition conflicts with new-only query")
    elif _matches_any(REFURB_PATTERNS, text):
        score -= 12
        reasons.append("condition penalty")

    if _matches_any(SPONSORED_PATTERNS, text):
        score -= 8
        reasons.append("sponsored/promoted penalty")

    specs = required_specs(query)
    missing_specs = [spec for spec in specs if spec not in text.replace(" ", "")]
    if missing_specs:
        score -= 10 * len(missing_specs)
        reasons.append("missing specs: " + ", ".join(missing_specs))

    if listing.rating is not None:
        score += max(0.0, min(listing.rating, 5.0) - 3.0) * 5
    if listing.review_count:
        score += min(listing.review_count, 500) / 100

    return ListingScore(listing=listing, score=round(score, 2), accepted=accepted, reasons=reasons)


def rank_listings(listings: list[ProductListing], query: str) -> list[ListingScore]:
    return sorted((score_listing(listing, query) for listing in listings), key=lambda item: item.score, reverse=True)


def coerce_listing(data: dict[str, Any]) -> ProductListing:
    price = data.get("price")
    if isinstance(price, str):
        match = re.search(r"[0-9][0-9,]*(?:\.\d+)?", price)
        price = float(match.group(0).replace(",", "")) if match else None
    return ProductListing(**{**data, "price": price, "raw": data})
