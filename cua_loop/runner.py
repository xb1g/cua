"""Outer retry loop with self-critique on failure."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from rich.console import Console

from cua_loop.client import run_single_attempt
from cua_loop.types import AttemptResult, RunResult
from cua_loop.verifier import verify

console = Console()

MAX_ATTEMPTS = int(os.getenv("CUA_MAX_ATTEMPTS", "5"))
TRAJ_DIR = Path(os.getenv("CUA_TRAJ_DIR", "trajectories"))


def _critique_for_next(prev_traj_summaries: list[str]) -> str:
    if not prev_traj_summaries:
        return ""
    bullets = "\n".join(f"- attempt {i + 1}: {s}" for i, s in enumerate(prev_traj_summaries))
    return (
        "Prior attempts failed. Specifically:\n"
        f"{bullets}\n"
        "Try a different approach this time. Be more careful about waiting for "
        "page loads and verifying you are on the right element before clicking."
    )


def _summarize_failure(attempt: AttemptResult) -> str:
    return (
        f"{attempt.verifier.reason} "
        f"(steps={len(attempt.trajectory.steps)}, error={attempt.trajectory.error or 'none'})"
    )


def _persist(run: RunResult) -> Path:
    TRAJ_DIR.mkdir(parents=True, exist_ok=True)
    fname = TRAJ_DIR / f"run-{int(time.time())}.json"
    fname.write_text(run.model_dump_json(indent=2))
    return fname


def run_with_retry(
    task: str,
    url: str | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    agent_id: str = "agent_0",
) -> RunResult:
    """Run the inner CUA loop up to N times. Self-critique on each failure."""
    started = time.time()
    attempts: list[AttemptResult] = []
    failure_summaries: list[str] = []

    for i in range(max_attempts):
        console.rule(f"[bold]attempt {i + 1}/{max_attempts}")
        attempt_started = time.time()
        try:
            traj = run_single_attempt(
                task=task,
                url=url,
                extra_context=_critique_for_next(failure_summaries),
                agent_id=agent_id,
            )
        except Exception as e:
            console.print(f"[red]inner loop crashed: {e}[/red]")
            from cua_loop.types import Trajectory

            traj = Trajectory(task=task, url=url, error=str(e))

        v = verify(traj)
        attempt = AttemptResult(
            attempt_index=i,
            trajectory=traj,
            verifier=v,
            duration_s=time.time() - attempt_started,
        )
        attempts.append(attempt)
        console.print(
            f"verifier: success={v.success} rows={v.rows_extracted} reason={v.reason!r}"
        )

        if v.success:
            run = RunResult(
                task=task,
                url=url,
                success=True,
                attempts=attempts,
                extracted=traj.extracted,
                total_duration_s=time.time() - started,
            )
            path = _persist(run)
            console.print(f"[green]✓ verified success on attempt {i + 1}.[/green] saved -> {path}")
            return run

        failure_summaries.append(_summarize_failure(attempt))

    run = RunResult(
        task=task,
        url=url,
        success=False,
        attempts=attempts,
        total_duration_s=time.time() - started,
    )
    path = _persist(run)
    console.print(f"[red]✗ all {max_attempts} attempts failed.[/red] saved -> {path}")
    return run
