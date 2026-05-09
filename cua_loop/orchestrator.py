"""Central orchestrator — true three-step swarm coordinator for AEGIS.

General-purpose swarm orchestration is intentionally separate from the legacy
marketplace-specific fan-out:

1. Understand intent with an LLM.
2. Break the work into complementary agent tasks.
3. Synthesize the agent outputs into one coherent result.

The original marketplace search cascade is preserved as
``orchestrate_marketplace()`` for callers that still need AEGIS' specialized
marketplace behavior.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console  # type: ignore[reportMissingImports]

from cua_loop.backends import make_backend
from cua_loop.client import run_single_attempt
from cua_loop.dom_extractor import extract_listings
from cua_loop.fallback_scripts import run_fallback_extraction
from cua_loop.marketplace import (
    MarketplaceScore,
    coerce_marketplace_listing,
    dedupe_across_marketplaces,
    score_marketplace_listing,
)
from cua_loop.query_parser import ParsedQuery, parse_query
from cua_loop.types import Trajectory
from cua_loop.url_params import generate_all_filtered_urls
from cua_loop.verifier import verify

console = Console()

EARLY_STOP_THRESHOLD = int(os.getenv("AEGIS_EARLY_STOP", "10"))
CUA_FAIL_STEP_LIMIT = int(os.getenv("AEGIS_CUA_FAIL_STEPS", "3"))

ORCHESTRATOR_MAX_AGENTS = int(os.getenv("ORCHESTRATOR_MAX_AGENTS", "12"))
ORCHESTRATOR_AGENT_TIMEOUT = float(os.getenv("ORCHESTRATOR_AGENT_TIMEOUT", "180"))
ORCHESTRATOR_LLM_TIMEOUT = float(os.getenv("ORCHESTRATOR_LLM_TIMEOUT", "60"))


@dataclass
class IntentAnalysis:
    true_goal: str
    desired_output_format: str
    success_criteria: list[str] = field(default_factory=list)
    key_entities: list[str] = field(default_factory=list)
    suggested_num_agents: int = 6


@dataclass
class AgentTask:
    agent_id: int
    role_name: str
    task_description: str
    specific_instructions: str
    expected_output: str
    dependencies: list[int] = field(default_factory=list)


@dataclass
class SynthesisResult:
    final_report: str
    key_findings: list[str] = field(default_factory=list)
    confidence_score: float = 0.0
    gaps_or_uncertainties: list[str] = field(default_factory=list)


@dataclass
class SwarmResult:
    task: str
    intent: IntentAnalysis
    agent_tasks: list[AgentTask]
    agent_results: list[dict[str, Any]]
    synthesis: SynthesisResult
    success: bool
    total_duration_s: float
    errors: list[str] = field(default_factory=list)


_swarm_llm_client: Any | None = None
_swarm_llm_model: str | None = None


def _get_swarm_llm() -> tuple[Any, str]:
    """Return an OpenAI-compatible chat client for swarm planning."""
    global _swarm_llm_client, _swarm_llm_model
    if _swarm_llm_client is not None and _swarm_llm_model is not None:
        return _swarm_llm_client, _swarm_llm_model

    from openai import OpenAI  # type: ignore[reportMissingImports]

    api_key = (
        os.getenv("ORCHESTRATOR_API_KEY")
        or os.getenv("VALIDATOR_API_KEY")
        or os.getenv("FIREWORKS_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    if not api_key:
        raise RuntimeError(
            "ORCHESTRATOR_API_KEY, VALIDATOR_API_KEY, FIREWORKS_API_KEY, "
            "or OPENAI_API_KEY is required for swarm orchestration."
        )

    base_url = (
        os.getenv("ORCHESTRATOR_BASE_URL")
        or os.getenv("VALIDATOR_BASE_URL")
        or os.getenv("FIREWORKS_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
    )
    _swarm_llm_model = (
        os.getenv("ORCHESTRATOR_MODEL")
        or os.getenv("VALIDATOR_MODEL")
        or os.getenv("FIREWORKS_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "accounts/fireworks/routers/kimi-k2p6-turbo"
    )

    if base_url:
        _swarm_llm_client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=ORCHESTRATOR_LLM_TIMEOUT,
        )
    else:
        _swarm_llm_client = OpenAI(api_key=api_key, timeout=ORCHESTRATOR_LLM_TIMEOUT)
    return _swarm_llm_client, _swarm_llm_model


def _chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Send a chat completion request and parse a JSON object response."""
    client, model = _get_swarm_llm()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_json_object(raw)


