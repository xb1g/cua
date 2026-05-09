"""AEGIS wide scaling: run parallel CUA attempts and pick the best trajectory."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console

from cua_loop.client import run_single_attempt
from cua_loop.marketplace import (
    coerce_marketplace_listing,
    dedupe_across_marketplaces,
    score_marketplace_listing,
)
from cua_loop.query_parser import parse_query
from cua_loop.runner import _persist
from cua_loop.sites import MARKETPLACE_REGISTRY, generate_all_urls
from cua_loop.url_params import generate_all_filtered_urls
from cua_loop.types import AttemptResult, RunResult, Trajectory, VerifierResult
from cua_loop.verifier import verify

console = Console()

DEFAULT_WIDTH = int(os.getenv("AEGIS_WIDTH", "3"))
_MARKETPLACE_MODE = os.getenv("AEGIS_MARKETPLACE_MODE", "true").lower() in {"1", "true", "yes"}


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


def _score_marketplace_results(extracted: object, task: str) -> list:
    """Convert raw CUA extracted data into scored, deduped MarketplaceScore objects."""
    if not isinstance(extracted, list):
        return []
    scores = []
    for item in extracted:
        if not isinstance(item, dict):
            continue
        try:
            listing = coerce_marketplace_listing(item)
            scores.append(score_marketplace_listing(listing, task))
        except Exception:
            continue
    if scores:
        scores = dedupe_across_marketplaces(scores)
    return scores


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

    mp_scores = None
    if _MARKETPLACE_MODE:
        mp_scores = _score_marketplace_results(selected.trajectory.extracted, task) or None

    run = RunResult(
        task=task,
        url=url,
        success=selected.verifier.success,
        attempts=sorted(attempts, key=lambda a: a.attempt_index),
        extracted=selected.trajectory.extracted,
        total_duration_s=time.time() - started,
        selected_attempt_index=selected.attempt_index,
        marketplace_scores=mp_scores,
    )
    path = _persist(run)
    console.print(f"selected branch {selected.attempt_index}; saved -> {path}")
    return run


def run_marketplace_scaling(
    task: str,
    location: str | None = None,
    width_per_site: int = 1,
    skip_login_required: bool = True,
) -> RunResult:
    """Fan out a NL query across multiple marketplaces in parallel.

    Parses the task string into structured fields, generates search URLs for
    each marketplace, then runs CUA branches against all of them concurrently.
    """
    parsed = parse_query(task)
    urls = generate_all_filtered_urls(
        parsed_query=parsed,
        location=location,
        skip_login_required=skip_login_required,
    )

    started = time.time()
    attempts: list[AttemptResult] = []
    branch_index = 0
    site_branches: list[tuple[str, str, int]] = []

    for site_name, url in urls.items():
        for _ in range(width_per_site):
            site_branches.append((site_name, url, branch_index))
            branch_index += 1

    total_branches = len(site_branches)
    console.rule(f"[bold]AEGIS marketplace fan-out: {len(urls)} sites x {width_per_site} = {total_branches} branches")

    with ThreadPoolExecutor(max_workers=total_branches) as pool:
        futures = {
            pool.submit(_run_branch, task, url, idx): site_name
            for site_name, url, idx in site_branches
        }
        for future in as_completed(futures):
            site_name = futures[future]
            attempt = future.result()
            attempts.append(attempt)
            console.print(
                f"[{site_name}] branch {attempt.attempt_index}: "
                f"success={attempt.verifier.success} "
                f"rows={attempt.verifier.rows_extracted}"
            )

    selected = max(attempts, key=_score)

    mp_scores = None
    if _MARKETPLACE_MODE:
        all_extracted = []
        for a in attempts:
            if isinstance(a.trajectory.extracted, list):
                all_extracted.extend(a.trajectory.extracted)
        if all_extracted:
            mp_scores = _score_marketplace_results(all_extracted, task) or None

    run = RunResult(
        task=task,
        url=None,
        success=selected.verifier.success,
        attempts=sorted(attempts, key=lambda a: a.attempt_index),
        extracted=selected.trajectory.extracted,
        total_duration_s=time.time() - started,
        selected_attempt_index=selected.attempt_index,
        marketplace_scores=mp_scores,
    )
    path = _persist(run)
    console.print(f"selected branch {selected.attempt_index}; saved -> {path}")
    return run
