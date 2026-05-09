"""Mock Trajectory factories for verifier testing.

Each factory returns a Trajectory that simulates a specific CUA outcome.
No real screenshots or CUA calls needed — these are pure data fixtures.
"""

from __future__ import annotations

import json
from cua_loop.types import Step, Trajectory


def successful_extraction() -> Trajectory:
    """Agent navigated correctly and extracted a clean table."""
    return Trajectory(
        task="Extract the pricing table from the page",
        url="https://example.com/pricing",
        steps=[
            Step(action_type="navigate", action_args={"url": "https://example.com/pricing"}),
            Step(action_type="scroll", action_args={"scroll_y": 300}),
            Step(action_type="click", action_args={"x": 400, "y": 250}),
            Step(action_type="done", action_args={}),
        ],
        final_message="I found the pricing table and extracted 3 plans.",
        extracted=[
            {"plan": "Starter", "price": "$9/mo", "features": "5 users, 10GB"},
            {"plan": "Pro", "price": "$29/mo", "features": "25 users, 100GB"},
            {"plan": "Enterprise", "price": "Custom", "features": "Unlimited"},
        ],
    )


def partial_extraction() -> Trajectory:
    """Agent extracted some rows but clearly missed data (only 1 of many)."""
    return Trajectory(
        task="Extract all product listings from the catalog page (expect ~20 items)",
        url="https://example.com/catalog",
        steps=[
            Step(action_type="navigate", action_args={"url": "https://example.com/catalog"}),
            Step(action_type="done", action_args={}),
        ],
        final_message="Here is the product listing I found.",
        extracted=[
            {"name": "Widget A", "price": "$10"},
        ],
    )


def wrong_schema() -> Trajectory:
    """Agent extracted data but with wrong/inconsistent field names."""
    return Trajectory(
        task="Extract the employee directory: columns should be name, title, department, email",
        url="https://example.com/directory",
        steps=[
            Step(action_type="navigate", action_args={"url": "https://example.com/directory"}),
            Step(action_type="scroll", action_args={"scroll_y": 500}),
            Step(action_type="done", action_args={}),
        ],
        final_message="Extracted the employee table.",
        extracted=[
            {"full_name": "Alice Smith", "role": "Engineer", "dept": "Eng", "contact": "alice@co.com"},
            {"full_name": "Bob Jones", "role": "Designer", "dept": "Design", "contact": "bob@co.com"},
        ],
    )


def says_done_but_empty() -> Trajectory:
    """Agent claimed success but extracted nothing."""
    return Trajectory(
        task="Extract the leaderboard table",
        url="https://example.com/leaderboard",
        steps=[
            Step(action_type="navigate", action_args={"url": "https://example.com/leaderboard"}),
            Step(action_type="click", action_args={"x": 200, "y": 300}),
            Step(action_type="click", action_args={"x": 350, "y": 400}),
            Step(action_type="done", action_args={}),
        ],
        final_message="I've completed the task and extracted the leaderboard.",
        extracted=None,
    )


def crash_error() -> Trajectory:
    """Agent crashed mid-run."""
    return Trajectory(
        task="Extract the inventory table",
        url="https://example.com/inventory",
        steps=[
            Step(action_type="navigate", action_args={"url": "https://example.com/inventory"}),
            Step(action_type="click", action_args={"x": 100, "y": 100}),
        ],
        final_message=None,
        extracted=None,
        error="TimeoutError: browser session expired after 30s",
    )


def hit_max_steps() -> Trajectory:
    """Agent looped until MAX_STEPS without producing a result."""
    steps = [
        Step(action_type="scroll", action_args={"scroll_y": 100, "x": 640, "y": 400})
        for _ in range(40)
    ]
    return Trajectory(
        task="Extract the flight results table",
        url="https://example.com/flights",
        steps=steps,
        final_message=None,
        extracted=None,
        error="hit MAX_STEPS=40 without terminating",
    )


def vague_done_message() -> Trajectory:
    """Agent said something vague — no structured data, just prose."""
    return Trajectory(
        task="Extract the stock price table for AAPL",
        url="https://finance.example.com/AAPL",
        steps=[
            Step(action_type="navigate", action_args={"url": "https://finance.example.com/AAPL"}),
            Step(action_type="scroll", action_args={"scroll_y": 200}),
            Step(action_type="done", action_args={}),
        ],
        final_message="I looked at the page and clicked around. The stock prices are visible on screen.",
        extracted=None,
    )


def non_list_extracted() -> Trajectory:
    """Agent extracted a single string instead of structured rows."""
    return Trajectory(
        task="Extract the comparison table",
        url="https://example.com/compare",
        steps=[
            Step(action_type="navigate", action_args={"url": "https://example.com/compare"}),
            Step(action_type="done", action_args={}),
        ],
        final_message="Here are the results.",
        extracted="Product A is better than Product B in price and features.",
    )


ALL_FIXTURES = {
    "successful_extraction": successful_extraction,
    "partial_extraction": partial_extraction,
    "wrong_schema": wrong_schema,
    "says_done_but_empty": says_done_but_empty,
    "crash_error": crash_error,
    "hit_max_steps": hit_max_steps,
    "vague_done_message": vague_done_message,
    "non_list_extracted": non_list_extracted,
}