def _parse_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from common LLM response wrappers."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    for candidate in (cleaned, _first_json_object(cleaned), _first_json_array(cleaned)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"tasks": parsed}
    raise RuntimeError("LLM response did not contain valid JSON.")


def _first_json_object(text: str) -> str | None:
    match = re.search(r"\{[\s\S]*\}", text)
    return match.group(0) if match else None


def _first_json_array(text: str) -> str | None:
    match = re.search(r"\[[\s\S]*\]", text)
    return match.group(0) if match else None


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _json_for_prompt(payload: Any, limit: int = 14_000) -> str:
    text = json.dumps(payload, indent=2, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated for prompt length]"


def understand_intent(task: str) -> IntentAnalysis:
    """Step 1: use an LLM to identify the real goal behind the task."""
    system = (
        "Analyze the user's task as a swarm-planning expert. "
        "Identify what the user truly wants, what final format would be most useful, "
        "how success should be judged, which entities matter, and how many agents "
        "would be appropriate. Respond with ONLY a JSON object containing: "
        "{\"true_goal\": str, \"desired_output_format\": str, "
        "\"success_criteria\": [str], \"key_entities\": [str], "
        "\"suggested_num_agents\": int}."
    )
    user = f"Task:\n{task}\n\nReturn JSON only."
    data = _chat_json(system, user, temperature=0.1, max_tokens=1200)

    return IntentAnalysis(
        true_goal=str(data.get("true_goal") or task).strip(),
        desired_output_format=str(
            data.get("desired_output_format") or "Clear, structured written report"
        ).strip(),
        success_criteria=_coerce_str_list(data.get("success_criteria"))
        or ["Directly satisfies the user's requested task"],
        key_entities=_coerce_str_list(data.get("key_entities")),
        suggested_num_agents=_clamp(
            _coerce_int(data.get("suggested_num_agents"), 6),
            1,
            ORCHESTRATOR_MAX_AGENTS,
        ),
    )


def decompose_task(intent: IntentAnalysis, num_agents: int) -> list[AgentTask]:
    """Step 2: use an LLM to create complementary work for N agents."""
    target_agents = _clamp(num_agents, 1, ORCHESTRATOR_MAX_AGENTS)
    system = (
        "You are a swarm task architect. Create genuinely DIFFERENT, complementary "
        "subtasks for browser-capable agents. Do NOT assign the same task with "
        "different strategies. Split by entity, source, perspective, phase, or "
        "artifact so each agent produces non-overlapping work. Avoid creating a "
        "final synthesis task unless it truly depends on prior agent outputs, "
        "because a separate synthesis step will combine the work. Respond with ONLY "
        "a JSON object: {\"tasks\": [{\"agent_id\": int, \"role_name\": str, "
        "\"task_description\": str, \"specific_instructions\": str, "
        "\"expected_output\": str, \"dependencies\": [int]}]}."
    )
    user = _json_for_prompt({"intent": asdict(intent), "num_agents": target_agents})
    data = _chat_json(system, user, temperature=0.35, max_tokens=3500)
    raw_tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw_tasks, list):
        raise RuntimeError("LLM decomposition response must include a tasks list.")

    tasks: list[AgentTask] = []
    used_ids: set[int] = set()
    for idx, item in enumerate(raw_tasks[:target_agents], start=1):
        if not isinstance(item, dict):
            continue
        agent_id = _coerce_int(item.get("agent_id"), idx)
        if agent_id < 1 or agent_id > target_agents or agent_id in used_ids:
            agent_id = idx
        used_ids.add(agent_id)

        dependencies = [
            dep
            for dep in (_coerce_int(value, -1) for value in item.get("dependencies") or [])
            if 1 <= dep <= target_agents and dep != agent_id
        ]
        task = AgentTask(
            agent_id=agent_id,
            role_name=str(item.get("role_name") or f"Agent {agent_id}").strip(),
            task_description=str(item.get("task_description") or intent.true_goal).strip(),
            specific_instructions=str(
                item.get("specific_instructions") or "Complete your assigned subtask precisely."
            ).strip(),
            expected_output=str(
                item.get("expected_output") or intent.desired_output_format
            ).strip(),
            dependencies=sorted(set(dependencies)),
        )
        tasks.append(task)

    tasks = sorted(tasks, key=lambda task: task.agent_id)
    if len(tasks) != target_agents:
        raise RuntimeError(f"LLM returned {len(tasks)} usable tasks; expected {target_agents}.")
    descriptions = [task.task_description.lower().strip() for task in tasks]
    if len(set(descriptions)) != len(descriptions):
        raise RuntimeError("LLM returned duplicate subtasks; expected complementary tasks.")
    return tasks


