from __future__ import annotations

import unittest

from cua_loop.ecommerce import coerce_listing, parse_budget, rank_listings, required_specs
from cua_loop.security import check_action_policy


class Action:
    type = "type"
    text = "checkout and buy now"
    url = None
    result = None
    keys = None


class EcommerceRankingTest(unittest.TestCase):
    def test_parse_budget_and_specs(self):
        query = "new 14 inch laptop under $1,000 with 16GB RAM and 512GB SSD"

        self.assertEqual(parse_budget(query), 1000.0)
        self.assertIn("16gb", required_specs(query))
        self.assertIn("512gb", required_specs(query))

    def test_rank_rejects_wrong_or_unavailable_products(self):
        query = "Find a new laptop under $1000 with 16GB RAM and 512GB SSD"
        ranked = rank_listings(
            [
                coerce_listing({"title": "New laptop 16GB RAM 512GB SSD", "price": "$949", "availability": "in stock", "condition": "new"}),
                coerce_listing({"title": "New laptop 16GB RAM 512GB SSD", "price": "$1200", "availability": "in stock", "condition": "new"}),
                coerce_listing({"title": "Refurbished laptop 16GB RAM 512GB SSD", "price": "$650", "availability": "in stock", "condition": "refurbished"}),
                coerce_listing({"title": "New laptop 16GB RAM 512GB SSD", "price": "$850", "availability": "out of stock", "condition": "new"}),
            ],
            query,
        )

        self.assertTrue(ranked[0].accepted)
        self.assertEqual(ranked[0].listing.price, 949)
        self.assertFalse(any(item.accepted for item in ranked if item.listing.price in {1200, 650, 850}))

    def test_checkout_action_is_blocked(self):
        self.assertFalse(check_action_policy(Action()).allowed)


if __name__ == "__main__":
    unittest.main()
