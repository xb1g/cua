"""Local deterministic self-checks for the AEGIS demo."""

from __future__ import annotations

import sys

from cua_loop.ecommerce import coerce_listing, rank_listings
from cua_loop.security import check_action_policy


class _Action:
    type = "type"
    text = "buy now and checkout with saved card"
    url = None
    result = None
    keys = None


def main() -> int:
    query = "Find a new laptop under $1000 with 16GB RAM and 512GB SSD."
    ranked = rank_listings(
        [
            coerce_listing(
                {
                    "title": "New 14 inch Laptop 16GB RAM 512GB SSD",
                    "price": "$899",
                    "shipping": 0,
                    "availability": "in stock",
                    "condition": "new",
                    "rating": 4.6,
                    "review_count": 240,
                }
            ),
            coerce_listing(
                {
                    "title": "Laptop 8GB RAM 256GB SSD sponsored",
                    "price": "$749",
                    "availability": "in stock",
                    "condition": "new",
                    "rating": 4.2,
                    "review_count": 10,
                }
            ),
            coerce_listing(
                {
                    "title": "Refurbished 14 inch Laptop 16GB RAM 512GB SSD",
                    "price": "$650",
                    "availability": "in stock",
                    "condition": "refurbished",
                }
            ),
        ],
        query,
    )

    checks = [
        (ranked[0].listing.price == 899, "best listing should balance exact specs, new condition, and price"),
        (ranked[0].accepted, "top listing should be accepted"),
        (not check_action_policy(_Action()).allowed, "checkout-like action should be blocked"),
    ]
    failures = [message for ok, message in checks if not ok]
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("AEGIS self-check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