def synthesize_results(
    agent_tasks: list[AgentTask],
    results: list[dict[str, Any]],
    intent: IntentAnalysis,
) -> SynthesisResult:
    """Step 3: use an LLM to combine agent work into the final answer."""
    system = (
        "You are a synthesis lead combining a swarm's outputs. Produce one coherent "
        "final answer in the user's desired format. Reconcile conflicts, highlight "
        "the strongest findings, and explicitly name gaps or uncertainty. Respond "
        "with ONLY a JSON object: {\"final_report\": str, \"key_findings\": [str], "
        "\"confidence_score\": float, \"gaps_or_uncertainties\": [str]}."
    )
    payload = {
        "intent": asdict(intent),
        "agent_tasks": [asdict(task) for task in agent_tasks],
        "agent_results": results,
    }
    data = _chat_json(system, _json_for_prompt(payload), temperature=0.2, max_tokens=5000)
    return SynthesisResult(
        final_report=str(data.get("final_report") or "").strip(),
        key_findings=_coerce_str_list(data.get("key_findings")),
        confidence_score=_clamp_float(_coerce_float(data.get("confidence_score"), 0.0), 0.0, 1.0),
        gaps_or_uncertainties=_coerce_str_list(data.get("gaps_or_uncertainties")),
    )


def _dependency_context(dependency_results: dict[int, dict[str, Any]]) -> str:
    if not dependency_results:
        return "No dependency outputs."
    summaries = []
    for agent_id, result in sorted(dependency_results.items()):
        summary = result.get("final_message") or result.get("extracted") or result.get("error")
        summaries.append(f"Agent {agent_id}: {summary}")
    return "\n".join(summaries)


def _agent_extra_context(
    agent_task: AgentTask,
    intent: IntentAnalysis,
    dependency_results: dict[int, dict[str, Any]],
) -> str:
    return (
        f"TRUE GOAL: {intent.true_goal}\n"
        f"DESIRED FINAL FORMAT: {intent.desired_output_format}\n"
        f"SUCCESS CRITERIA: {', '.join(intent.success_criteria)}\n"
        f"KEY ENTITIES: {', '.join(intent.key_entities) or 'None specified'}\n\n"
        f"YOUR SWARM ROLE: {agent_task.role_name}\n"
        f"YOUR SPECIFIC TASK: {agent_task.task_description}\n"
        f"SPECIFIC INSTRUCTIONS: {agent_task.specific_instructions}\n"
        f"EXPECTED OUTPUT: {agent_task.expected_output}\n\n"
        "You are one specialist in a coordinated swarm. Do only your assigned "
        "complementary slice; do not duplicate other agents' work. Return concise, "
        "source-aware findings that can be synthesized later.\n\n"
        f"DEPENDENCY OUTPUTS:\n{_dependency_context(dependency_results)}"
    )


def _agent_error_result(agent_task: AgentTask, error: str, duration_s: float) -> dict[str, Any]:
    return {
        "agent_id": agent_task.agent_id,
        "role_name": agent_task.role_name,
        "task_description": agent_task.task_description,
        "expected_output": agent_task.expected_output,
        "success": False,
        "final_message": None,
        "extracted": None,
        "error": error,
        "duration_s": duration_s,
        "steps": 0,
    }


