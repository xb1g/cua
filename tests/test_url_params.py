"""Tests for maximally-filtered URL generation."""

from __future__ import annotations

import pytest

from cua_loop.query_parser import ParsedQuery
from cua_loop.url_params import (
    FILTERED_URL_GENERATORS,
    craigslist_filtered_url,
    ebay_filtered_url,
    fb_marketplace_filtered_url,
    generate_all_filtered_urls,
    generate_filtered_url,
    mercari_filtered_url,
    offerup_filtered_url,
    reverb_filtered_url,
)


def _pq(keywords="eames chair", max_price=1500.0, max_distance_mi=50.0,
         condition_filters=None, raw="") -> ParsedQuery:
    return ParsedQuery(
        keywords=keywords,
        max_price=max_price,
        max_distance_mi=max_distance_mi,
        condition_filters=condition_filters or [],
        raw=raw,
    )


class TestCraigslist:
    def test_basic_params(self):
        url = craigslist_filtered_url(_pq())
        assert "sfbay.craigslist.org" in url
        assert "query=eames+chair" in url
        assert "max_price=1500" in url
        assert "sort=priceasc" in url
        assert "bundleDuplicates=1" in url
        assert "searchNearby=1" in url
        assert "search_distance=50" in url

    def test_no_price_no_distance(self):
        url = craigslist_filtered_url(_pq(max_price=None, max_distance_mi=None))
        assert "max_price" not in url
        assert "search_distance" not in url
        assert "sort=priceasc" in url

    def test_location_override(self):
        url = craigslist_filtered_url(_pq(), location="los angeles")
        assert "losangeles.craigslist.org" in url


class TestEbay:
    def test_basic_params(self):
        url = ebay_filtered_url(_pq())
        assert "ebay.com" in url
        assert "_nkw=eames+chair" in url
        assert "_udhi=1500" in url
        assert "_sop=15" in url
        assert "LH_BIN=1" in url
        assert "LH_ItemCondition=3000" in url

    def test_distance_params(self):
        url = ebay_filtered_url(_pq(max_distance_mi=25.0))
        assert "LH_PrefLoc=99" in url
        assert "_sadis=25" in url

    def test_no_distance(self):
        url = ebay_filtered_url(_pq(max_distance_mi=None))
        assert "LH_PrefLoc" not in url
        assert "_sadis" not in url

    def test_condition_like_new(self):
        url = ebay_filtered_url(_pq(condition_filters=["like new"]))
        assert "LH_ItemCondition=3000" in url
        assert "7000" not in url

    def test_condition_new_in_box(self):
        url = ebay_filtered_url(_pq(condition_filters=["new in box"]))
        assert "LH_ItemCondition=1000" in url


class TestMercari:
    def test_basic_params(self):
        url = mercari_filtered_url(_pq())
        assert "mercari.com" in url
        assert "keyword=eames+chair" in url
        assert "maxPrice=1500" in url
        assert "order=price_asc" in url
        assert "status=on_sale" in url

    def test_condition_like_new(self):
        url = mercari_filtered_url(_pq(condition_filters=["like new"]))
        assert "itemCondition=2" in url

    def test_no_condition(self):
        url = mercari_filtered_url(_pq())
        assert "itemCondition" not in url


class TestOfferUp:
    def test_basic_params(self):
        url = offerup_filtered_url(_pq())
        assert "offerup.com" in url
        assert "q=eames+chair" in url
        assert "price_max=1500" in url
        assert "sort=price" in url
        assert "radius=50" in url

    def test_no_distance(self):
        url = offerup_filtered_url(_pq(max_distance_mi=None))
        assert "radius" not in url


class TestReverb:
    def test_basic_params(self):
        url = reverb_filtered_url(_pq())
        assert "reverb.com" in url
        assert "query=eames+chair" in url
        assert "price_max=1500" in url
        assert "sort=price" in url
        assert "item_state=used" in url


class TestFBMarketplace:
    def test_basic_params(self):
        url = fb_marketplace_filtered_url(_pq())
        assert "facebook.com/marketplace" in url
        assert "query=eames+chair" in url
        assert "maxPrice=1500" in url
        assert "sortBy=price_ascend" in url
        assert "itemCondition=used" in url
        assert "radius=50" in url


class TestGenerateFiltered:
    def test_all_six_have_generators(self):
        expected = {"craigslist", "ebay_used", "mercari", "offerup", "reverb", "fb_marketplace"}
        assert set(FILTERED_URL_GENERATORS.keys()) == expected

    def test_generate_filtered_url_known(self):
        url = generate_filtered_url("craigslist", _pq())
        assert url is not None
        assert "craigslist.org" in url

    def test_generate_filtered_url_unknown(self):
        assert generate_filtered_url("aliexpress", _pq()) is None

    def test_generate_all_filtered_urls(self):
        urls = generate_all_filtered_urls(_pq(), skip_login_required=True)
        assert "craigslist" in urls
        assert "mercari" in urls
        assert "reverb" in urls
        # high bot-detection sites (ebay, offerup) skipped by default
        assert "fb_marketplace" not in urls  # login required, skipped
        for url in urls.values():
            assert "http" in url

    def test_generate_all_includes_fb_when_not_skipping(self):
        import os
        os.environ["AEGIS_SKIP_HIGH_DETECTION"] = "false"
        try:
            urls = generate_all_filtered_urls(_pq(), skip_login_required=False)
            assert "fb_marketplace" in urls
        finally:
            os.environ["AEGIS_SKIP_HIGH_DETECTION"] = "true"

    def test_all_urls_have_price_filter(self):
        urls = generate_all_filtered_urls(_pq(max_price=800.0))
        for name, url in urls.items():
            assert "800" in url, f"{name} URL missing price filter: {url}"

    def test_all_urls_have_sort(self):
        urls = generate_all_filtered_urls(_pq())
        sort_indicators = ["sort", "_sop", "order", "sortBy"]
        for name, url in urls.items():
            assert any(s in url for s in sort_indicators), f"{name} URL missing sort: {url}"
