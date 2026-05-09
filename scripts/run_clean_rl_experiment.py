"""Clean RL experiment runner with local verification and proper persistence."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel
from rich.console import Console

from cua_loop.client import run_single_attempt
from cua_loop.types import AttemptResult, Trajectory, VerifierResult
from cua_loop.rl import SearchStrategy
from cua_loop.verifier import verify

console = Console()


class EpisodeStats(BaseModel):
    episode: int
    strategy: str
    algorithm: str
    epsilon: float
    reward: float
    success: bool
    rows: int
    listings_found: int
    stuck_recoveries: int
    action_verification_failures: int
    dangerous_actions_blocked: int
    runtime_seconds: float
    reason: str


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


def compute_reward(attempt: AttemptResult) -> float:
    """Compute reward using local DOM signals only."""
    verifier = attempt.verifier
    traj = attempt.trajectory

    reward = 0.0

    # Success bonus
    if verifier.success:
        reward += 0.40

    # Row count bonus
    if verifier.rows_extracted >= 5:
        reward += 0.20

    # Field presence bonuses
    extracted = traj.extracted or []
    if isinstance(extracted, list) and len(extracted) > 0:
        titles_present = sum(1 for item in extracted if isinstance(item, dict) and item.get("title"))
        prices_present = sum(1 for item in extracted if isinstance(item, dict) and item.get("price"))
        if titles_present >= len(extracted) * 0.5:
            reward += 0.15
        if prices_present >= len(extracted) * 0.5:
            reward += 0.15

    # Page interaction bonus (if steps taken and not all failed)
    step_count = len(traj.steps)
    verified_steps = sum(1 for s in traj.steps if s.verification_passed is True)
    if step_count > 0 and verified_steps >= step_count * 0.3:
        reward += 0.10

    # Penalties
    stuck_recoveries = sum(1 for s in traj.steps if "stuck" in (s.model_message or "").lower())
    reward -= stuck_recoveries * 0.10

    failed_verifications = sum(1 for s in traj.steps if s.verification_passed is False)
    reward -= failed_verifications * 0.15

    dangerous_blocked = sum(1 for s in traj.steps if s.blocked)
    reward -= dangerous_blocked * 0.50

    return max(0.0, min(1.0, round(reward, 3)))


def count_signals(attempt: AttemptResult) -> dict[str, int]:
    """Count various signals from the attempt."""
    traj = attempt.trajectory
    return {
        "stuck_recoveries": sum(1 for s in traj.steps if "stuck" in (s.model_message or "").lower()),
        "action_verification_failures": sum(1 for s in traj.steps if s.verification_passed is False),
        "dangerous_actions_blocked": sum(1 for s in traj.steps if s.blocked),
    }


def save_episode_json(episode_stats: EpisodeStats, traj: Trajectory, output_dir: Path) -> Path:
    """Save episode data as JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fname = output_dir / f"episode_{episode_stats.episode:04d}.json"
    data = {
        **episode_stats.model_dump(),
        "trajectory": traj.model_dump(),
    }
    with open(fname, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return fname


def save_episode_csv(episode_stats: EpisodeStats, csv_path: Path) -> None:
    """Append episode data to CSV."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EpisodeStats.model_fields.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(episode_stats.model_dump())


def load_policy(path: Path) -> dict[str, dict]:
    """Load RL policy from JSON."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    return data.get("stats", {})


def save_policy(policy: dict, path: Path) -> None:
    """Save RL policy to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"stats": policy}, f, indent=2)


def thompson_sample(successes: float, failures: float) -> float:
    """Sample from Beta distribution for Thompson sampling."""
    return random.betavariate(1.0 + successes, 1.0 + failures)


def ucb_score(mean: float, pulls: int, total_pulls: int) -> float:
    """UCB1 score."""
    if pulls == 0:
        return float("inf")
    exploration = math.sqrt(2 * math.log(max(total_pulls, 1)) / pulls)
    return mean + exploration


def choose_strategy(
    strategies: list[SearchStrategy],
    policy: dict[str, dict],
    epsilon: float,
    algorithm: str,
    total_pulls: int,
) -> SearchStrategy:
    """Choose strategy using epsilon-greedy with Thompson or UCB."""
    untried = [s for s in strategies if s.name not in policy or policy[s.name].get("pulls", 0) == 0]
    if untried:
        return untried[0]

    if random.random() < epsilon:
        return random.choice(strategies)

    if algorithm == "thompson":
        return max(
            strategies,
            key=lambda s: thompson_sample(
                policy.get(s.name, {}).get("successes", 0),
                policy.get(s.name, {}).get("failures", 0),
            ),
        )
    else:
        return max(
            strategies,
            key=lambda s: ucb_score(
                policy.get(s.name, {}).get("mean_reward", 0),
                policy.get(s.name, {}).get("pulls", 0),
                total_pulls,
            ),
        )


def update_policy(policy: dict, strategy_name: str, reward: float, success: bool) -> None:
    """Update policy with new reward."""
    if strategy_name not in policy:
        policy[strategy_name] = {"pulls": 0, "reward_sum": 0.0, "successes": 0.0, "failures": 0.0, "mean_reward": 0.0}

    p = policy[strategy_name]
    p["pulls"] += 1
    p["reward_sum"] += reward
    p["mean_reward"] = p["reward_sum"] / p["pulls"]
    if success:
        p["successes"] += reward
    else:
        p["failures"] += 1.0 - reward


def decayed_epsilon(episode: int, total: int, start: float, decay: float, floor: float) -> float:
    """Decay epsilon with exponential decay."""
    return max(floor, start * (decay ** episode))


def run_experiment(
    task: str,
    url: str | None,
    episodes: int,
    algorithm: str,
    epsilon_start: float,
    epsilon_decay: float,
    epsilon_min: float,
    max_steps: int,
    strategies: list[SearchStrategy],
    output_dir: Path,
) -> dict:
    """Run the RL experiment."""
    os.environ["CUA_MAX_STEPS"] = str(max_steps)
    os.environ["VERIFY_MODE"] = "local"

    policy_path = output_dir / "aegis-rl-policy.json"
    csv_path = output_dir / "episodes.csv"

    policy = load_policy(policy_path)
    total_pulls = sum(p.get("pulls", 0) for p in policy.values())

    results = []

    for episode in range(episodes):
        epsilon = decayed_epsilon(episode, episodes, epsilon_start, epsilon_decay, epsilon_min)
        strategy = choose_strategy(strategies, policy, epsilon, algorithm, total_pulls)

        console.rule(f"[bold]Episode {episode + 1}/{episodes}: {strategy.name} ({algorithm}, eps={epsilon:.3f})")

        started = time.time()
        try:
            traj = run_single_attempt(
                task=task,
                url=url,
                extra_context=f"AEGIS strategy: {strategy.name}.\n{strategy.instruction}",
                kind="kernel",
            )
        except Exception as exc:
            traj = Trajectory(task=task, url=url, error=str(exc))

        try:
            verifier = verify(traj)
        except Exception as exc:
            verifier = VerifierResult(success=False, rows_extracted=0, schema_valid=False, reason=f"verifier error: {exc}")

        attempt = AttemptResult(
            attempt_index=0,
            trajectory=traj,
            verifier=verifier,
            duration_s=time.time() - started,
        )

        reward = compute_reward(attempt)
        signals = count_signals(attempt)

        episode_stats = EpisodeStats(
            episode=episode + 1,
            strategy=strategy.name,
            algorithm=algorithm,
            epsilon=epsilon,
            reward=reward,
            success=verifier.success,
            rows=verifier.rows_extracted,
            listings_found=len(traj.extracted) if isinstance(traj.extracted, list) else 0,
            stuck_recoveries=signals["stuck_recoveries"],
            action_verification_failures=signals["action_verification_failures"],
            dangerous_actions_blocked=signals["dangerous_actions_blocked"],
            runtime_seconds=round(attempt.duration_s, 2),
            reason=verifier.reason[:80] if verifier.reason else "",
        )

        save_episode_json(episode_stats, traj, output_dir)
        save_episode_csv(episode_stats, csv_path)

        update_policy(policy, strategy.name, reward, verifier.success)
        save_policy(policy, policy_path)
        total_pulls += 1

        console.print(
            f"reward={reward} success={verifier.success} rows={verifier.rows_extracted} "
            f"reason={verifier.reason[:50]!r}"
        )

        results.append(episode_stats.model_dump())

    return {
        "policy": policy,
        "results": results,
        "csv_path": str(csv_path),
        "policy_path": str(policy_path),
    }


def generate_visualizations(output_dir: Path, csv_path: Path) -> None:
    """Generate visualization plots from episodes CSV."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError:
        console.print("[yellow]matplotlib/pandas not available, skipping visualizations[/yellow]")
        return

    if not csv_path.exists():
        return

    df = pd.read_csv(csv_path)

    # Reward curve
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(df["episode"], df["reward"], marker="o", linewidth=2, label="Episode Reward")
    rolling = df["reward"].rolling(window=5, min_periods=1).mean()
    ax.plot(df["episode"], rolling, linestyle="--", label="Rolling Avg (5)")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Reward")
    ax.set_title("AEGIS RL Reward Curve")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_dir / "reward_curve.png", dpi=150)
    plt.close()

    # Rolling reward
    fig, ax = plt.subplots(figsize=(10, 5))
    rolling = df["reward"].rolling(window=5, min_periods=1).mean()
    ax.plot(df["episode"], rolling, marker="o", linewidth=2, color="green")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Rolling Avg Reward")
    ax.set_title("Rolling Average Reward (window=5)")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_dir / "rolling_reward.png", dpi=150)
    plt.close()

    # Strategy counts
    fig, ax = plt.subplots(figsize=(10, 5))
    counts = df["strategy"].value_counts()
    ax.bar(counts.index, counts.values)
    ax.set_xlabel("Strategy")
    ax.set_ylabel("Count")
    ax.set_title("Strategy Selection Count")
    ax.tick_params(axis="x", rotation=20)
    plt.tight_layout()
    plt.savefig(output_dir / "strategy_counts.png", dpi=150)
    plt.close()

    # Avg reward by strategy
    fig, ax = plt.subplots(figsize=(10, 5))
    avg_reward = df.groupby("strategy")["reward"].mean().sort_values(ascending=False)
    ax.bar(avg_reward.index, avg_reward.values)
    ax.set_xlabel("Strategy")
    ax.set_ylabel("Average Reward")
    ax.set_title("Average Reward by Strategy")
    ax.tick_params(axis="x", rotation=20)
    plt.tight_layout()
    plt.savefig(output_dir / "avg_reward_by_strategy.png", dpi=150)
    plt.close()

    console.print(f"[green]Generated visualizations in {output_dir}[/green]")


