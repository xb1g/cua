"""Verify cross-branch learning and fallback extraction fire correctly.

Uses mocked CUA branches to test the orchestration logic in scaling.py
without needing real API keys or browser sessions.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from cua_loop.cross_branch import (
    build_cross_branch_hint,
    extract_demonstration,
    should_retry_with_hints,
)
from cua_loop.fallback_scripts import run_fallback_extraction
from cua_loop.types import AttemptResult, Step, Trajectory, VerifierResult


class TestShouldRetryWithHints:
    def test_fires_when_mixed_results(self):
        attempts = [
            AttemptResult(
                attempt_index=0,
                trajectory=Trajectory(task="t"),
                verifier=VerifierResult(success=True, rows_extracted=5),
                duration_s=10,
            ),
            AttemptResult(
                attempt_index=1,
                trajectory=Trajectory(task="t"),
                verifier=VerifierResult(success=False, rows_extracted=0),
                duration_s=10,
            ),
        ]
        assert should_retry_with_hints(attempts) is True

    def test_does_not_fire_when_all_succeed(self):
        attempts = [
            AttemptResult(
                attempt_index=0,
                trajectory=Trajectory(task="t"),
                verifier=VerifierResult(success=True, rows_extracted=5),
                duration_s=10,
            ),
        ]
        assert should_retry_with_hints(attempts) is False

    def test_does_not_fire_when_all_fail(self):
        attempts = [
            AttemptResult(
                attempt_index=0,
                trajectory=Trajectory(task="t"),
                verifier=VerifierResult(success=False),
                duration_s=10,
            ),
            AttemptResult(
                attempt_index=1,
                trajectory=Trajectory(task="t"),
                verifier=VerifierResult(success=False),
                duration_s=10,
            ),
        ]
        assert should_retry_with_hints(attempts) is False


class TestBuildCrossBranchHint:
    def test_generates_hint_from_successful_branch(self):
        traj = Trajectory(task="find chairs")
        traj.steps = [
            Step(action_type="navigate", action_args={"url": "https://example.com"}, screenshot_url=""),
            Step(action_type="click", action_args={"x": 100, "y": 200}, screenshot_url=""),
            Step(action_type="type", action_args={"text": "eames chair"}, screenshot_url=""),
            Step(action_type="scroll", action_args={"scroll_y": 500}, screenshot_url=""),
        ]
        traj.extracted = [{"title": "Chair 1"}, {"title": "Chair 2"}]
        traj.final_message = "Found 2 chairs"

        successful = [
            ("craigslist", AttemptResult(
                attempt_index=0, trajectory=traj,
                verifier=VerifierResult(success=True, rows_extracted=2),
                duration_s=15,
            ))
        ]

        hint = build_cross_branch_hint(successful, "reverb")
        assert "CROSS-BRANCH LEARNING" in hint
        assert "craigslist" in hint
        assert "reverb" in hint
        assert "Navigate" in hint or "Click" in hint

    def test_empty_hint_when_no_successful(self):
        hint = build_cross_branch_hint([], "reverb")
        assert hint == ""


class TestExtractDemonstration:
    def test_extracts_action_sequence(self):
        traj = Trajectory(task="t")
        traj.steps = [
            Step(action_type="navigate", action_args={}, screenshot_url=""),
            Step(action_type="click", action_args={"x": 50, "y": 100}, screenshot_url=""),
        ]
        traj.extracted = [{"title": "A"}]
        demo = extract_demonstration(traj, "craigslist")
        assert "craigslist" in demo
        assert "Navigate" in demo
        assert "Click" in demo
        assert "1 listings extracted" in demo

    def test_empty_on_no_steps(self):
        traj = Trajectory(task="t")
        assert extract_demonstration(traj) == ""


class TestFallbackExtraction:
    @patch("cua_loop.fallback_scripts._FALLBACK_ENABLED", True)
    @patch("cua_loop.fallback_scripts.make_backend")
    @patch("cua_loop.fallback_scripts.scroll_and_accumulate")
    def test_fallback_fires_and_returns_listings(self, mock_scroll, mock_backend):
        mock_scroll.return_value = [
            {"title": "Couch", "price": 150},
            {"title": "Table", "price": 200},
        ]
        backend_instance = MagicMock()
        backend_instance.__enter__ = MagicMock(return_value=backend_instance)
        backend_instance.__exit__ = MagicMock(return_value=False)
        mock_backend.return_value = backend_instance

        result = run_fallback_extraction("https://sfbay.craigslist.org/search/fua?query=couch")
        assert len(result) == 2
        assert result[0]["title"] == "Couch"
        mock_scroll.assert_called_once()
        backend_instance.navigate.assert_called_once()

    @patch("cua_loop.fallback_scripts._FALLBACK_ENABLED", False)
    def test_fallback_disabled_returns_empty(self):
        result = run_fallback_extraction("https://example.com")
        assert result == []

    def test_fallback_empty_url_returns_empty(self):
        result = run_fallback_extraction("")
        assert result == []


class TestScalingWiring:
    """Test that scaling.py correctly invokes cross-branch and fallback."""

    @patch("cua_loop.scaling._run_branch")
    @patch("cua_loop.scaling.run_fallback_extraction")
    @patch("cua_loop.scaling.generate_all_filtered_urls")
    @patch("cua_loop.scaling._persist")
    def test_cross_branch_retry_fires(self, mock_persist, mock_urls, mock_fallback, mock_branch):
        mock_persist.return_value = "/tmp/test.json"
        mock_urls.return_value = {
            "craigslist": "https://sfbay.craigslist.org/search?q=couch",
            "reverb": "https://reverb.com/marketplace?query=couch",
        }
        mock_fallback.return_value = []

        success_traj = Trajectory(task="find couch", url="https://sfbay.craigslist.org")
        success_traj.steps = [Step(action_type="click", action_args={"x": 1, "y": 1}, screenshot_url="")]
        success_traj.extracted = [{"title": "Couch"}]

        fail_traj = Trajectory(task="find couch", url="https://reverb.com")
        fail_traj.error = "hit MAX_STEPS"

        call_count = [0]
        def branch_side_effect(task, url, idx, hint=""):
            call_count[0] += 1
            if "craigslist" in (url or ""):
                return AttemptResult(
                    attempt_index=idx, trajectory=success_traj,
                    verifier=VerifierResult(success=True, rows_extracted=1),
                    duration_s=10,
                )
            return AttemptResult(
                attempt_index=idx, trajectory=fail_traj,
                verifier=VerifierResult(success=False),
                duration_s=10,
            )

        mock_branch.side_effect = branch_side_effect

        from cua_loop.scaling import run_marketplace_scaling
        result = run_marketplace_scaling("find couch under $200")

        # Initial 2 branches + 1 cross-branch retry of failed reverb = 3 total calls
        assert call_count[0] == 3, f"Expected 3 branch calls (2 initial + 1 retry), got {call_count[0]}"
        assert result.success is True
