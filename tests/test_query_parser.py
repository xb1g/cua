"""Tests for cua_loop.query_parser — NL query parsing."""
from __future__ import annotations

from cua_loop.query_parser import ParsedQuery, parse_query


# ─── budget extraction ───────────────────────────────────────────────────────

def test_parse_budget_under():
    q = parse_query("Eames chair under $1500")
    assert q.max_price == 1500.0


def test_parse_budget_below():
    q = parse_query("laptop below $800")
    assert q.max_price == 800.0


def test_parse_budget_less_than():
    q = parse_query("guitar less than $2,000")
    assert q.max_price == 2000.0


def test_parse_no_budget():
    q = parse_query("vintage camera")
    assert q.max_price is None


# ─── distance extraction ────────────────────────────────────────────────────

def test_parse_distance_within_miles():
    q = parse_query("desk within 50 miles")
    assert q.max_distance_mi == 50.0


def test_parse_distance_under_mi():
    q = parse_query("couch under 25mi")
    assert q.max_distance_mi == 25.0


def test_parse_no_distance():
    q = parse_query("Eames chair under $1500")
    assert q.max_distance_mi is None


# ─── condition filters ───────────────────────────────────────────────────────

def test_condition_no_replicas():
    q = parse_query("Eames chair no replicas")
    assert "no replicas" in q.condition_filters


def test_condition_real_leather():
    q = parse_query("sofa real leather under $2000")
    assert "real leather" in q.condition_filters


def test_condition_authentic():
    q = parse_query("authentic Louis Vuitton bag")
    assert "authentic" in q.condition_filters


def test_condition_multiple():
    q = parse_query("Eames chair no replicas real leather authentic")
    assert "no replicas" in q.condition_filters
    assert "real leather" in q.condition_filters
    assert "authentic" in q.condition_filters


def test_no_condition_filters():
    q = parse_query("used laptop under $500")
    assert q.condition_filters == []


# ─── keyword extraction ─────────────────────────────────────────────────────

def test_keywords_strip_budget():
    q = parse_query("MacBook Pro under $1500")
    assert "under" not in q.keywords.lower()
    assert "$1500" not in q.keywords
    assert "MacBook Pro" in q.keywords


def test_keywords_strip_distance():
    q = parse_query("desk within 50 miles of SF")
    assert "within" not in q.keywords.lower() or "50 miles" not in q.keywords
    assert "desk" in q.keywords


def test_keywords_strip_conditions():
    q = parse_query("Eames chair no replicas real leather")
    assert "no replicas" not in q.keywords.lower()
    assert "real leather" not in q.keywords.lower()
    assert "Eames" in q.keywords or "eames" in q.keywords.lower()


# ─── full round-trip ─────────────────────────────────────────────────────────

def test_full_complex_query():
    q = parse_query(
        "Eames lounge chair under $1500 within 50 miles no replicas real leather"
    )
    assert q.max_price == 1500.0
    assert q.max_distance_mi == 50.0
    assert "no replicas" in q.condition_filters
    assert "real leather" in q.condition_filters
    assert "Eames" in q.keywords
    assert q.raw == "Eames lounge chair under $1500 within 50 miles no replicas real leather"


def test_raw_preserved():
    raw = "vintage guitar under $500"
    q = parse_query(raw)
    assert q.raw == raw


def test_simple_query_no_extras():
    q = parse_query("vintage camera")
    assert q.keywords == "vintage camera"
    assert q.max_price is None
    assert q.max_distance_mi is None
    assert q.condition_filters == []
