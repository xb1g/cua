"""Kernel-backed reinforcement loop for AEGIS search strategies.

This is a contextual bandit, not model-weight training. It learns which prompt
strategy variants produce verified e-commerce trajectories for a given task.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path
from typing import Callable, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from rich.console import Console

from cua_loop.client import run_single_attempt
from cua_loop.types import AttemptResult, Trajectory, VerifierResult
from cua_loop.verifier import verify

console = Console()

DEFAULT_POLICY_PATH = Path(os.getenv("AEGIS_RL_POLICY", "trajectories/aegis-rl-policy.json"))
DEFAULT_REWARD_PLOT_PATH = Path(os.getenv("AEGIS_RL_PLOT", "trajectories/reward_curve.png"))
BanditAlgorithm = Literal["ucb1", "thompson"]


class SearchStrategy(BaseModel):
    name: str
    instruction: str


class StrategyStats(BaseModel):
    pulls: int = 0
    reward_sum: float = 0.0
    last_reward: float = 0.0
    successes: float = 0.0
    failures: float = 0.0

    @property
    def mean_reward(self) -> float:
        return self.reward_sum / self.pulls if self.pulls else 0.0


class RLPolicy(BaseModel):
    stats: dict[str, StrategyStats] = Field(default_factory=dict)

    def choose(self, strategies: list[SearchStrategy], epsilon: float = 0.15, algorithm: BanditAlgorithm = "ucb1") -> SearchStrategy:
        for strategy in strategies:
            self.stats.setdefault(strategy.name, StrategyStats())

        untried = [strategy for strategy in strategies if self.stats[strategy.name].pulls == 0]
        if untried:
            return untried[0]
        if random.random() < epsilon:
            return random.choice(strategies)

        if algorithm == "thompson":
            return max(strategies, key=lambda strategy: self.thompson_sample(strategy.name))

        total_pulls = sum(self.stats[strategy.name].pulls for strategy in strategies)
        return max(strategies, key=lambda strategy: self.ucb_score(strategy.name, total_pulls))

    def ucb_score(self, strategy_name: str, total_pulls: int) -> float:
        stats = self.stats[strategy_name]
        if stats.pulls == 0:
            return float("inf")
        exploration = math.sqrt(2 * math.log(max(total_pulls, 1)) / stats.pulls)
        return stats.mean_reward + exploration

    def thompson_sample(self, strategy_name: str) -> float:
        stats = self.stats[strategy_name]
        return random.betavariate(1.0 + stats.successes, 1.0 + stats.failures)

    def update(self, strategy_name: str, reward: float) -> None:
        stats = self.stats.setdefault(strategy_name, StrategyStats())
        stats.pulls += 1
        stats.reward_sum += reward
        stats.last_reward = reward
        success_mass = reward_to_beta_success(reward)
        stats.successes += success_mass
        stats.failures += 1.0 - success_mass


DEFAULT_STRATEGIES = [
    SearchStrategy(
        name="direct_specs",
        instruction="Search with exact product specs first. Prefer filters for price, RAM, storage, condition, and availability.",
    ),
    SearchStrategy(
        name="broad_then_filter",
        instruction="Start broad, collect candidates, then reject products that fail budget, stock, condition, or spec constraints.",
    ),
    SearchStrategy(
        name="sort_low_price",
        instruction="Sort by lowest total price, but verify shipping, stock, condition, and exact configuration before accepting.",
    ),
    SearchStrategy(
        name="review_quality",
        instruction="Prioritize well-reviewed products. Reject sponsored or low-confidence matches even if the price is attractive.",
    ),
]


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> RLPolicy:
    if not path.exists():
        return RLPolicy()
    return RLPolicy.model_validate_json(path.read_text())


def save_policy(policy: RLPolicy, path: Path = DEFAULT_POLICY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(policy.model_dump_json(indent=2))


def reward_from_attempt(attempt: AttemptResult) -> float:
    verifier = attempt.verifier
    reward = 0.0
    if verifier.success:
        reward += 1.0
    if verifier.schema_valid:
        reward += 0.25
    reward += min(verifier.rows_extracted, 10) * 0.05
    reward -= sum(1 for step in attempt.trajectory.steps if step.blocked) * 1.0
    reward -= sum(1 for step in attempt.trajectory.steps if step.verification_passed is False) * 0.25
    if attempt.trajectory.error:
        reward -= 0.5
    return round(reward, 3)


def reward_to_beta_success(reward: float) -> float:
    """Map shaped verifier reward into a [0, 1] Beta update mass."""
    return max(0.0, min(1.0, (reward + 1.5) / 3.0))


def decayed_epsilon(episode: int, episodes: int, start: float = 0.3, floor: float = 0.05) -> float:
    if start <= 0:
        return 0.0
    if episodes <= 1:
        return max(start, floor)
    ratio = episode / max(episodes - 1, 1)
    return max(floor, start + (floor - start) * ratio)


def save_reward_plot(rewards: list[float], policy: RLPolicy, path: Path = DEFAULT_REWARD_PLOT_PATH) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    fig, (curve_ax, bar_ax) = plt.subplots(2, 1, figsize=(9, 7))

    episodes = list(range(1, len(rewards) + 1))
    curve_ax.plot(episodes, rewards, marker="o", linewidth=2)
    curve_ax.set_title("Reward per Episode")
    curve_ax.set_xlabel("Episode")
    curve_ax.set_ylabel("Reward")
    curve_ax.grid(True, alpha=0.3)

    summary = policy_summary(policy)
    names = [str(item["strategy"]) for item in summary]
    means = [float(item["mean_reward"]) for item in summary]
    bar_ax.bar(names, means)
    bar_ax.set_title("Mean Reward per Strategy")
    bar_ax.set_xlabel("Strategy")
    bar_ax.set_ylabel("Mean Reward")
    bar_ax.tick_params(axis="x", rotation=20)

    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _run_strategy(task: str, url: str | None, strategy: SearchStrategy, index: int) -> AttemptResult:
    started = time.time()
    extra_context = (
        f"AEGIS RL strategy: {strategy.name}.\n"
        f"{strategy.instruction}\n"
        "Use Kernel browser actions safely. Do not buy, checkout, message sellers, or submit payment data."
    )
    try:
        traj = run_single_attempt(task=task, url=url, extra_context=extra_context, kind="kernel")
    except Exception as exc:
        traj = Trajectory(task=task, url=url, error=str(exc))

    try:
        verifier = verify(traj)
    except Exception as exc:
        verifier = VerifierResult(success=False, reason=f"verifier crashed: {exc}")

    return AttemptResult(
        attempt_index=index,
        trajectory=traj,
        verifier=verifier,
        duration_s=time.time() - started,
    )


def train_policy(
    task: str,
    url: str | None,
    episodes: int,
    epsilon: float = 0.3,
    policy_path: Path = DEFAULT_POLICY_PATH,
    strategies: list[SearchStrategy] = DEFAULT_STRATEGIES,
    runner: Callable[[str, str | None, SearchStrategy, int], AttemptResult] = _run_strategy,
    algorithm: BanditAlgorithm = "ucb1",
    epsilon_min: float = 0.05,
    plot_path: Path = DEFAULT_REWARD_PLOT_PATH,
) -> RLPolicy:
    policy = load_policy(policy_path)
    episodes = max(1, episodes)
    rewards: list[float] = []

    for episode in range(episodes):
        episode_epsilon = decayed_epsilon(episode, episodes, epsilon, epsilon_min)
        strategy = policy.choose(strategies, epsilon=episode_epsilon, algorithm=algorithm)
        console.rule(f"[bold]RL episode {episode + 1}/{episodes}: {strategy.name} ({algorithm}, eps={episode_epsilon:.3f})")
        attempt = runner(task, url, strategy, episode)
        reward = reward_from_attempt(attempt)
        rewards.append(reward)
        policy.update(strategy.name, reward)
        console.print(
            f"reward={reward} success={attempt.verifier.success} "
            f"rows={attempt.verifier.rows_extracted} reason={attempt.verifier.reason!r}"
        )
        save_policy(policy, policy_path)

    save_reward_plot(rewards, policy, plot_path)
    return policy


def policy_summary(policy: RLPolicy) -> list[dict[str, float | int | str]]:
    return [
        {
            "strategy": name,
            "pulls": stats.pulls,
            "mean_reward": round(stats.mean_reward, 3),
            "last_reward": stats.last_reward,
            "successes": round(stats.successes, 3),
            "failures": round(stats.failures, 3),
        }
        for name, stats in sorted(policy.stats.items(), key=lambda item: item[1].mean_reward, reverse=True)
    ]


def main() -> int:
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Train AEGIS e-commerce search strategies with Kernel-backed bandit RL.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--url", default=None)
    parser.add_argument("--episodes", type=int, default=4)
    parser.add_argument("--epsilon", type=float, default=0.3)
    parser.add_argument("--epsilon-min", type=float, default=0.05)
    parser.add_argument("--algorithm", choices=("ucb1", "thompson"), default="ucb1")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--plot", type=Path, default=DEFAULT_REWARD_PLOT_PATH)
    args = parser.parse_args()

    os.environ.setdefault("BROWSER_BACKEND", "kernel")
    policy = train_policy(
        args.task,
        args.url,
        args.episodes,
        args.epsilon,
        args.policy,
        algorithm=args.algorithm,
        epsilon_min=args.epsilon_min,
        plot_path=args.plot,
    )
    console.print(json.dumps(policy_summary(policy), indent=2))
    console.print(f"saved reward plot -> {args.plot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
