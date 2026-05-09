"""AEGIS wide scaling: run parallel CUA attempts and pick the best trajectory."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console  # type: ignore[reportMissingImports]

from cua_loop.client import run_single_attempt
from cua_loop.cross_branch import build_cross_branch_hint, should_retry_with_hints
from cua_loop.fallback_scripts import run_fallback_extraction
from cua_loop.marketplace import (
    coerce_marketplace_listing,
    dedupe_across_marketplaces,
    score_marketplace_listing,
)
from cua_loop.orchestrator import AgentTask, synthesize_results, understand_intent, decompose_task
from cua_loop.query_parser import parse_query
from cua_loop.runner import _persist
from cua_loop.sites import MARKETPLACE_REGISTRY, generate_all_urls
from cua_loop.url_params import generate_all_filtered_urls
from cua_loop.types import AttemptResult, RunResult, Trajectory, VerifierResult
from cua_loop.verifier import verify

console = Console()

DEFAULT_WIDTH = int(os.getenv("AEGIS_WIDTH", "3"))
_MARKETPLACE_MODE = os.getenv("AEGIS_MARKETPLACE_MODE", "true").lower() in {"1", "true", "yes"}
_CROSS_BRANCH_RETRY = os.getenv("AEGIS_CROSS_BRANCH_RETRY", "true").lower() in {"1", "true", "yes"}


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


def _run_branch(task: str, url: str | None, branch_index: int, extra_hint: str = "", channel: str = "") -> AttemptResult:
    started = time.time()
    extra_context = (
        f"Wide-scaling branch {branch_index}. Try a distinct strategy. "
        "Prefer safe, reversible actions and verify page state before important clicks."
    )
    if extra_hint:
        extra_context += f"\n\n{extra_hint}"
    try:
        traj = run_single_attempt(task=task, url=url, extra_context=extra_context, channel=channel)
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


def _run_agent_task(agent_task: AgentTask, channel: str) -> dict:
    """Run a specific orchestrated AgentTask and return a compact branch record."""
    started = time.time()
    task_description = getattr(agent_task, "task_description")
    specific_instructions = getattr(agent_task, "specific_instructions", "")
    try:
        traj = run_single_attempt(
            task=task_description,
            url=None,
            extra_context=specific_instructions,
            channel=channel,
        )
        success = not bool(traj.error)
        result_data = traj.model_dump() if hasattr(traj, "model_dump") else traj.dict()
    except Exception as exc:
        success = False
        result_data = {"error": str(exc), "task": task_description}

    return {
        "agent_id": getattr(agent_task, "agent_id", channel),
        "role_name": getattr(agent_task, "role_name", "agent"),
        "result_data": result_data,
        "success": success,
        "duration": time.time() - started,
    }


def run_orchestrated_swarm(task: str, agent_tasks: list[AgentTask]) -> RunResult:
    """Run orchestrator-assigned AgentTasks in parallel and synthesize all results."""
    started = time.time()
    agent_results: list[dict] = []
    width = max(1, len(agent_tasks))

    console.rule(f"[bold]AEGIS orchestrated swarm width={width}")
    with ThreadPoolExecutor(max_workers=width) as pool:
        futures = {
            pool.submit(_run_agent_task, agent_task, f"agent_{i}"): agent_task
            for i, agent_task in enumerate(agent_tasks)
        }
        for future in as_completed(futures):
            agent_task = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "agent_id": getattr(agent_task, "agent_id", "unknown"),
                    "role_name": getattr(agent_task, "role_name", "agent"),
                    "result_data": {"error": str(exc)},
                    "success": False,
                    "duration": 0.0,
                }
            agent_results.append(result)
            console.print(
                f"agent {result['agent_id']} ({result['role_name']}): "
                f"success={result['success']} duration={result['duration']:.1f}s"
            )

    failed_tasks = [
        agent_tasks[i] for i, r in enumerate(agent_results)
        if not r.get("success") and i < len(agent_tasks)
    ]
    retry_results: list[dict] = []
    if failed_tasks:
        from cua_loop.orchestrator import retry_failed_agents
        retry_results = retry_failed_agents(failed_tasks)
        agent_results.extend(retry_results)

    combined_result = synthesize_results(task, agent_results, retry_results)
    attempts: list[AttemptResult] = []
    extracted = {
        "agent_results": sorted(agent_results, key=lambda r: str(r.get("agent_id", ""))),
        "combined_result": combined_result,
    }

    run = RunResult(
        task=task,
        url=None,
        success=any(result.get("success") for result in agent_results),
        attempts=attempts,
        extracted=extracted,
        total_duration_s=time.time() - started,
        selected_attempt_index=None,
    )
    object.__setattr__(run, "combined_result", combined_result)
    path = _persist(run)
    console.print(f"orchestrated swarm synthesized {len(agent_results)} results; saved -> {path}")
    return run


def run_wide_scaling(task: str, url: str | None = None, width: int = DEFAULT_WIDTH) -> RunResult:
    """Run N independent browser attempts in parallel and select the strongest result."""
    started = time.time()
    attempts: list[AttemptResult] = []
    width = max(1, width)

    console.rule(f"[bold]AEGIS wide scaling width={width}")
    with ThreadPoolExecutor(max_workers=width) as pool:
        futures = [pool.submit(_run_branch, task, url, i, channel=f"agent_{i}") for i in range(width)]
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

    Parses the task string into structured fields, generates maximally-filtered
    search URLs for each marketplace, then runs CUA branches concurrently.
    Failed branches are retried with cross-branch learning hints from successful
    ones, and ultimately rescued by fallback programmatic extraction.
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
    site_for_index: dict[int, str] = {idx: name for name, _, idx in site_branches}
    url_for_index: dict[int, str] = {idx: u for _, u, idx in site_branches}

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

    # Cross-branch learning: retry failed branches with demos from successful ones
    if _CROSS_BRANCH_RETRY and should_retry_with_hints(attempts):
        successful = [
            (site_for_index.get(a.attempt_index, ""), a)
            for a in attempts if a.verifier.success
        ]
        failed_branches = [
            (site_for_index.get(a.attempt_index, ""), url_for_index.get(a.attempt_index, ""), a.attempt_index)
            for a in attempts if not a.verifier.success
        ]
        console.print(
            f"[yellow]cross-branch retry:[/yellow] {len(successful)} succeeded, "
            f"retrying {len(failed_branches)} failed with demonstrations"
        )
        retry_index = branch_index
        with ThreadPoolExecutor(max_workers=max(1, len(failed_branches))) as pool:
            retry_futures = {}
            for failed_site, failed_url, _ in failed_branches:
                hint = build_cross_branch_hint(successful, failed_site)
                retry_futures[pool.submit(_run_branch, task, failed_url, retry_index, hint)] = failed_site
                retry_index += 1
            for future in as_completed(retry_futures):
                site_name = retry_futures[future]
                attempt = future.result()
                attempts.append(attempt)
                console.print(
                    f"[{site_name}] retry {attempt.attempt_index}: "
                    f"success={attempt.verifier.success} "
                    f"rows={attempt.verifier.rows_extracted}"
                )

    # Fallback: programmatic extraction for any still-failing branches
    for attempt in list(attempts):
        if attempt.verifier.success:
            continue
        failed_url = url_for_index.get(attempt.attempt_index) or attempt.trajectory.url
        if not failed_url:
            continue
        site_name = site_for_index.get(attempt.attempt_index, "unknown")
        fallback = run_fallback_extraction(failed_url)
        if fallback:
            attempt.trajectory.extracted = fallback
            attempt.trajectory.error = None
            attempt.verifier = VerifierResult(
                success=True, rows_extracted=len(fallback),
                schema_valid=True, reason="fallback programmatic extraction",
            )
            console.print(f"[green][{site_name}] fallback rescued {len(fallback)} listings[/green]")

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
