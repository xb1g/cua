"""Tests for cua_loop.sites — marketplace URL generators."""
from __future__ import annotations

from cua_loop.sites import (
    MARKETPLACE_REGISTRY,
    craigslist_url,
    ebay_used_url,
    fb_marketplace_url,
    generate_all_urls,
    mercari_url,
    offerup_url,
    reverb_url,
)


# ─── registry ────────────────────────────────────────────────────────────────

def test_registry_has_all_six_sites():
    expected = {"craigslist", "offerup", "mercari", "ebay_used", "reverb", "fb_marketplace"}
    assert set(MARKETPLACE_REGISTRY.keys()) == expected


def test_fb_marketplace_requires_login():
    assert MARKETPLACE_REGISTRY["fb_marketplace"].requires_login is True


def test_non_login_sites_do_not_require_login():
    for name, adapter in MARKETPLACE_REGISTRY.items():
        if name != "fb_marketplace":
            assert adapter.requires_login is False, f"{name} should not require login"


# ─── craigslist ──────────────────────────────────────────────────────────────

def test_craigslist_basic():
    url = craigslist_url("Eames chair", 1500)
    assert "sfbay.craigslist.org/search/sss" in url
    assert "query=Eames+chair" in url
    assert "max_price=1500" in url


def test_craigslist_no_price():
    url = craigslist_url("vintage lamp")
    assert "max_price" not in url
    assert "query=vintage+lamp" in url


def test_craigslist_location_override():
    url = craigslist_url("bike", location="los angeles")
    assert "losangeles.craigslist.org" in url


def test_craigslist_unknown_location_defaults_sfbay():
    url = craigslist_url("table", location="rural ohio")
    assert "sfbay.craigslist.org" in url


# ─── offerup ─────────────────────────────────────────────────────────────────

def test_offerup_basic():
    url = offerup_url("MacBook Pro", 1200)
    assert "offerup.com/search" in url
    assert "q=MacBook+Pro" in url
    assert "price_max=1200" in url


def test_offerup_no_price():
    url = offerup_url("desk")
    assert "price_max" not in url


# ─── mercari ─────────────────────────────────────────────────────────────────

def test_mercari_basic():
    url = mercari_url("Nintendo Switch", 250)
    assert "mercari.com/search" in url
    assert "keyword=Nintendo+Switch" in url
    assert "maxPrice=250" in url


def test_mercari_no_price():
    url = mercari_url("headphones")
    assert "maxPrice" not in url


# ─── ebay used ───────────────────────────────────────────────────────────────

def test_ebay_used_basic():
    url = ebay_used_url("ThinkPad X1", 800)
    assert "ebay.com/sch/i.html" in url
    assert "_nkw=ThinkPad+X1" in url
    assert "LH_ItemCondition" in url
    assert "_udhi=800" in url


def test_ebay_used_no_price():
    url = ebay_used_url("camera lens")
    assert "_udhi" not in url
    assert "LH_ItemCondition" in url


# ─── reverb ──────────────────────────────────────────────────────────────────

def test_reverb_basic():
    url = reverb_url("Fender Stratocaster", 900)
    assert "reverb.com/marketplace" in url
    assert "query=Fender+Stratocaster" in url
    assert "price_max=900" in url


def test_reverb_no_price():
    url = reverb_url("guitar pedal")
    assert "price_max" not in url


# ─── fb marketplace ──────────────────────────────────────────────────────────

def test_fb_marketplace_basic():
    url = fb_marketplace_url("couch", 500)
    assert "facebook.com/marketplace" in url
    assert "query=couch" in url
    assert "maxPrice=500" in url


def test_fb_marketplace_no_price():
    url = fb_marketplace_url("dining table")
    assert "maxPrice" not in url


# ─── special characters ─────────────────────────────────────────────────────

def test_special_characters_encoded():
    url = craigslist_url('14" laptop & charger', 500)
    assert "+" in url or "%22" in url
    assert "%26" in url or "&" in url


# ─── generate_all_urls ───────────────────────────────────────────────────────

def test_generate_all_urls_returns_all_six():
    urls = generate_all_urls("test query", 100)
    assert len(urls) == 6
    assert set(urls.keys()) == set(MARKETPLACE_REGISTRY.keys())


def test_generate_all_urls_skip_login():
    urls = generate_all_urls("test query", 100, skip_login_required=True)
    assert "fb_marketplace" not in urls
    assert len(urls) == 5


def test_generate_all_urls_no_price():
    urls = generate_all_urls("test query")
    for name, url in urls.items():
        price_params = ["max_price=", "price_max=", "maxPrice=", "_udhi="]
        assert not any(p in url for p in price_params), f"{name} should not have price param"
