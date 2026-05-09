"""AEGIS wide scaling: run parallel CUA attempts and pick the best trajectory."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console

from cua_loop.client import run_single_attempt
from cua_loop.runner import _persist
from cua_loop.types import AttemptResult, RunResult, Trajectory, VerifierResult
from cua_loop.verifier import verify

console = Console()

DEFAULT_WIDTH = int(os.getenv("AEGIS_WIDTH", "3"))


def _score(attempt: AttemptResult) -> tuple[int, int, int, float]:
    verifier = attempt.verifier
    unsafe_blocks = sum(1 for step in attempt.trajectory.steps if step.blocked)
    failed_step_checks = sum(1 for step in attempt.trajectory.steps if step.verification_passed is False)
    return (
        1 if verifier.success else 0,
        1 if verifier.schema_valid else 0,
        verifier.rows_extracted,
        -attempt.duration_s - unsafe_blocks * 100 - failed_step_checks * 10,
    )


def _run_branch(task: str, url: str | None, branch_index: int) -> AttemptResult:
    started = time.time()
    extra_context = (
        f"Wide-scaling branch {branch_index}. Try a distinct strategy. "
        "Prefer safe, reversible actions and verify page state before important clicks."
    )
    try:
        traj = run_single_attempt(task=task, url=url, extra_context=extra_context)
    except Exception as exc:
        traj = Trajectory(task=task, url=url, error=str(exc))

    try:
        verifier = verify(traj)
    except Exception as exc:
        verifier = VerifierResult(success=False, reason=f"verifier crashed: {exc}")

    return AttemptResult(
        attempt_index=branch_index,
        trajectory=traj,
        verifier=verifier,
        duration_s=time.time() - started,
    )


def run_wide_scaling(task: str, url: str | None = None, width: int = DEFAULT_WIDTH) -> RunResult:
    """Run N independent browser attempts in parallel and select the strongest result."""
    started = time.time()
    attempts: list[AttemptResult] = []
    width = max(1, width)

    console.rule(f"[bold]AEGIS wide scaling width={width}")
    with ThreadPoolExecutor(max_workers=width) as pool:
        futures = [pool.submit(_run_branch, task, url, i) for i in range(width)]
        for future in as_completed(futures):
            attempt = future.result()
            attempts.append(attempt)
            console.print(
                f"branch {attempt.attempt_index}: success={attempt.verifier.success} "
                f"rows={attempt.verifier.rows_extracted} reason={attempt.verifier.reason!r}"
            )

    selected = max(attempts, key=_score)
    run = RunResult(
        task=task,
        url=url,
        success=selected.verifier.success,
        attempts=sorted(attempts, key=lambda a: a.attempt_index),
        extracted=selected.trajectory.extracted,
        total_duration_s=time.time() - started,
        selected_attempt_index=selected.attempt_index,
    )
    path = _persist(run)
    console.print(f"selected branch {selected.attempt_index}; saved -> {path}")
    return run
