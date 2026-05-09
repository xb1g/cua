"""Natural-language query parser for Bargain Radar.

Regex/heuristic extraction — no LLM calls. Pulls budget, distance, condition
filters, and clean keywords from a free-text marketplace query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cua_loop.ecommerce import parse_budget
from cua_loop.marketplace import parse_max_distance

CONDITION_PATTERNS: list[tuple[str, str]] = [
    (r"\bno\s+replicas?\b", "no replicas"),
    (r"\bno\s+knock[- ]?offs?\b", "no knockoffs"),
    (r"\bauthentic\b", "authentic"),
    (r"\bgenuine\b", "genuine"),
    (r"\boriginal\b", "original"),
    (r"\breal\s+leather\b", "real leather"),
    (r"\bno\s+fakes?\b", "no fakes"),
    (r"\bmint\s+condition\b", "mint condition"),
    (r"\blike\s+new\b", "like new"),
    (r"\bexcellent\s+condition\b", "excellent condition"),
    (r"\bgood\s+condition\b", "good condition"),
    (r"\bnew\s+in\s+box\b", "new in box"),
    (r"\bnib\b", "new in box"),
]

BUDGET_PATTERN = re.compile(
    r"(?:under|below|less\s+than|<=?)\s*\$?[0-9][0-9,]*(?:\.\d+)?", re.I
)

DISTANCE_PATTERN = re.compile(
    r"(?:within|under|<=?)\s*\d+(?:\.\d+)?\s*(?:miles?|mi|km)\b", re.I
)


@dataclass
class ParsedQuery:
    keywords: str
    max_price: float | None = None
    max_distance_mi: float | None = None
    condition_filters: list[str] = field(default_factory=list)
    raw: str = ""


def parse_query(query: str) -> ParsedQuery:
    raw = query
    max_price = parse_budget(query)
    max_distance_mi = parse_max_distance(query)

    condition_filters: list[str] = []
    cleaned = query
    for pattern, label in CONDITION_PATTERNS:
        if re.search(pattern, cleaned, re.I):
            condition_filters.append(label)
            cleaned = re.sub(pattern, " ", cleaned, flags=re.I)

    cleaned = BUDGET_PATTERN.sub(" ", cleaned)
    cleaned = DISTANCE_PATTERN.sub(" ", cleaned)

    keywords = re.sub(r"\s+", " ", cleaned).strip()
    keywords = re.sub(r"^[,\s]+|[,\s]+$", "", keywords)

    return ParsedQuery(
        keywords=keywords,
        max_price=max_price,
        max_distance_mi=max_distance_mi,
        condition_filters=condition_filters,
        raw=raw,
    )
