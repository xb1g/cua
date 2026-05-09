"""RL training dataset schema and export utilities for CUA trajectories.

Three-layer schema:
  - RLStep:           Per-step (s, a, r, s') transition
  - RLEpisode:        Full trajectory with outcome reward
  - RLPreferencePair: Better vs worse trajectory for DPO/RLHF

Export formats: JSONL (streaming), Parquet (columnar), HuggingFace Dataset.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from rich.console import Console

from cua_loop.types import AttemptResult, RunResult, Step, Trajectory

console = Console()

TaskType = Literal[
    "extract",       # Extract structured data from page
    "navigate",      # Navigate to a specific page/state
    "fill_form",     # Fill out a form
    "purchase_flow", # Complete a purchase (simulated)
    "search",        # Search and find specific info
    "interact",      # General interaction task
]

TerminationReason = Literal[
    "success",       # Agent completed task successfully
    "max_steps",     # Hit step limit
    "loop_detected", # Stuck in action loop
    "blocked",       # Safety policy stopped it
    "crashed",       # Runtime error
    "timeout",       # Time limit exceeded
]

PreferenceSource = Literal[
    "outcome",       # chosen succeeded, rejected failed
    "efficiency",    # both succeeded, chosen was faster
    "reward",        # both succeeded, chosen had higher reward
    "human",         # human labeled
    "verifier",      # LLM verifier scored chosen higher
]


# ---------------------------------------------------------------------------
# Layer 1: Per-step transition
# ---------------------------------------------------------------------------

class RLStep(BaseModel):
    """Single (s, a, r, s') transition for RL training."""

    # Identity
    episode_id: str
    step_index: int

    # State (s_t)
    screenshot_url: str | None = None
    screenshot_b64: str | None = None
    page_url: str | None = None
    page_title: str | None = None
    dom_summary: str | None = None
    element_map: list[dict[str, Any]] | None = None

    # Action (a_t)
    action_type: str
    action_args: dict[str, Any] = Field(default_factory=dict)
    model_reasoning: str | None = None

    # Next state (s_{t+1})
    after_screenshot_url: str | None = None
    after_screenshot_b64: str | None = None
    page_changed: bool | None = None

    # Step-level signals
    action_verified: bool | None = None
    verification_reason: str | None = None
    was_blocked: bool = False
    block_reason: str | None = None
    was_stuck: bool = False

    # Computed step reward
    step_reward: float = 0.0


# ---------------------------------------------------------------------------
# Layer 2: Episode (full trajectory with outcome)
# ---------------------------------------------------------------------------

class RLEpisode(BaseModel):
    """Complete episode for RL training."""

    # Identity
    episode_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    run_id: str = ""
    attempt_index: int = 0

    # Task specification
    task_type: TaskType = "interact"
    task_description: str = ""
    task_url: str | None = None
    task_constraints: dict[str, Any] = Field(default_factory=dict)

    # Strategy metadata (from bandit)
    strategy_name: str | None = None
    strategy_instruction: str | None = None

    # Trajectory
    steps: list[RLStep] = Field(default_factory=list)
    num_steps: int = 0
    duration_s: float = 0.0

    # Outcome signals
    success: bool = False
    outcome_reward: float = 0.0

    # Extraction outcome (for extract tasks)
    rows_extracted: int = 0
    schema_valid: bool = False
    extracted_data: Any = None

    # Action outcome (for action tasks)
    goal_reached: bool = False
    target_state_screenshot: str | None = None
    target_state_description: str | None = None

    # Verifier
    verifier_reason: str = ""
    verifier_model: str = ""

    # Error / termination
    error: str | None = None
    termination_reason: TerminationReason = "success"

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Layer 3: Preference pairs for DPO / RLHF
# ---------------------------------------------------------------------------

class RLPreferencePair(BaseModel):
    """Preference pair for DPO training."""

    task_id: str
    task_description: str
    task_url: str | None = None

    chosen: RLEpisode
    rejected: RLEpisode

    preference_source: PreferenceSource = "outcome"
    reward_margin: float = 0.0


# ---------------------------------------------------------------------------
# Reward functions
# ---------------------------------------------------------------------------

def compute_step_reward(step: RLStep) -> float:
    """Per-step reward for any task type."""
    r = 0.0

    # Page changed after action → the action had visible effect
    if step.page_changed is True:
        r += 0.1
    elif step.page_changed is False:
        r -= 0.05

    # Action passed verification
    if step.action_verified is True:
        r += 0.05
    elif step.action_verified is False:
        r -= 0.15

    # Blocked by safety → penalty
    if step.was_blocked:
        r -= 0.5

    # Stuck in loop → penalty
    if step.was_stuck:
        r -= 0.3

    # Keyboard actions are more reliable than clicks
    if step.action_type in ("keypress", "type", "key"):
        r += 0.02

    return round(r, 4)


def compute_episode_reward(episode: RLEpisode) -> float:
    """Episode-level reward, dispatched by task_type."""
    if episode.task_type == "extract":
        return _extract_episode_reward(episode)
    return _action_episode_reward(episode)


def _action_episode_reward(episode: RLEpisode) -> float:
    """Episode reward for action-oriented tasks (navigate, fill_form, etc.)."""
    r = 0.0

    # Goal completion is the dominant signal
    if episode.success and episode.goal_reached:
        r += 2.0
    elif episode.success:
        r += 1.0

    # Efficiency bonus
    if episode.num_steps < 10:
        r += 0.5
    elif episode.num_steps < 20:
        r += 0.25

    # Step budget penalty
    r -= max(0, episode.num_steps - 25) * 0.05

    # Discounted step rewards
    gamma = 0.99
    for i, step in enumerate(episode.steps):
        r += step.step_reward * (gamma ** i)

    # Termination penalty
    if episode.termination_reason in ("loop_detected", "max_steps"):
        r -= 0.5
    if episode.termination_reason == "crashed":
        r -= 1.0

    return round(r, 4)


def _extract_episode_reward(episode: RLEpisode) -> float:
    """Episode reward for extraction tasks (backwards-compat with rl.py)."""
    r = 0.0
    if episode.success:
        r += 1.0
    if episode.schema_valid:
        r += 0.25
    r += min(episode.rows_extracted, 10) * 0.05
    r -= max(0, episode.num_steps - 20) * 0.1
    r -= sum(1 for s in episode.steps if s.was_blocked) * 1.0
    r -= sum(1 for s in episode.steps if s.action_verified is False) * 0.25
    if episode.num_steps < 15:
        r += 0.5
    return round(r, 4)


# ---------------------------------------------------------------------------
# Conversion: existing types → RL schema
# ---------------------------------------------------------------------------

def _task_id(task: str) -> str:
    """Stable hash of a task description for grouping."""
    return hashlib.sha256(task.encode()).hexdigest()[:12]


def _infer_task_type(task: str) -> TaskType:
    """Best-effort task type inference from natural language."""
    t = task.lower()
    if any(w in t for w in ("extract", "scrape", "table", "listings", "find cheapest", "find the")):
        return "extract"
    if any(w in t for w in ("navigate to", "go to", "open")):
        return "navigate"
    if any(w in t for w in ("fill", "form", "submit", "enter", "register", "sign up")):
        return "fill_form"
    if any(w in t for w in ("buy", "purchase", "checkout", "add to cart")):
        return "purchase_flow"
    if any(w in t for w in ("search", "look up", "find")):
        return "search"
    return "interact"


def _infer_termination(attempt: AttemptResult) -> TerminationReason:
    """Infer why the episode ended from existing data."""
    error = attempt.trajectory.error or ""
    if attempt.verifier.success:
        return "success"
    if "loop detected" in error.lower():
        return "loop_detected"
    if "MAX_STEPS" in error:
        return "max_steps"
    if "blocked" in error.lower():
        return "blocked"
    if attempt.trajectory.error:
        return "crashed"
    return "max_steps"


def step_to_rl_step(step: Step, episode_id: str, index: int) -> RLStep:
    """Convert an existing Step to an RLStep."""
    page_changed: bool | None = None
    if step.screenshot_url and step.after_screenshot_url:
        page_changed = step.screenshot_url != step.after_screenshot_url

    rl_step = RLStep(
        episode_id=episode_id,
        step_index=index,
        screenshot_url=step.screenshot_url,
        page_url=None,
        action_type=step.action_type,
        action_args=step.action_args,
        model_reasoning=step.model_message,
        after_screenshot_url=step.after_screenshot_url,
        page_changed=page_changed,
        action_verified=step.verification_passed,
        verification_reason=step.verification_reason,
        was_blocked=step.blocked,
        block_reason=step.block_reason,
    )
    rl_step.step_reward = compute_step_reward(rl_step)
    return rl_step


def attempt_to_episode(
    attempt: AttemptResult,
    task: str,
    url: str | None,
    run_id: str = "",
) -> RLEpisode:
    """Convert an AttemptResult into an RLEpisode."""
    episode_id = str(uuid.uuid4())
    task_type = _infer_task_type(task)

    rl_steps = [
        step_to_rl_step(step, episode_id, i)
        for i, step in enumerate(attempt.trajectory.steps)
    ]

    episode = RLEpisode(
        episode_id=episode_id,
        task_id=_task_id(task),
        run_id=run_id,
        attempt_index=attempt.attempt_index,
        task_type=task_type,
        task_description=task,
        task_url=url,
        steps=rl_steps,
        num_steps=len(rl_steps),
        duration_s=attempt.duration_s,
        success=attempt.verifier.success,
        rows_extracted=attempt.verifier.rows_extracted,
        schema_valid=attempt.verifier.schema_valid,
        extracted_data=attempt.trajectory.extracted,
        goal_reached=attempt.verifier.success,
        verifier_reason=attempt.verifier.reason,
        error=attempt.trajectory.error,
        termination_reason=_infer_termination(attempt),
    )
    episode.outcome_reward = compute_episode_reward(episode)
    return episode


def run_to_episodes(run: RunResult, run_id: str = "") -> list[RLEpisode]:
    """Convert a full RunResult into a list of RLEpisodes."""
    return [
        attempt_to_episode(attempt, run.task, run.url, run_id=run_id)
        for attempt in run.attempts
    ]


# ---------------------------------------------------------------------------
# Preference pair construction
# ---------------------------------------------------------------------------

def build_preference_pairs(episodes: list[RLEpisode]) -> list[RLPreferencePair]:
    """Build DPO preference pairs from episodes on the same task.

    Strategy:
    1. Group by task_id.
    2. Within each group, pair every successful episode against every failed one (outcome).
    3. Among successes, pair more efficient against less efficient (efficiency).
    """
    from collections import defaultdict

    by_task: dict[str, list[RLEpisode]] = defaultdict(list)
    for ep in episodes:
        by_task[ep.task_id].append(ep)

    pairs: list[RLPreferencePair] = []

    for task_id, group in by_task.items():
        successes = [ep for ep in group if ep.success]
        failures = [ep for ep in group if not ep.success]

        # Outcome pairs: success > failure
        for chosen in successes:
            for rejected in failures:
                pairs.append(RLPreferencePair(
                    task_id=task_id,
                    task_description=chosen.task_description,
                    task_url=chosen.task_url,
                    chosen=chosen,
                    rejected=rejected,
                    preference_source="outcome",
                    reward_margin=chosen.outcome_reward - rejected.outcome_reward,
                ))

        # Efficiency pairs among successes
        if len(successes) >= 2:
            ranked = sorted(successes, key=lambda ep: ep.outcome_reward, reverse=True)
            for i in range(len(ranked) - 1):
                if ranked[i].outcome_reward > ranked[i + 1].outcome_reward:
                    pairs.append(RLPreferencePair(
                        task_id=task_id,
                        task_description=ranked[i].task_description,
                        task_url=ranked[i].task_url,
                        chosen=ranked[i],
                        rejected=ranked[i + 1],
                        preference_source="efficiency",
                        reward_margin=ranked[i].outcome_reward - ranked[i + 1].outcome_reward,
                    ))

    return pairs


# ---------------------------------------------------------------------------
# Export: JSONL
# ---------------------------------------------------------------------------

def _step_to_jsonl_record(step: RLStep, episode: RLEpisode) -> dict[str, Any]:
    """Flatten an RLStep into a JSONL-friendly dict (no screenshots as b64)."""
    return {
        "episode_id": step.episode_id,
        "step_index": step.step_index,
        "task_id": episode.task_id,
        "task_type": episode.task_type,
        "task_description": episode.task_description,
        "task_url": episode.task_url,
        "screenshot_url": step.screenshot_url,
        "after_screenshot_url": step.after_screenshot_url,
        "action_type": step.action_type,
        "action_args": step.action_args,
        "model_reasoning": step.model_reasoning,
        "page_changed": step.page_changed,
        "action_verified": step.action_verified,
        "was_blocked": step.was_blocked,
        "was_stuck": step.was_stuck,
        "step_reward": step.step_reward,
        "episode_reward": episode.outcome_reward,
        "episode_success": episode.success,
        "termination_reason": episode.termination_reason,
    }


def export_steps_jsonl(episodes: list[RLEpisode], path: Path) -> int:
    """Export per-step records to JSONL. Returns count of records written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w") as f:
        for ep in episodes:
            for step in ep.steps:
                record = _step_to_jsonl_record(step, ep)
                f.write(json.dumps(record, default=str) + "\n")
                count += 1
    return count


def export_episodes_jsonl(episodes: list[RLEpisode], path: Path) -> int:
    """Export episode-level summaries to JSONL. Returns count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w") as f:
        for ep in episodes:
            record = ep.model_dump(exclude={"steps"})
            # Add step count and action distribution
            action_dist: dict[str, int] = {}
            for step in ep.steps:
                action_dist[step.action_type] = action_dist.get(step.action_type, 0) + 1
            record["action_distribution"] = action_dist
            record["total_step_reward"] = round(sum(s.step_reward for s in ep.steps), 4)
            f.write(json.dumps(record, default=str) + "\n")
            count += 1
    return count


def export_preferences_jsonl(pairs: list[RLPreferencePair], path: Path) -> int:
    """Export preference pairs to JSONL. Returns count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w") as f:
        for pair in pairs:
            record = {
                "task_id": pair.task_id,
                "task_description": pair.task_description,
                "task_url": pair.task_url,
                "preference_source": pair.preference_source,
                "reward_margin": pair.reward_margin,
                "chosen_episode_id": pair.chosen.episode_id,
                "chosen_reward": pair.chosen.outcome_reward,
                "chosen_steps": pair.chosen.num_steps,
                "chosen_success": pair.chosen.success,
                "rejected_episode_id": pair.rejected.episode_id,
                "rejected_reward": pair.rejected.outcome_reward,
                "rejected_steps": pair.rejected.num_steps,
                "rejected_success": pair.rejected.success,
            }
            f.write(json.dumps(record, default=str) + "\n")
            count += 1
    return count


# ---------------------------------------------------------------------------
# Export: Parquet
# ---------------------------------------------------------------------------

def export_parquet(episodes: list[RLEpisode], output_dir: Path) -> dict[str, int]:
    """Export to Parquet files. Returns counts per table."""
    try:
        import pandas as pd
    except ImportError:
        console.print("[red]pandas is required for parquet export. Install: uv pip install pandas pyarrow[/red]")
        return {}
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        console.print("[red]pyarrow is required for parquet export. Install: uv pip install pyarrow[/red]")
        return {}

    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    # Steps table
    step_records = []
    for ep in episodes:
        for step in ep.steps:
            step_records.append(_step_to_jsonl_record(step, ep))
    if step_records:
        df = pd.DataFrame(step_records)
        # Convert action_args dict to JSON string for Parquet compatibility
        df["action_args"] = df["action_args"].apply(lambda x: json.dumps(x, default=str))
        df.to_parquet(output_dir / "steps.parquet", index=False)
        counts["steps"] = len(df)

    # Episodes table
    ep_records = []
    for ep in episodes:
        record = ep.model_dump(exclude={"steps"})
        action_dist: dict[str, int] = {}
        for step in ep.steps:
            action_dist[step.action_type] = action_dist.get(step.action_type, 0) + 1
        record["action_distribution"] = json.dumps(action_dist)
        record["total_step_reward"] = round(sum(s.step_reward for s in ep.steps), 4)
        # Flatten complex fields
        record["task_constraints"] = json.dumps(record.get("task_constraints", {}), default=str)
        record["extracted_data"] = json.dumps(record.get("extracted_data"), default=str) if record.get("extracted_data") else None
        ep_records.append(record)
    if ep_records:
        df = pd.DataFrame(ep_records)
        df.to_parquet(output_dir / "episodes.parquet", index=False)
        counts["episodes"] = len(df)

    # Preferences table
    pairs = build_preference_pairs(episodes)
    if pairs:
        pair_records = []
        for pair in pairs:
            pair_records.append({
                "task_id": pair.task_id,
                "task_description": pair.task_description,
                "preference_source": pair.preference_source,
                "reward_margin": pair.reward_margin,
                "chosen_episode_id": pair.chosen.episode_id,
                "chosen_reward": pair.chosen.outcome_reward,
                "chosen_steps": pair.chosen.num_steps,
                "rejected_episode_id": pair.rejected.episode_id,
                "rejected_reward": pair.rejected.outcome_reward,
                "rejected_steps": pair.rejected.num_steps,
            })
        df = pd.DataFrame(pair_records)
        df.to_parquet(output_dir / "preferences.parquet", index=False)
        counts["preferences"] = len(df)

    return counts


# ---------------------------------------------------------------------------
# Bulk loader: read all trajectories from disk
# ---------------------------------------------------------------------------

def load_all_episodes(traj_dir: Path, success_only: bool = False) -> list[RLEpisode]:
    """Load all trajectory JSON files and convert to RLEpisodes."""
    all_episodes: list[RLEpisode] = []
    for path in sorted(traj_dir.glob("*.json")):
        try:
            run = RunResult.model_validate_json(path.read_text())
        except Exception:
            console.print(f"[yellow]skip {path.name}: parse error[/yellow]")
            continue
        run_id = path.stem
        episodes = run_to_episodes(run, run_id=run_id)
        if success_only:
            episodes = [ep for ep in episodes if ep.success]
        all_episodes.extend(episodes)
    return all_episodes


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Export CUA trajectories to RL training datasets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  cua-export --format jsonl --output rl_data/steps.jsonl
  cua-export --format parquet --output rl_data/
  cua-export --preferences --output rl_data/preferences.jsonl
  cua-export --success-only --format parquet --output rl_data/
  cua-export --stats  # just print dataset statistics
""",
    )
    parser.add_argument("--trajectories", type=Path, default=Path("trajectories"))
    parser.add_argument("--output", "-o", type=Path, default=Path("rl_data"))
    parser.add_argument(
        "--format", choices=("jsonl", "parquet", "episodes"), default="jsonl",
        help="Export format: jsonl (per-step), parquet (columnar), episodes (episode-level jsonl)",
    )
    parser.add_argument("--success-only", action="store_true", help="Only export successful episodes.")
    parser.add_argument("--preferences", action="store_true", help="Export DPO preference pairs.")
    parser.add_argument("--stats", action="store_true", help="Print dataset statistics without exporting.")
    args = parser.parse_args()

    episodes = load_all_episodes(args.trajectories, success_only=args.success_only)
    console.print(f"[bold]Loaded {len(episodes)} episodes from {args.trajectories}[/bold]")

    if not episodes:
        console.print("[red]No episodes found. Run the CUA loop first.[/red]")
        return 1

    # Print statistics
    successes = sum(1 for ep in episodes if ep.success)
    total_steps = sum(ep.num_steps for ep in episodes)
    task_types: dict[str, int] = {}
    terminations: dict[str, int] = {}
    for ep in episodes:
        task_types[ep.task_type] = task_types.get(ep.task_type, 0) + 1
        terminations[ep.termination_reason] = terminations.get(ep.termination_reason, 0) + 1

    console.print(f"  Episodes:    {len(episodes)} ({successes} successful, {len(episodes) - successes} failed)")
    console.print(f"  Total steps: {total_steps}")
    console.print(f"  Task types:  {task_types}")
    console.print(f"  Terminations: {terminations}")

    rewards = [ep.outcome_reward for ep in episodes]
    if rewards:
        console.print(f"  Reward range: [{min(rewards):.3f}, {max(rewards):.3f}], mean={sum(rewards)/len(rewards):.3f}")

    pairs = build_preference_pairs(episodes)
    console.print(f"  Preference pairs: {len(pairs)}")

    if args.stats:
        return 0

    # Export
    if args.preferences:
        path = args.output if args.output.suffix == ".jsonl" else args.output / "preferences.jsonl"
        count = export_preferences_jsonl(pairs, path)
        console.print(f"[green]✓ Exported {count} preference pairs → {path}[/green]")

    elif args.format == "parquet":
        counts = export_parquet(episodes, args.output)
        for table, count in counts.items():
            console.print(f"[green]✓ {table}.parquet: {count} records[/green]")
        console.print(f"[green]  → {args.output}/[/green]")

    elif args.format == "episodes":
        path = args.output if args.output.suffix == ".jsonl" else args.output / "episodes.jsonl"
        count = export_episodes_jsonl(episodes, path)
        console.print(f"[green]✓ Exported {count} episodes → {path}[/green]")

    else:  # jsonl (per-step)
        path = args.output if args.output.suffix == ".jsonl" else args.output / "steps.jsonl"
        count = export_steps_jsonl(episodes, path)
        console.print(f"[green]✓ Exported {count} step records → {path}[/green]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
