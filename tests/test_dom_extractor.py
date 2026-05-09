"""Tests for the DOM extraction module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cua_loop.dom_extractor import (
    MARKETPLACE_EXTRACTORS,
    _run_js,
    extract_listings,
)


def _make_backend(execute_js_return: str = "[]"):
    backend = MagicMock()
    backend.execute_js.return_value = execute_js_return
    return backend


class TestRunJs:
    def test_parses_json_array(self):
        items = [{"title": "Chair", "price": "$100"}]
        backend = _make_backend(json.dumps(items))
        result = _run_js(backend, "return '[]';")
        assert result == items

    def test_filters_non_dict_items(self):
        backend = _make_backend(json.dumps([{"title": "A"}, "not a dict", 42]))
        result = _run_js(backend, "return '[]';")
        assert len(result) == 1
        assert result[0]["title"] == "A"

    def test_returns_empty_on_no_execute_js(self):
        backend = MagicMock(spec=[])
        result = _run_js(backend, "return '[]';")
        assert result == []

    def test_returns_empty_on_exception(self):
        backend = MagicMock()
        backend.execute_js.side_effect = RuntimeError("connection lost")
        result = _run_js(backend, "return '[]';")
        assert result == []

    def test_returns_empty_on_invalid_json(self):
        backend = _make_backend("not json at all")
        result = _run_js(backend, "return '[]';")
        assert result == []

    def test_returns_empty_on_empty_string(self):
        backend = _make_backend("")
        result = _run_js(backend, "return '[]';")
        assert result == []


class TestExtractListings:
    def test_uses_marketplace_specific_extractor(self):
        items = [{"title": "Aeron", "price": "$400", "marketplace": "craigslist"}]
        backend = _make_backend(json.dumps(items))
        result = extract_listings(backend, marketplace="craigslist")
        assert len(result) == 1
        assert result[0]["title"] == "Aeron"
        call_code = backend.execute_js.call_args[0][0]
        assert "craigslist" in call_code.lower() or "cl-static" in call_code

    def test_falls_back_to_generic_on_empty_specific(self):
        call_count = {"n": 0}
        def side_effect(code):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "[]"
            return json.dumps([{"title": "Generic Item"}])

        backend = MagicMock()
        backend.execute_js.side_effect = side_effect
        result = extract_listings(backend, marketplace="craigslist")
        assert len(result) == 1
        assert result[0]["title"] == "Generic Item"
        assert backend.execute_js.call_count == 2

    def test_no_marketplace_uses_generic_only(self):
        items = [{"title": "Some Listing"}]
        backend = _make_backend(json.dumps(items))
        result = extract_listings(backend, marketplace=None)
        assert len(result) == 1
        assert backend.execute_js.call_count == 1

    def test_unknown_marketplace_uses_generic(self):
        items = [{"title": "Unknown Site Item"}]
        backend = _make_backend(json.dumps(items))
        result = extract_listings(backend, marketplace="aliexpress")
        assert len(result) == 1

    def test_fb_alias_resolution(self):
        items = [{"title": "FB Item"}]
        backend = _make_backend(json.dumps(items))
        result = extract_listings(backend, marketplace="facebook")
        assert len(result) == 1


class TestMarketplaceExtractors:
    def test_all_six_marketplaces_have_extractors(self):
        expected = {"craigslist", "ebay", "mercari", "offerup", "reverb", "fb"}
        assert set(MARKETPLACE_EXTRACTORS.keys()) == expected

    @pytest.mark.parametrize("marketplace", list(MARKETPLACE_EXTRACTORS.keys()))
    def test_extractor_js_is_valid_string(self, marketplace):
        js = MARKETPLACE_EXTRACTORS[marketplace]
        assert isinstance(js, str)
        assert len(js) > 50
        assert "return JSON.stringify" in js or "return " in js