def _trajectory_result(agent_task: AgentTask, traj: Trajectory, duration_s: float) -> dict[str, Any]:
    success = bool(traj.extracted is not None or traj.final_message) and not traj.error
    return {
        "agent_id": agent_task.agent_id,
        "role_name": agent_task.role_name,
        "task_description": agent_task.task_description,
        "expected_output": agent_task.expected_output,
        "success": success,
        "final_message": traj.final_message,
        "extracted": traj.extracted,
        "error": traj.error,
        "duration_s": duration_s,
        "steps": len(traj.steps),
    }


def _run_agent_task(
    agent_task: AgentTask,
    intent: IntentAnalysis,
    dependency_results: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    started = time.time()
    try:
        traj = run_single_attempt(
            task=agent_task.task_description,
            url=None,
            extra_context=_agent_extra_context(agent_task, intent, dependency_results),
            channel=f"swarm_agent_{agent_task.agent_id}",
        )
        return _trajectory_result(agent_task, traj, time.time() - started)
    except Exception as exc:
        return _agent_error_result(agent_task, str(exc), time.time() - started)


def _run_agent_tasks(
    agent_tasks: list[AgentTask],
    intent: IntentAnalysis,
    max_workers: int,
    agent_timeout: float,
) -> list[dict[str, Any]]:
    """Run agent tasks with dependency awareness and per-agent timeout records."""
    if not agent_tasks:
        return []

    pending = {task.agent_id: task for task in agent_tasks}
    completed: dict[int, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    running: dict[Future, tuple[AgentTask, float]] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(agent_tasks))))
    try:
        while pending or running:
            for agent_id, task in list(pending.items()):
                if all(dep in completed for dep in task.dependencies):
                    dependency_results = {
                        dep: completed[dep]
                        for dep in task.dependencies
                        if dep in completed
                    }
                    running[pool.submit(_run_agent_task, task, intent, dependency_results)] = (
                        task,
                        time.time(),
                    )
                    del pending[agent_id]

            if not running:
                for task in sorted(pending.values(), key=lambda item: item.agent_id):
                    missing = [dep for dep in task.dependencies if dep not in completed]
                    result = _agent_error_result(
                        task,
                        f"Unresolved dependencies: {missing}",
                        0.0,
                    )
                    completed[task.agent_id] = result
                    results.append(result)
                break

            done, _ = wait(list(running.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                task, _started = running.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = _agent_error_result(task, str(exc), 0.0)
                completed[task.agent_id] = result
                results.append(result)

            now = time.time()
            for future, (task, started) in list(running.items()):
                if now - started >= agent_timeout:
                    future.cancel()
                    running.pop(future, None)
                    result = _agent_error_result(
                        task,
                        f"Agent timed out after {agent_timeout:.0f}s",
                        now - started,
                    )
                    completed[task.agent_id] = result
                    results.append(result)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return sorted(results, key=lambda result: int(result.get("agent_id", 0)))


def _failure_swarm_result(
    task: str,
    started: float,
    error: str,
    intent: IntentAnalysis | None = None,
    agent_tasks: list[AgentTask] | None = None,
    agent_results: list[dict[str, Any]] | None = None,
) -> SwarmResult:
    safe_intent = intent or IntentAnalysis(
        true_goal=task,
        desired_output_format="Unable to determine because intent analysis failed.",
        success_criteria=[],
        key_entities=[],
        suggested_num_agents=0,
    )
    return SwarmResult(
        task=task,
        intent=safe_intent,
        agent_tasks=agent_tasks or [],
        agent_results=agent_results or [],
        synthesis=SynthesisResult(
            final_report="",
            key_findings=[],
            confidence_score=0.0,
            gaps_or_uncertainties=[error],
        ),
        success=False,
        total_duration_s=time.time() - started,
        errors=[error],
    )


def _fallback_synthesis(
    results: list[dict[str, Any]],
    error: str,
) -> SynthesisResult:
    fragments = []
    for result in results:
        if result.get("final_message"):
            fragments.append(f"Agent {result['agent_id']}: {result['final_message']}")
        elif result.get("extracted") is not None:
            fragments.append(f"Agent {result['agent_id']}: {result['extracted']}")
    return SynthesisResult(
        final_report="\n\n".join(fragments),
        key_findings=[],
        confidence_score=0.0,
        gaps_or_uncertainties=[f"Synthesis failed: {error}"],
    )


def run_swarm_orchestration(task: str, num_agents: int = 6) -> SwarmResult:
    """Run the general-purpose three-step swarm orchestrator."""
    started = time.time()
    errors: list[str] = []

    try:
        intent = understand_intent(task)
    except Exception as exc:
        return _failure_swarm_result(task, started, f"intent analysis failed: {exc}")

    target_agents = _clamp(
        num_agents if num_agents > 0 else intent.suggested_num_agents,
        1,
        ORCHESTRATOR_MAX_AGENTS,
    )

    try:
        agent_tasks = decompose_task(intent, target_agents)
    except Exception as exc:
        return _failure_swarm_result(
            task,
            started,
            f"task decomposition failed: {exc}",
            intent=intent,
        )

    console.rule(f"[bold]AEGIS swarm: {len(agent_tasks)} complementary agents")
    for agent_task in agent_tasks:
        dep_text = f" deps={agent_task.dependencies}" if agent_task.dependencies else ""
        console.print(
            f"  agent {agent_task.agent_id}: {agent_task.role_name} — "
            f"{agent_task.task_description}{dep_text}"
        )

    agent_results = _run_agent_tasks(
        agent_tasks=agent_tasks,
        intent=intent,
        max_workers=target_agents,
        agent_timeout=ORCHESTRATOR_AGENT_TIMEOUT,
    )
    for result in agent_results:
        if result.get("error"):
            errors.append(f"agent {result.get('agent_id')} failed: {result.get('error')}")

    synthesis_failed = False
    try:
        synthesis = synthesize_results(agent_tasks, agent_results, intent)
    except Exception as exc:
        synthesis_failed = True
        errors.append(f"synthesis failed: {exc}")
        synthesis = _fallback_synthesis(agent_results, str(exc))

    any_agent_succeeded = any(result.get("success") for result in agent_results)
    success = any_agent_succeeded and bool(synthesis.final_report.strip()) and not synthesis_failed
    return SwarmResult(
        task=task,
        intent=intent,
        agent_tasks=agent_tasks,
        agent_results=agent_results,
        synthesis=synthesis,
        success=success,
        total_duration_s=time.time() - started,
        errors=errors,
    )


SEARCH_STRATEGIES = [
    {
        "name": "keyword_default",
        "hint": "Search using the exact keywords from the query. Do not modify the search terms.",
    },
    {
        "name": "category_browse",
        "hint": "Instead of searching by keyword, browse the relevant category. "
                "Navigate to the category page (e.g. Furniture, Electronics, Musical Instruments) "
                "and scan listings visually.",
    },
    {
        "name": "price_sorted",
        "hint": "Search by keyword but sort results by price (lowest first). "
                "Focus on finding the cheapest options that match the criteria.",
    },
    {
        "name": "newest_first",
        "hint": "Search by keyword but sort by newest listings first. "
                "Recently posted items are more likely to still be available.",
    },
]


@dataclass
class BranchConfig:
    branch_index: int
    marketplace: str
    url: str
    strategy: dict[str, str]
    task: str


class OrchestratorResult(BaseModel):
    task: str
    parsed_query: ParsedQuery
    success: bool
    total_listings_found: int = 0
    total_branches: int = 0
    branches_succeeded: int = 0
    branches_failed: int = 0
    early_stopped: bool = False
    marketplace_scores: list[MarketplaceScore] | None = None
    raw_listings: list[dict[str, Any]] = Field(default_factory=list)
    total_duration_s: float = 0.0
    branch_details: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class _SharedState:
    """Thread-safe accumulator for cross-branch results."""

    def __init__(self, early_stop: int = EARLY_STOP_THRESHOLD):
        self._lock = threading.Lock()
        self._listings: list[dict[str, Any]] = []
        self._early_stop = early_stop
        self.should_stop = threading.Event()

    def add_listings(self, items: list[dict[str, Any]]) -> int:
        with self._lock:
            self._listings.extend(items)
            total = len(self._listings)
            if total >= self._early_stop:
                self.should_stop.set()
            return total

    @property
    def listings(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._listings)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._listings)


def _run_cua_branch(
    config: BranchConfig,
    shared: _SharedState,
) -> dict[str, Any]:
    """Run a single marketplace CUA branch with adaptive mode cascading."""
    result: dict[str, Any] = {
        "branch_index": config.branch_index,
        "marketplace": config.marketplace,
        "strategy": config.strategy["name"],
        "mode": "cua",
        "success": False,
        "listings_found": 0,
        "duration_s": 0.0,
    }
    started = time.time()

    if shared.should_stop.is_set():
        result["mode"] = "skipped"
        result["duration_s"] = time.time() - started
        return result

    extra_context = (
        f"Branch {config.branch_index} ({config.marketplace}). "
        f"Strategy: {config.strategy['hint']}"
    )

    try:
        traj = run_single_attempt(
            task=config.task,
            url=config.url,
            extra_context=extra_context,
        )
        v = verify(traj)
        result["verifier_success"] = v.success
        result["rows_extracted"] = v.rows_extracted

        if v.success and isinstance(traj.extracted, list) and len(traj.extracted) > 0:
            shared.add_listings(traj.extracted)
            result["success"] = True
            result["listings_found"] = len(traj.extracted)
            result["mode"] = "cua"
            result["duration_s"] = time.time() - started
            return result
    except Exception as exc:
        result["error"] = str(exc)

    if shared.should_stop.is_set():
        result["duration_s"] = time.time() - started
        return result

    try:
        backend = make_backend()
        with backend as b:
            b.navigate(config.url)
            if hasattr(b, "wait_for_page_load"):
                b.wait_for_page_load()
            else:
                b.wait(2)
            dom_listings = extract_listings(b, marketplace=config.marketplace)
            if dom_listings:
                shared.add_listings(dom_listings)
                result["success"] = True
                result["listings_found"] = len(dom_listings)
                result["mode"] = "dom"
                result["duration_s"] = time.time() - started
                return result
    except Exception:
        pass

    if shared.should_stop.is_set():
        result["duration_s"] = time.time() - started
        return result

    try:
        fallback_listings = run_fallback_extraction(
            url=config.url,
            marketplace=config.marketplace,
        )
        if fallback_listings:
            shared.add_listings(fallback_listings)
            result["success"] = True
            result["listings_found"] = len(fallback_listings)
            result["mode"] = "fallback"
            result["duration_s"] = time.time() - started
            return result
    except Exception:
        pass

    result["duration_s"] = time.time() - started
    return result


def _assign_strategies(
    marketplaces: dict[str, str],
) -> list[BranchConfig]:
    """Assign diverse strategies across marketplace URLs."""
    branches: list[BranchConfig] = []
    idx = 0
    for mp_name, url in marketplaces.items():
        strategy = SEARCH_STRATEGIES[idx % len(SEARCH_STRATEGIES)]
        branches.append(BranchConfig(
            branch_index=idx,
            marketplace=mp_name,
            url=url,
            strategy=strategy,
            task="",
        ))
        idx += 1
    return branches


def orchestrate_marketplace(
    query: str,
    max_browsers: int = 12,
    location: str | None = None,
    skip_login_required: bool = True,
    early_stop: int = EARLY_STOP_THRESHOLD,
) -> OrchestratorResult:
    """Specialized multi-marketplace search orchestrator preserved from AEGIS."""
    started = time.time()
    parsed = parse_query(query)

    urls = generate_all_filtered_urls(
        parsed_query=parsed,
        location=location,
        skip_login_required=skip_login_required,
    )

    branches = _assign_strategies(urls)
    for branch in branches:
        branch.task = query

    branches = branches[:max_browsers]
    shared = _SharedState(early_stop=early_stop)

    console.rule(f"[bold]AEGIS marketplace orchestrator: {len(branches)} branches across {len(urls)} marketplaces")
    for branch in branches:
        console.print(f"  branch {branch.branch_index}: {branch.marketplace} / {branch.strategy['name']}")

    branch_details: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(len(branches), max_browsers)) as pool:
        futures: dict[Future, BranchConfig] = {
            pool.submit(_run_cua_branch, branch, shared): branch
            for branch in branches
        }
        for future in wait(futures.keys())[0]:
            branch_cfg = futures[future]
            try:
                detail = future.result()
            except Exception as exc:
                detail = {
                    "branch_index": branch_cfg.branch_index,
                    "marketplace": branch_cfg.marketplace,
                    "strategy": branch_cfg.strategy["name"],
                    "mode": "error",
                    "success": False,
                    "error": str(exc),
                    "duration_s": 0.0,
                }
            branch_details.append(detail)
            status = "[green]OK[/green]" if detail.get("success") else "[red]FAIL[/red]"
            console.print(
                f"  branch {detail['branch_index']} ({detail['marketplace']}): "
                f"{status} mode={detail.get('mode')} listings={detail.get('listings_found', 0)}"
            )

    all_listings = shared.listings
    succeeded = sum(1 for detail in branch_details if detail.get("success"))

    mp_scores = None
    if all_listings:
        scores = []
        for item in all_listings:
            if not isinstance(item, dict):
                continue
            try:
                listing = coerce_marketplace_listing(item)
                scores.append(score_marketplace_listing(listing, query))
            except Exception:
                continue
        if scores:
            mp_scores = dedupe_across_marketplaces(scores)

    result = OrchestratorResult(
        task=query,
        parsed_query=parsed,
        success=len(all_listings) > 0,
        total_listings_found=len(all_listings),
        total_branches=len(branches),
        branches_succeeded=succeeded,
        branches_failed=len(branches) - succeeded,
        early_stopped=shared.should_stop.is_set(),
        marketplace_scores=mp_scores,
        raw_listings=all_listings,
        total_duration_s=time.time() - started,
        branch_details=sorted(branch_details, key=lambda item: item["branch_index"]),
    )

    console.rule("[bold]Marketplace Orchestrator Summary")
    console.print(
        f"  {succeeded}/{len(branches)} branches succeeded | "
        f"{len(all_listings)} total listings | "
        f"{len(mp_scores or [])} after dedup | "
        f"early_stop={result.early_stopped} | "
        f"{result.total_duration_s:.1f}s"
    )

    return result


