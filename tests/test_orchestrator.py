"""Tests for the AEGIS orchestrator."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cua_loop.orchestrator import (
    SEARCH_STRATEGIES,
    OrchestratorResult,
    _SharedState,
    _assign_strategies,
    orchestrate,
)


class TestSharedState:
    def test_add_listings_accumulates(self):
        state = _SharedState(early_stop=10)
        state.add_listings([{"title": "A"}])
        state.add_listings([{"title": "B"}, {"title": "C"}])
        assert state.count == 3
        assert len(state.listings) == 3

    def test_early_stop_fires(self):
        state = _SharedState(early_stop=3)
        assert not state.should_stop.is_set()
        state.add_listings([{"title": f"item_{i}"} for i in range(5)])
        assert state.should_stop.is_set()

    def test_thread_safe(self):
        import threading
        state = _SharedState(early_stop=1000)
        def add_batch():
            for _ in range(50):
                state.add_listings([{"title": "x"}])
        threads = [threading.Thread(target=add_batch) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state.count == 200


class TestStrategyAssignment:
    def test_assigns_diverse_strategies(self):
        urls = {"craigslist": "https://cl.example.com", "reverb": "https://reverb.example.com",
                "mercari": "https://mercari.example.com", "offerup": "https://offerup.example.com"}
        branches = _assign_strategies(urls)
        strategies = [b.strategy["name"] for b in branches]
        assert len(set(strategies)) == 4

    def test_branch_indices_sequential(self):
        urls = {"a": "https://a.com", "b": "https://b.com"}
        branches = _assign_strategies(urls)
        assert [b.branch_index for b in branches] == [0, 1]

    def test_all_strategies_exist(self):
        assert len(SEARCH_STRATEGIES) == 4
        for s in SEARCH_STRATEGIES:
            assert "name" in s
            assert "hint" in s


class TestOrchestrate:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CUA_TRAJ_DIR", str(tmp_path / "trajectories"))
        monkeypatch.setenv("CUA_MAX_STEPS", "3")
        monkeypatch.setenv("AEGIS_EARLY_STOP", "5")

    def _mock_run_single(self, extracted=None):
        def _run(**kwargs):
            from cua_loop.types import Trajectory
            traj = Trajectory(task=kwargs.get("task", ""), url=kwargs.get("url"))
            traj.extracted = extracted
            traj.final_message = "Found listings"
            return traj
        return _run

    def _mock_verify(self, success=True, rows=3):
        return MagicMock(return_value=MagicMock(
            success=success, rows_extracted=rows, schema_valid=True, reason="ok"
        ))

    def test_orchestrate_returns_result(self, monkeypatch):
        listings = [
            {"title": "Couch", "price": 150.0, "marketplace": "craigslist"},
            {"title": "Chair", "price": 100.0, "marketplace": "craigslist"},
        ]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=self._mock_run_single(listings)),
            patch("cua_loop.orchestrator.verify", self._mock_verify()),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"craigslist": "https://cl.example.com"}),
        ):
            result = orchestrate("used couch under $200", max_browsers=1)

        assert isinstance(result, OrchestratorResult)
        assert result.success
        assert result.total_listings_found >= 2

    def test_orchestrate_no_listings_triggers_fallback(self, monkeypatch):
        fallback_listings = [{"title": "Fallback Item", "price": "$50", "marketplace": "craigslist"}]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=Exception("CUA crash")),
            patch("cua_loop.orchestrator.extract_listings", return_value=[]),
            patch("cua_loop.orchestrator.run_fallback_extraction", return_value=fallback_listings),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"craigslist": "https://cl.example.com"}),
            patch("cua_loop.orchestrator.make_backend") as mock_be,
        ):
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_be.return_value = ctx

            result = orchestrate("used couch under $200", max_browsers=1)

        assert result.success
        assert result.total_listings_found >= 1

    def test_early_stop_skips_remaining(self, monkeypatch):
        monkeypatch.setenv("AEGIS_EARLY_STOP", "2")
        listings = [{"title": f"Item {i}", "price": float(i * 100), "marketplace": "cl"} for i in range(5)]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=self._mock_run_single(listings)),
            patch("cua_loop.orchestrator.verify", self._mock_verify(rows=5)),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"craigslist": "https://cl.example.com", "reverb": "https://reverb.example.com"}),
        ):
            result = orchestrate("chairs", max_browsers=2, early_stop=2)

        assert result.success
        assert result.total_listings_found >= 2
