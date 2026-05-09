"""End-to-end integration tests for the marketplace pipeline.

Mocks Northstar (Lightcone), the browser backend, and the verifier so no live
APIs are needed. Validates the full flow: scaling → verification → marketplace
scoring → dedup → action policy.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cua_loop.action_verifier import ActionVerification
from cua_loop.marketplace import MarketplaceScore
from cua_loop.scaling import run_wide_scaling
from cua_loop.types import RunResult

SAMPLE_LISTINGS = [
    {
        "title": "Herman Miller Aeron Chair",
        "price": 450.0,
        "condition": "used",
        "marketplace": "craigslist",
        "notes": "Great condition, size B",
        "photo_count": 5,
    },
    {
        "title": "Herman Miller Aeron Chair",
        "price": 460.0,
        "condition": "used",
        "marketplace": "fb",
        "notes": "Size B, like new",
        "photo_count": 4,
    },
    {
        "title": "Steelcase Leap V2",
        "price": 300.0,
        "condition": "pre-owned",
        "marketplace": "offerup",
        "notes": "Black fabric, no arms",
        "photo_count": 3,
    },
]

SCAM_LISTING = {
    "title": "Herman Miller Embody",
    "price": 200.0,
    "condition": "used",
    "marketplace": "fb",
    "notes": "Zelle only, cannot meet, shipping only",
    "photo_count": 0,
}

TASK = "Find ergonomic office chairs under $500"


def _make_provider_mock():
    """Build a mock ModelProvider whose create_response yields a terminate action."""
    terminate_action = SimpleNamespace(
        type="terminate",
        result="done",
        x=None, y=None, end_x=None, end_y=None,
        text=None, keys=None, url=None,
        scroll_x=None, scroll_y=None, button=None, status=None,
    )
    computer_call = SimpleNamespace(
        type="computer_call",
        call_id="call_001",
        action=terminate_action,
    )
    response = MagicMock()
    response.output = [computer_call]
    response.id = "resp_001"

    provider = MagicMock()
    provider.create_response.return_value = response
    provider.get_tools.return_value = [{"type": "computer_use", "display_width": 1280, "display_height": 720, "environment": "desktop"}]
    return provider


def _make_backend_mock():
    """Build a mock browser backend that returns distinct screenshot URLs."""
    counter = {"n": 0}
    backend = MagicMock()
    ctx = MagicMock()

    def _screenshot_url():
        counter["n"] += 1
        return f"https://screenshots.test/{counter['n']}.png"

    ctx.screenshot_url = _screenshot_url
    ctx.click = MagicMock()
    ctx.wait = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    backend.return_value = ctx
    return backend



_PATCHES = {
    "provider": "cua_loop.client._get_provider",
    "backend": "cua_loop.client.make_backend",
    "action_verifier": "cua_loop.client.verify_action_effect",
    "notify": "cua_loop.client._notify_ui",
}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CUA_TRAJ_DIR", str(tmp_path / "trajectories"))
    monkeypatch.setenv("CUA_MAX_STEPS", "5")


class TestFullPipeline:
    def _run(self, extracted, monkeypatch, marketplace_mode="true"):
        monkeypatch.setenv("AEGIS_MARKETPLACE_MODE", marketplace_mode)
        import cua_loop.scaling as scaling_mod
        monkeypatch.setattr(scaling_mod, "_MARKETPLACE_MODE",
                            marketplace_mode.lower() in {"1", "true", "yes"})

        provider = _make_provider_mock()
        backend = _make_backend_mock()

        def _patched_run(**kwargs):
            from cua_loop.types import Trajectory
            traj = Trajectory(task=kwargs.get("task", ""), url=kwargs.get("url"))
            traj.extracted = extracted
            traj.final_message = "Found listings"
            return traj

        with (
            patch(_PATCHES["provider"], return_value=provider),
            patch(_PATCHES["backend"], backend),
            patch(_PATCHES["action_verifier"], return_value=ActionVerification(True, "ok")),
            patch(_PATCHES["notify"]),
            patch("cua_loop.scaling.run_single_attempt", _patched_run),
        ):
            return run_wide_scaling(task=TASK, width=1)

    def test_full_pipeline_scores_and_dedupes(self, monkeypatch):
        result = self._run(SAMPLE_LISTINGS, monkeypatch)
        assert isinstance(result, RunResult)
        assert result.marketplace_scores is not None
        scores = result.marketplace_scores
        assert len(scores) == 2, f"expected 2 after dedup (2 Aeron dupes → 1), got {len(scores)}"
        for s in scores:
            assert isinstance(s, MarketplaceScore)
            assert s.listing.title

    def test_marketplace_mode_off_skips_scoring(self, monkeypatch):
        result = self._run(SAMPLE_LISTINGS, monkeypatch, marketplace_mode="false")
        assert result.marketplace_scores is None

    def test_scam_listing_rejected(self, monkeypatch):
        result = self._run([SCAM_LISTING], monkeypatch)
        assert result.marketplace_scores is not None
        assert len(result.marketplace_scores) == 1
        score = result.marketplace_scores[0]
        assert score.is_scam_suspected is True
        assert score.accepted is False

    def test_empty_extracted_produces_no_scores(self, monkeypatch):
        result = self._run(None, monkeypatch)
        assert result.marketplace_scores is None

    def test_non_dict_items_skipped(self, monkeypatch):
        result = self._run(["not a dict", 42, SAMPLE_LISTINGS[2]], monkeypatch)
        assert result.marketplace_scores is not None
        assert len(result.marketplace_scores) == 1
        assert result.marketplace_scores[0].listing.title == "Steelcase Leap V2"


class TestMarketplaceActionPolicy:
    def test_contact_seller_blocked(self, monkeypatch):
        monkeypatch.setenv("AEGIS_MARKETPLACE_MODE", "true")
        from cua_loop.marketplace import check_marketplace_action_policy
        action = SimpleNamespace(type="click", text="message seller", url=None, keys=None, result=None)
        policy = check_marketplace_action_policy(action, None)
        assert not policy.allowed
        assert "marketplace" in policy.reason.lower()

    def test_safe_action_allowed(self, monkeypatch):
        monkeypatch.setenv("AEGIS_MARKETPLACE_MODE", "true")
        from cua_loop.marketplace import check_marketplace_action_policy
        action = SimpleNamespace(type="scroll", text=None, url=None, keys=None, result=None)
        policy = check_marketplace_action_policy(action, None)
        assert policy.allowed
