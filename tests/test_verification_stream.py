from __future__ import annotations

import unittest

from aegis_core.verification.loop_breaker import detect_loop
from aegis_core.verification.middleware import StepState, verify_step
from aegis_core.verification.on_track import drift_for_k_steps, score_progress
from aegis_core.verification.retry_policy import decide_retry_strategy
from aegis_core.verification.screen_predictor import compare_screens, predict_expected_observation, verify_screen_change


class VerificationStreamTest(unittest.TestCase):
    def test_screen_predictor_expects_mutating_actions_to_change(self):
        action = {"type": "click", "x": 20, "y": 30}

        prediction = predict_expected_observation(action, "screen-a")
        same = verify_screen_change(action, "screen-a", "screen-a")
        changed = verify_screen_change(action, "screen-a", "screen-b")

        self.assertEqual(prediction.expectation, "should_change")
        self.assertFalse(same.ok)
        self.assertTrue(changed.ok)
        self.assertTrue(compare_screens("screen-a", "screen-b").changed)

    def test_on_track_flags_low_progress(self):
        score = score_progress(
            "Find laptops under $1000 with 16GB RAM",
            [
                {"action": "click", "message": "captcha error wrong page"},
                {"action": "click", "message": "failed timeout wrong page"},
            ],
        )

        self.assertTrue(score.drift)
        self.assertTrue(drift_for_k_steps([score, score, score]))

    def test_loop_breaker_detects_repeated_state(self):
        signal = detect_loop(
            ["same-screen", "same-screen", "same-screen", "same-screen"],
            [{"type": "click", "x": 1, "y": 2}],
        )

        self.assertTrue(signal.stuck)
        self.assertEqual(signal.reason, "same screen repeated 4 times")

    def test_retry_policy_maps_failures(self):
        self.assertEqual(decide_retry_strategy("stuck_loop", 0.9).strategy, "rephrase")
        self.assertEqual(decide_retry_strategy("wrong_page", 0.7).strategy, "rephrase")
        self.assertEqual(decide_retry_strategy(None, 0.2).strategy, "resample")
        self.assertEqual(decide_retry_strategy(None, 0.9, blocked=True).strategy, "abort")

    def test_middleware_returns_verdict_for_loop(self):
        state = StepState(
            goal="Find laptops under $1000",
            screenshot="screen-a",
            recent_steps=[{"message": "looking for laptop price"}],
            recent_screenshots=["screen-a", "screen-a", "screen-a"],
            recent_actions=[{"type": "click", "x": 1, "y": 2}],
        )
        next_state = StepState(
            goal="Find laptops under $1000",
            screenshot="screen-a",
            recent_steps=[{"message": "looking for laptop price"}],
            recent_screenshots=["screen-a", "screen-a", "screen-a", "screen-a"],
            recent_actions=[{"type": "click", "x": 1, "y": 2}] * 4,
        )

        verdict = verify_step(state, {"type": "click", "x": 1, "y": 2}, next_state)

        self.assertFalse(verdict.on_track)
        self.assertEqual(verdict.drift_reason, "stuck_loop")
        self.assertEqual(verdict.retry_strategy, "rephrase")


if __name__ == "__main__":
    unittest.main()