def orchestrate(
    query: str,
    max_browsers: int = 12,
    location: str | None = None,
    skip_login_required: bool = True,
    early_stop: int = EARLY_STOP_THRESHOLD,
) -> OrchestratorResult:
    """Backward-compatible alias for the marketplace orchestrator."""
    return orchestrate_marketplace(
        query=query,
        max_browsers=max_browsers,
        location=location,
        skip_login_required=skip_login_required,
        early_stop=early_stop,
    )


def main() -> None:
    import argparse

    from dotenv import load_dotenv  # type: ignore[reportMissingImports]

    load_dotenv()

    parser = argparse.ArgumentParser(description="AEGIS swarm orchestrator")
    parser.add_argument("--query", "--task", dest="task", required=True, help="Natural language task")
    parser.add_argument("--num-agents", type=int, default=6)
    parser.add_argument("--marketplace", action="store_true", help="Use legacy marketplace search orchestration")
    parser.add_argument("--max-browsers", type=int, default=12)
    parser.add_argument("--location", type=str, default=None)
    parser.add_argument("--early-stop", type=int, default=EARLY_STOP_THRESHOLD)
    args = parser.parse_args()

    if args.marketplace:
        result = orchestrate_marketplace(
            query=args.task,
            max_browsers=args.max_browsers,
            location=args.location,
            early_stop=args.early_stop,
        )
        console.print(f"\nsuccess={result.success} listings={result.total_listings_found}")
        if result.marketplace_scores:
            console.print(f"top scores: {len(result.marketplace_scores)} scored+deduped listings")
            for score in result.marketplace_scores[:5]:
                console.print(f"  {score.listing.title}: ${score.listing.price} (score={score.score:.1f})")
        return

    swarm = run_swarm_orchestration(args.task, num_agents=args.num_agents)
    console.print(f"\nsuccess={swarm.success} confidence={swarm.synthesis.confidence_score:.2f}")
    if swarm.errors:
        console.print(f"errors: {swarm.errors}")
    console.print(swarm.synthesis.final_report)


if __name__ == "__main__":
    main()