def generate_notebook(output_dir: Path, csv_path: Path, policy_path: Path) -> None:
    """Generate rl_analysis.ipynb from episodes data."""
    try:
        import pandas as pd
    except ImportError:
        return

    if not csv_path.exists():
        return

    df = pd.read_csv(csv_path)

    with open(policy_path) as f:
        policy = json.load(f).get("stats", {})

    total_episodes = len(df)
    total_success = df["success"].sum()
    overall_success_rate = total_success / total_episodes * 100 if total_episodes > 0 else 0
    avg_reward = df["reward"].mean()
    avg_runtime = df["runtime_seconds"].mean()

    best_strategy = df.groupby("strategy")["reward"].mean().idxmax() if not df.empty else "N/A"
    most_used = df["strategy"].value_counts().idxmax() if not df.empty else "N/A"

    rolling = df["reward"].rolling(window=5, min_periods=1).mean().tolist()

    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# AEGIS RL Experiment Analysis\n", f"Generated: {datetime.now().isoformat()}"],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## Experiment Summary\n",
                    f"- **Total Episodes**: {total_episodes}\n",
                    f"- **Overall Success Rate**: {overall_success_rate:.1f}%\n",
                    f"- **Average Reward**: {avg_reward:.3f}\n",
                    f"- **Average Runtime**: {avg_runtime:.1f}s\n",
                    f"- **Best Strategy**: {best_strategy}\n",
                    f"- **Most Used Strategy**: {most_used}\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "import pandas as pd\n",
                    "import matplotlib.pyplot as plt\n",
                    "import os\n\n",
                    f"csv_path = '{csv_path}'\n",
                    f"output_dir = '{output_dir}'\n\n",
                    "df = pd.read_csv(csv_path)\n",
                    "print(df.head(10))",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["## Reward Curve"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "fig, ax = plt.subplots(figsize=(10, 5))\n",
                    "ax.plot(df['episode'], df['reward'], marker='o', linewidth=2, label='Episode Reward')\n",
                    "rolling = df['reward'].rolling(window=5, min_periods=1).mean()\n",
                    "ax.plot(df['episode'], rolling, linestyle='--', label='Rolling Avg (5)', color='green')\n",
                    "ax.set_xlabel('Episode')\n",
                    "ax.set_ylabel('Reward')\n",
                    "ax.set_title('AEGIS RL Reward Curve')\n",
                    "ax.legend()\n",
                    "ax.grid(True, alpha=0.3)\n",
                    "plt.tight_layout()\n",
                    "plt.show()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["## Strategy Selection Counts"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "fig, ax = plt.subplots(figsize=(10, 5))\n",
                    "counts = df['strategy'].value_counts()\n",
                    "ax.bar(counts.index, counts.values)\n",
                    "ax.set_xlabel('Strategy')\n",
                    "ax.set_ylabel('Count')\n",
                    "ax.set_title('Strategy Selection Count')\n",
                    "plt.xticks(rotation=20)\n",
                    "plt.tight_layout()\n",
                    "plt.show()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["## Average Reward by Strategy"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "fig, ax = plt.subplots(figsize=(10, 5))\n",
                    "avg_reward = df.groupby('strategy')['reward'].mean().sort_values(ascending=False)\n",
                    "ax.bar(avg_reward.index, avg_reward.values)\n",
                    "ax.set_xlabel('Strategy')\n",
                    "ax.set_ylabel('Average Reward')\n",
                    "ax.set_title('Average Reward by Strategy')\n",
                    "plt.xticks(rotation=20)\n",
                    "plt.tight_layout()\n",
                    "plt.show()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["## Success Rate by Strategy"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "fig, ax = plt.subplots(figsize=(10, 5))\n",
                    "success_rate = df.groupby('strategy')['success'].mean().sort_values(ascending=False) * 100\n",
                    "ax.bar(success_rate.index, success_rate.values)\n",
                    "ax.set_xlabel('Strategy')\n",
                    "ax.set_ylabel('Success Rate (%)')\n",
                    "ax.set_title('Success Rate by Strategy')\n",
                    "plt.xticks(rotation=20)\n",
                    "plt.tight_layout()\n",
                    "plt.show()",
                ],
            },
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["## Final Findings\n", f"**Best performing strategy**: {best_strategy}\n", f"**Most frequently selected**: {most_used}\n", f"**Overall success rate**: {overall_success_rate:.1f}%\n", f"**Average reward**: {avg_reward:.3f}\n"],
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }

    with open(output_dir / "rl_analysis.ipynb", "w") as f:
        json.dump(notebook, f, indent=2)

    console.print(f"[green]Generated {output_dir / 'rl_analysis.ipynb'}[/green]")


