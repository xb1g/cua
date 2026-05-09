"""Tests for the local DOM-based verifier.

The verifier checks traj.extracted for valid listings (dicts with title+price).
Success requires >= MIN_LISTINGS (default 3) valid entries.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cua_loop.types import Trajectory, VerifierResult
from cua_loop.verifier import verify


def _traj(extracted=None):
    t = Trajectory(task="find couches", url="https://example.com")
    if extracted is not None:
        t.extracted = extracted
    return t


class TestSuccessCases:
    def test_valid_listings_succeed(self):
        traj = _traj([
            {"title": "Chair", "price": "$100"},
            {"title": "Desk", "price": "$200"},
            {"title": "Lamp", "price": "$50"},
        ])
        result = verify(traj)
        assert result.success is True
        assert result.rows_extracted == 3
        assert result.schema_valid is True

    def test_more_than_min_succeeds(self):
        traj = _traj([{"title": f"Item {i}", "price": f"${i*10}"} for i in range(10)])
        result = verify(traj)
        assert result.success is True
        assert result.rows_extracted == 10

    def test_extra_fields_ignored(self):
        traj = _traj([
            {"title": "A", "price": "$1", "condition": "good", "url": "http://x"},
            {"title": "B", "price": "$2", "seller": "bob"},
            {"title": "C", "price": "$3", "photos": 5},
        ])
        assert verify(traj).success is True

    def test_price_as_number(self):
        traj = _traj([
            {"title": "A", "price": 100},
            {"title": "B", "price": 200},
            {"title": "C", "price": 300},
        ])
        assert verify(traj).success is True


class TestFailureCases:
    def test_none_extracted_fails(self):
        traj = _traj()
        result = verify(traj)
        assert result.success is False
        assert result.rows_extracted == 0

    def test_empty_list_fails(self):
        traj = _traj([])
        result = verify(traj)
        assert result.success is False
        assert result.rows_extracted == 0

    def test_missing_price_fails(self):
        traj = _traj([
            {"title": "Chair"},
            {"title": "Desk"},
            {"title": "Lamp"},
        ])
        result = verify(traj)
        assert result.success is False
        assert result.schema_valid is False

    def test_missing_title_fails(self):
        traj = _traj([
            {"price": "$100"},
            {"price": "$200"},
            {"price": "$300"},
        ])
        result = verify(traj)
        assert result.success is False

    def test_too_few_listings_fails(self):
        traj = _traj([
            {"title": "A", "price": "$1"},
            {"title": "B", "price": "$2"},
        ])
        result = verify(traj)
        assert result.success is False

    def test_non_list_extracted_fails(self):
        traj = _traj("not a list")
        result = verify(traj)
        assert result.success is False
        assert "Invalid extracted type" in result.reason

    def test_non_dict_items_ignored(self):
        traj = _traj(["string", 42, None])
        result = verify(traj)
        assert result.success is False

    def test_empty_title_fails(self):
        traj = _traj([
            {"title": "", "price": "$100"},
            {"title": "", "price": "$200"},
            {"title": "", "price": "$300"},
        ])
        result = verify(traj)
        assert result.success is False

    def test_none_price_fails(self):
        traj = _traj([
            {"title": "A", "price": None},
            {"title": "B", "price": None},
            {"title": "C", "price": None},
        ])
        result = verify(traj)
        assert result.success is False


class TestEdgeCases:
    def test_mixed_valid_and_invalid(self):
        traj = _traj([
            {"title": "Valid", "price": "$100"},
            {"title": "Also Valid", "price": "$200"},
            {"title": "Third Valid", "price": "$300"},
            {"missing": "fields"},
            "not a dict",
        ])
        result = verify(traj)
        assert result.success is True
        assert result.rows_extracted == 5

    def test_exactly_min_listings(self):
        traj = _traj([
            {"title": "A", "price": "$1"},
            {"title": "B", "price": "$2"},
            {"title": "C", "price": "$3"},
        ])
        assert verify(traj).success is True

    def test_result_is_verifier_result_type(self):
        result = verify(_traj([]))
        assert isinstance(result, VerifierResult)

    def test_reason_truncated_to_80_chars(self):
        result = verify(_traj([]))
        assert len(result.reason) <= 80

    def test_dict_extracted_not_list_fails(self):
        traj = _traj({"title": "A", "price": "$1"})
        result = verify(traj)
        assert result.success is False
        assert "Invalid extracted type" in result.reason

    def test_single_valid_listing_fails(self):
        traj = _traj([{"title": "Solo", "price": "$999"}])
        result = verify(traj)
        assert result.success is False
        assert result.rows_extracted == 1

    def test_reason_includes_count(self):
        traj = _traj([
            {"title": "A", "price": "$1"},
            {"title": "B", "price": "$2"},
            {"title": "C", "price": "$3"},
        ])
        result = verify(traj)
        assert "3/3" in result.reason
