"""Bargain Radar — second-hand marketplace specific scoring and safety.

Layers ON TOP of the generic infrastructure (cua_loop.ecommerce, cua_loop.security).
This module is loaded only when running the Bargain Radar demo flow against
sites like Facebook Marketplace, Craigslist, OfferUp, Mercari, eBay (used),
and Reverb. The base scorer in ecommerce.py is reused; this module adds:

  - replica / authenticity detection
  - scam-pattern flags (off-platform contact, payment-rail traps)
  - distance + freshness scoring
  - marketplace-specific dangerous-action patterns (contact-seller, etc.)

Nothing here mutates the team's existing modules. It composes them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from cua_loop.ecommerce import (
    ListingScore,
    ProductListing,
    score_listing as base_score_listing,
)
from cua_loop.security import PolicyDecision, _text_from_action, detect_prompt_injection


# ── Replica / knockoff patterns (huge problem on FB Marketplace + Craigslist) ──
REPLICA_PATTERNS: tuple[str, ...] = (
    r"\binspired by\b",
    r"\bstyle of\b",
    r"\bin the style of\b",
    r"\b(wayfair|article|west elm)[- ]style\b",
    r"\bknock[- ]?off\b",
    r"\breplica\b",
    r"\breproduction\b",
    r"\bdupe\b",
    r"\blook[- ]?alike\b",
    r"\b(not (an? )?original|unbranded)\b",
    r"\b(faux|imitation|counterfeit)\b",
)

# ── Scam phrasing (off-platform contact, irreversible payment rails) ──────────
SCAM_PATTERNS: tuple[str, ...] = (
    r"\bzelle( only| preferred)?\b",
    r"\bvenmo only\b",
    r"\bcash app(?: only)?\b",
    r"\bwestern union\b",
    r"\bmoney ?gram\b",
    r"\bgift cards?\b",
    r"\bprepaid card\b",
    r"\bwire transfer\b",
    r"\bcrypto( only)?\b",
    r"\bbitcoin\b",
    r"\bshipping only\b",
    r"\bmust ship\b",
    r"\bcan(?:not| ?'?t) meet\b",
    r"\bno (?:meet ?ups?|local pickup)\b",
    r"\bcontact me at \+?\d",
    r"\btext me at \+?\d",
    r"\bemail me at \S+@\S+",
    r"\bdeposit\b.*\bhold\b",
    r"\bnon[- ]?refundable\b.*\bdeposit\b",
)

# ── Listing freshness (Craigslist / FB / OfferUp surface relative timestamps) ─
FRESHNESS_RECENT_PATTERNS: tuple[str, ...] = (
    r"\b\d+ minutes? ago\b",
    r"\b\d+ hours? ago\b",
    r"\bjust posted\b",
    r"\btoday\b",
    r"\byesterday\b",
)
FRESHNESS_STALE_PATTERNS: tuple[str, ...] = (
    r"\b\d+ months? ago\b",
    r"\bover a month\b",
)

# ── Marketplace-specific dangerous-action patterns layered on top of security.py
MARKETPLACE_DANGEROUS_ACTION_PATTERNS: tuple[str, ...] = (
    r"\b(message|contact|chat|email|call|text)\b.*\bseller\b",
    r"\bsend (?:a )?message\b",
    r"\bmake (?:an? )?offer\b",
    r"\bplace (?:a )?bid\b",
    r"\b(?:request|ask) (?:phone|number|address|location)\b",
    r"\bclick (?:link|here|this) (?:in|inside)? ?(?:listing|description|message)\b",
    r"\bopen (?:external|3rd[- ]?party) (?:link|url|site)\b",
)


class MarketplaceListing(ProductListing):
    """Extends ProductListing with second-hand-specific signals."""
    distance_mi: float | None = None
    posted_age_text: str | None = None
    photo_count: int | None = None
    seller_age_days: int | None = None
    seller_other_listings: int | None = None
    listing_id: str | None = None
    marketplace: str | None = None  # craigslist / fb / offerup / mercari / ebay / reverb
    raw_url: str | None = None


class MarketplaceScore(BaseModel):
    """Score result for a marketplace listing."""
    listing: MarketplaceListing
    score: float
    accepted: bool
    is_replica_suspected: bool = False
    is_scam_suspected: bool = False
    is_stale: bool = False
    distance_penalty_applied: bool = False
    base: ListingScore | None = Field(default=None, exclude=False)
    reasons: list[str] = Field(default_factory=list)


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _full_text(listing: MarketplaceListing) -> str:
    parts = [
        listing.title,
        listing.notes,
        listing.condition,
        listing.seller,
        listing.posted_age_text,
        listing.marketplace,
        str(listing.raw or ""),
    ]
    return " ".join(str(p or "") for p in parts).lower()


def _to_product_listing(listing: MarketplaceListing) -> ProductListing:
    """Strip marketplace-only fields so the base scorer can run."""
    base_fields = ProductListing.model_fields.keys()
    payload = {k: getattr(listing, k) for k in base_fields if hasattr(listing, k)}
    return ProductListing(**payload)


def parse_max_distance(query: str) -> float | None:
    match = re.search(
        r"(?:within|under|<=?)\s*(\d+(?:\.\d+)?)\s*(?:miles?|mi|km)\b", query, re.I
    )
    if not match:
        return None
    return float(match.group(1))


def score_marketplace_listing(
    listing: MarketplaceListing, query: str
) -> MarketplaceScore:
    """Score a second-hand listing.

    Composes the base ecommerce scorer (price + spec match + availability) and
    adds replica / scam / freshness / distance signals that are specific to
    Bargain Radar.
    """
    base = base_score_listing(_to_product_listing(listing), query)
    score = base.score
    reasons = list(base.reasons)
    accepted = base.accepted

    text = _full_text(listing)

    # On second-hand marketplaces "used" / "pre-owned" is the EXPECTED
    # condition. Reverse the base scorer's "condition penalty" if the user
    # didn't explicitly ask for a "new" item.
    wants_new = bool(re.search(r"\bnew\b", query, re.I))
    if not wants_new:
        # remove the -12 used-penalty noise added by the base scorer
        for reason in list(reasons):
            if reason == "condition penalty":
                score += 12
                reasons.remove(reason)
                reasons.append("used-as-default (second-hand marketplace)")
                break

    # Replica detection — auto-reject for queries that say "no replicas",
    # otherwise heavy penalty.
    is_replica_suspected = _matches_any(REPLICA_PATTERNS, text)
    if is_replica_suspected:
        wants_authentic = bool(
            re.search(r"\b(no replicas?|authentic|genuine|original|real)\b", query, re.I)
        )
        if wants_authentic:
            accepted = False
            score -= 60
            reasons.append("rejected: replica/knockoff and user requested authentic")
        else:
            score -= 25
            reasons.append("replica/knockoff suspected")

    # Scam phrasing — auto-reject. Off-platform contact + irreversible
    # payment rails are the dominant scam pattern on FB Marketplace + CL.
    is_scam_suspected = _matches_any(SCAM_PATTERNS, text)
    if is_scam_suspected:
        accepted = False
        score -= 80
        reasons.append("rejected: scam-pattern phrasing matched")

    # Freshness. Recent = fresh signal, stale = penalty.
    is_stale = False
    if listing.posted_age_text:
        age = listing.posted_age_text.lower()
        if _matches_any(FRESHNESS_STALE_PATTERNS, age):
            is_stale = True
            score -= 8
            reasons.append("listing >1 month old")
        elif _matches_any(FRESHNESS_RECENT_PATTERNS, age):
            score += 5
            reasons.append("listing recent (<24h)")

    # Distance scoring — only if user specified a radius and we know distance.
    distance_penalty_applied = False
    max_distance = parse_max_distance(query)
    if max_distance is not None and listing.distance_mi is not None:
        if listing.distance_mi > max_distance:
            accepted = False
            distance_penalty_applied = True
            score -= 50
            reasons.append(
                f"rejected: {listing.distance_mi:.0f}mi > {max_distance:.0f}mi requested"
            )
        else:
            score += max(0.0, max_distance - listing.distance_mi) / max(max_distance, 1.0) * 8
            reasons.append(f"within radius: {listing.distance_mi:.0f}mi")

    # Trust signals: photos + seller history (listings without any photos on
    # FB Marketplace are a strong scam tell).
    if listing.photo_count is not None:
        if listing.photo_count == 0:
            score -= 15
            reasons.append("zero photos (high scam risk)")
        elif listing.photo_count >= 4:
            score += 4
            reasons.append("4+ photos")
    if listing.seller_age_days is not None and listing.seller_age_days < 14:
        score -= 6
        reasons.append("seller account <2 weeks old")

    return MarketplaceScore(
        listing=listing,
        score=round(score, 2),
        accepted=accepted,
        is_replica_suspected=is_replica_suspected,
        is_scam_suspected=is_scam_suspected,
        is_stale=is_stale,
        distance_penalty_applied=distance_penalty_applied,
        base=base,
        reasons=reasons,
    )


def rank_marketplace_listings(
    listings: list[MarketplaceListing], query: str
) -> list[MarketplaceScore]:
    return sorted(
        (score_marketplace_listing(l, query) for l in listings),
        key=lambda s: s.score,
        reverse=True,
    )


def dedupe_across_marketplaces(
    scores: list[MarketplaceScore], price_tolerance: float = 25.0
) -> list[MarketplaceScore]:
    """Same item appearing on multiple marketplaces (cross-posting) is common.
    Keep the highest-scoring instance, drop near-duplicates by (title-normalized,
    price-bucket).
    """
    seen: dict[tuple[str, int], MarketplaceScore] = {}
    for s in sorted(scores, key=lambda x: x.score, reverse=True):
        title_key = re.sub(r"\W+", " ", (s.listing.title or "")).strip().lower()
        # Round-to-nearest bucketing so prices on a tolerance boundary collapse
        price_bucket = round((s.listing.price or 0) / max(price_tolerance, 1.0))
        key = (title_key, price_bucket)
        if key not in seen:
            seen[key] = s
    return sorted(seen.values(), key=lambda x: x.score, reverse=True)


def coerce_marketplace_listing(data: dict[str, Any]) -> MarketplaceListing:
    """Coerce a raw dict (from CUA extracted data) into a MarketplaceListing."""
    price = data.get("price")
    if isinstance(price, str):
        match = re.search(r"[0-9][0-9,]*(?:\.\d+)?", price)
        price = float(match.group(0).replace(",", "")) if match else None
    title = data.get("title")
    if not title or not isinstance(title, str):
        title = "(untitled)"
    return MarketplaceListing(**{**data, "title": title, "price": price, "raw": data})


def check_marketplace_action_policy(
    action: Any, model_message: str | None = None
) -> PolicyDecision:
    """Layer marketplace-specific dangerous-action checks on top of the team's
    base security.py policy. The base patterns (purchase, payments, secrets,
    delete, prompt injection) still apply — this adds:

      - contacting / messaging / calling / texting sellers
      - placing bids / making offers
      - clicking external links inside listings
      - asking the seller for phone / address / location
    """
    # First defer to the base policy for prompt injection on the message text.
    injection = detect_prompt_injection(model_message, _text_from_action(action))
    if injection:
        return PolicyDecision(False, injection)

    action_text = _text_from_action(action)
    for pattern in MARKETPLACE_DANGEROUS_ACTION_PATTERNS:
        if re.search(pattern, action_text, re.I):
            return PolicyDecision(
                False, f"marketplace dangerous action matched: {pattern}"
            )
    return PolicyDecision(True)