def main():
    parser = argparse.ArgumentParser(description="Run clean AEGIS RL experiment")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--url", default=None, help="Starting URL")
    parser.add_argument("--episodes", type=int, default=30, help="Number of episodes")
    parser.add_argument("--algorithm", default="thompson", choices=["thompson", "ucb1"])
    parser.add_argument("--epsilon-start", type=float, default=0.25)
    parser.add_argument("--epsilon-decay", type=float, default=0.97)
    parser.add_argument("--epsilon-min", type=float, default=0.05)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--output-dir", type=Path, default=Path("trajectories"))
    args = parser.parse_args()

    load_dotenv(override=True)
    os.environ.setdefault("BROWSER_BACKEND", "kernel")
    os.environ.setdefault("VERIFY_MODE", "local")

    console.print(f"[bold green]Starting RL experiment: {args.episodes} episodes[/bold green]")

    result = run_experiment(
        task=args.task,
        url=args.url,
        episodes=args.episodes,
        algorithm=args.algorithm,
        epsilon_start=args.epsilon_start,
        epsilon_decay=args.epsilon_decay,
        epsilon_min=args.epsilon_min,
        max_steps=args.max_steps,
        strategies=DEFAULT_STRATEGIES,
        output_dir=args.output_dir,
    )

    console.print("[bold green]Generating visualizations...[/bold green]")
    generate_visualizations(args.output_dir, Path(result["csv_path"]))
    generate_notebook(args.output_dir, Path(result["csv_path"]), Path(result["policy_path"]))

    console.print("[bold green]Experiment complete![/bold green]")


if __name__ == "__main__":
    main()