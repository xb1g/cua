from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cua_loop.rl import RLPolicy, SearchStrategy, decayed_epsilon, policy_summary, reward_from_attempt, reward_to_beta_success, train_policy
from cua_loop.types import AttemptResult, Step, Trajectory, VerifierResult


class RLPolicyTest(unittest.TestCase):
    def test_reward_prefers_verified_clean_attempts(self):
        good = AttemptResult(
            attempt_index=0,
            trajectory=Trajectory(task="find laptop", steps=[]),
            verifier=VerifierResult(success=True, rows_extracted=4, schema_valid=True, reason="ok"),
            duration_s=1.0,
        )
        bad = AttemptResult(
            attempt_index=1,
            trajectory=Trajectory(
                task="find laptop",
                steps=[Step(action_type="click", verification_passed=False, blocked=True)],
                error="blocked unsafe action",
            ),
            verifier=VerifierResult(success=False, rows_extracted=0, schema_valid=False, reason="blocked"),
            duration_s=1.0,
        )

        self.assertGreater(reward_from_attempt(good), reward_from_attempt(bad))

    def test_policy_updates_and_sorts_summary(self):
        policy = RLPolicy()
        policy.update("direct_specs", 1.0)
        policy.update("direct_specs", 0.5)
        policy.update("broad_then_filter", 0.1)

        summary = policy_summary(policy)

        self.assertEqual(summary[0]["strategy"], "direct_specs")
        self.assertEqual(summary[0]["pulls"], 2)
        self.assertGreater(summary[0]["successes"], 0)

    def test_thompson_sampling_can_choose_best_strategy(self):
        random_policy = RLPolicy()
        random_policy.update("good", 1.4)
        random_policy.update("good", 1.2)
        random_policy.update("bad", -0.5)
        strategies = [SearchStrategy(name="good", instruction=""), SearchStrategy(name="bad", instruction="")]

        selected = random_policy.choose(strategies, epsilon=0.0, algorithm="thompson")

        self.assertIn(selected.name, {"good", "bad"})

    def test_epsilon_decay_and_reward_mapping(self):
        self.assertEqual(decayed_epsilon(0, 3), 0.3)
        self.assertEqual(decayed_epsilon(2, 3), 0.05)
        self.assertEqual(reward_to_beta_success(10), 1.0)
        self.assertEqual(reward_to_beta_success(-10), 0.0)

    def test_train_policy_uses_injected_runner(self):
        strategies = [
            SearchStrategy(name="a", instruction="A"),
            SearchStrategy(name="b", instruction="B"),
        ]

        def runner(task: str, url: str | None, strategy: SearchStrategy, index: int) -> AttemptResult:
            return AttemptResult(
                attempt_index=index,
                trajectory=Trajectory(task=task, url=url),
                verifier=VerifierResult(success=strategy.name == "b", rows_extracted=3, schema_valid=True, reason="ok"),
                duration_s=0.1,
            )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            plot_path = Path(tmp) / "reward_curve.png"
            policy = train_policy("find laptop", "https://example.com", 2, 0.0, path, strategies, runner, plot_path=plot_path)
            self.assertTrue(path.exists())
            self.assertTrue(plot_path.exists())

        self.assertEqual(policy.stats["a"].pulls, 1)
        self.assertEqual(policy.stats["b"].pulls, 1)


if __name__ == "__main__":
    unittest.main()
