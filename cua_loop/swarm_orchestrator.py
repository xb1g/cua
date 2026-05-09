"""Swarm orchestrator — 3-step intelligent multi-agent coordination.

1. UNDERSTAND INTENT: Analyze what the user truly wants
2. BREAK DOWN TASKS: Create complementary subtasks for each agent  
3. COMBINE WORKS: Synthesize all agent outputs into coherent result
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

console = Console()


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class IntentAnalysis:
    """Result of Step 1: Understanding the user's true intent."""
    true_goal: str
    desired_output_format: str
    success_criteria: list[str]
    key_entities: list[str]
    suggested_num_agents: int
    domain_hints: list[str] = field(default_factory=list)
    complexity_score: float = 0.5  # 0-1


@dataclass  
class AgentTask:
    """A specific subtask assigned to one agent."""
    agent_id: int
    role_name: str
    task_description: str
    specific_instructions: str
    expected_output: str
    dependencies: list[int] = field(default_factory=list)


@dataclass
class AgentResult:
    """Result from one agent's work."""
    agent_id: int
    role_name: str
    success: bool
    result_data: Any
    duration_s: float
    error: str | None = None


@dataclass
class SynthesisResult:
    """Result of Step 3: Combined output from all agents."""
    final_report: str
    key_findings: list[str]
    confidence_score: float
    gaps_or_uncertainties: list[str]
    agent_contributions: dict[int, str] = field(default_factory=dict)


@dataclass
class SwarmResult:
    """Complete result of an orchestrated swarm run."""
    intent: IntentAnalysis
    agent_tasks: list[AgentTask]
    agent_results: list[AgentResult]
    synthesis: SynthesisResult
    total_duration_s: float
    success: bool


# ---------------------------------------------------------------------------
# LLM helper (reuse validator pattern)
# ---------------------------------------------------------------------------

class _LLMHelper:
    """Lightweight LLM wrapper using existing env/API infrastructure."""
    
    def __init__(self):
        from openai import OpenAI
        
        api_key = os.getenv("VALIDATOR_API_KEY") or os.getenv("FIREWORKS_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "VALIDATOR_API_KEY or FIREWORKS_API_KEY required for swarm orchestrator."
            )
        
        base_url = os.getenv(
            "VALIDATOR_BASE_URL",
            os.getenv("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"),
        )
        self._model = os.getenv(
            "VALIDATOR_MODEL",
            os.getenv("FIREWORKS_MODEL", "accounts/fireworks/routers/kimi-k2p6-turbo"),
        )
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
    
    def chat(self, system: str, user: str, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    
    def parse_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}


_llm: _LLMHelper | None = None


def _get_llm() -> _LLMHelper:
    global _llm
    if _llm is None:
        _llm = _LLMHelper()
    return _llm


# ---------------------------------------------------------------------------
# Step 1: Understand Intent
# ---------------------------------------------------------------------------

def understand_intent(task: str) -> IntentAnalysis:
    """Analyze the user's task to understand true intent and requirements."""
    
    console.rule("[bold blue]Step 1: Understanding Intent[/bold blue]")
    console.print(f"Task: {task[:100]}...")
    
    system = (
        "You are an expert task analyst. Given a user's request, analyze what they truly want.\n"
        "Respond with ONLY a JSON object:\n"
        '{\n'
        '  "true_goal": "what the user really wants to achieve",\n'
        '  "desired_output_format": "expected format of the final deliverable",\n'
        '  "success_criteria": ["criterion 1", "criterion 2", ...],\n'
        '  "key_entities": ["entity 1", "entity 2", ...],\n'
        '  "suggested_num_agents": number (3-12),\n'
        '  "domain_hints": ["relevant domain 1", "domain 2", ...],\n'
        '  "complexity_score": float (0.0-1.0)\n'
        '}'
    )
    
    user = f"Analyze this task and extract the true intent:\n\n{task}"
    
    try:
        llm = _get_llm()
        text = llm.chat(system, user, temperature=0.1, max_tokens=1024)
        data = llm.parse_json(text)
        
        intent = IntentAnalysis(
            true_goal=data.get("true_goal", task),
            desired_output_format=data.get("desired_output_format", "structured report"),
            success_criteria=data.get("success_criteria", []),
            key_entities=data.get("key_entities", []),
            suggested_num_agents=data.get("suggested_num_agents", 6),
            domain_hints=data.get("domain_hints", []),
            complexity_score=float(data.get("complexity_score", 0.5)),
        )
        
        console.print(f"[green]Intent: {intent.true_goal[:80]}...[/green]")
        console.print(f"[dim]Entities: {', '.join(intent.key_entities)} | Suggested agents: {intent.suggested_num_agents}[/dim]")
        return intent
        
    except Exception as exc:
        console.print(f"[red]Intent analysis failed: {exc}. Using fallback.[/red]")
        return IntentAnalysis(
            true_goal=task,
            desired_output_format="structured report",
            success_criteria=["Complete the task successfully"],
            key_entities=[],
            suggested_num_agents=6,
            complexity_score=0.5,
        )


# ---------------------------------------------------------------------------
# Step 2: Break Down Tasks
# ---------------------------------------------------------------------------

def decompose_task(intent: IntentAnalysis, num_agents: int) -> list[AgentTask]:
    """Break the intent into N complementary subtasks for different agents."""
    
    console.rule(f"[bold blue]Step 2: Breaking Down into {num_agents} Tasks[/bold blue]")
    
    system = (
        "You are an expert task decomposer. Given a goal and number of agents, create\n"
        "genuinely DIFFERENT subtasks that are COMPLEMENTARY (not redundant).\n"
        "Each agent should handle a distinct aspect that, when combined, covers the whole goal.\n\n"
        "Respond with ONLY a JSON array of objects:\n"
        '[\n'
        '  {\n'
        '    "agent_id": 0,\n'
        '    "role_name": "e.g. Product Researcher",\n'
        '    "task_description": "specific focused task for this agent",\n'
        '    "specific_instructions": "detailed guidance on approach",\n'
        '    "expected_output": "what this agent should produce",\n'
        '    "dependencies": []\n'
        '  },\n'
        '  ...\n'
        ']\n\n'
        "Rules:\n"
        "- role_name should be descriptive and unique\n"
        "- task_description should be specific and actionable\n"
        "- specific_instructions should guide the approach\n"
        "- expected_output should be clear and verifiable\n"
        "- dependencies: list of agent_ids that must finish before this one starts (optional)\n"
        "- NEVER give the same task to multiple agents\n"
        "- One agent can be a 'Synthesizer' that combines others' work"
    )
    
    user = (
        f"Goal: {intent.true_goal}\n"
        f"Output format needed: {intent.desired_output_format}\n"
        f"Success criteria: {', '.join(intent.success_criteria)}\n"
        f"Key entities to cover: {', '.join(intent.key_entities)}\n"
        f"Number of agents: {num_agents}\n\n"
        f"Create {num_agents} complementary subtasks."
    )
    
    try:
        llm = _get_llm()
        text = llm.chat(system, user, temperature=0.3, max_tokens=2048)
        data = llm.parse_json(text)
        
        tasks: list[AgentTask] = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                tasks.append(AgentTask(
                    agent_id=item.get("agent_id", len(tasks)),
                    role_name=item.get("role_name", f"Agent {len(tasks)}"),
                    task_description=item.get("task_description", intent.true_goal),
                    specific_instructions=item.get("specific_instructions", ""),
                    expected_output=item.get("expected_output", "results"),
                    dependencies=item.get("dependencies", []),
                ))
        
        # If we got fewer tasks than requested, pad with generic ones
        while len(tasks) < num_agents:
            idx = len(tasks)
            tasks.append(AgentTask(
                agent_id=idx,
                role_name=f"Research Agent {idx+1}",
                task_description=f"Research aspect #{idx+1} of: {intent.true_goal}",
                specific_instructions=f"Focus on a distinct angle. Previous agents cover: {', '.join(t.role_name for t in tasks)}",
                expected_output="detailed findings",
            ))
        
        # Limit to requested number
        tasks = tasks[:num_agents]
        
        for t in tasks:
            console.print(f"[cyan]Agent {t.agent_id}: {t.role_name}[/cyan] — {t.task_description[:60]}...")
        
        return tasks
        
    except Exception as exc:
        console.print(f"[red]Task decomposition failed: {exc}. Using fallback.[/red]")
        # Fallback: create simple evenly-split tasks
        tasks = []
        entities = intent.key_entities or ["aspect 1", "aspect 2", "aspect 3"]
        for i in range(num_agents):
            entity = entities[i % len(entities)] if entities else f"part {i+1}"
            tasks.append(AgentTask(
                agent_id=i,
                role_name=f"{entity.title()} Researcher",
                task_description=f"Research and analyze {entity} in the context of: {intent.true_goal}",
                specific_instructions="Be thorough and extract specific facts, not generalities.",
                expected_output=f"detailed findings about {entity}",
            ))
        return tasks


# ---------------------------------------------------------------------------
# Step 3: Combine Works (Synthesis)
# ---------------------------------------------------------------------------

def synthesize_results(
    agent_tasks: list[AgentTask],
    agent_results: list[AgentResult],
    intent: IntentAnalysis,
) -> SynthesisResult:
    """Combine all agent outputs into a coherent final result."""
    
    console.rule("[bold blue]Step 3: Synthesizing Results[/bold blue]")
    
    # Build summary of all results
    results_summary = []
    for result in agent_results:
        task = next((t for t in agent_tasks if t.agent_id == result.agent_id), None)
        role = task.role_name if task else f"Agent {result.agent_id}"
        status = "✓" if result.success else "✗"
        data_str = str(result.result_data)[:500] if result.result_data else "None"
        results_summary.append(
            f"--- {role} (Agent {result.agent_id}) {status} ---\n"
            f"Success: {result.success}\n"
            f"Output: {data_str}\n"
        )
    
    results_text = "\n".join(results_summary)
    
    system = (
        "You are an expert synthesis analyst. Given multiple agent outputs, create a unified, coherent result.\n"
        "Respond with ONLY a JSON object:\n"
        '{\n'
        '  "final_report": "comprehensive combined report (can be long, multiple paragraphs)",\n'
        '  "key_findings": ["finding 1", "finding 2", ...],\n'
        '  "confidence_score": float (0.0-1.0),\n'
        '  "gaps_or_uncertainties": ["gap 1", "gap 2", ...]\n'
        '}'
    )
    
    user = (
        f"Original goal: {intent.true_goal}\n"
        f"Desired output format: {intent.desired_output_format}\n"
        f"Success criteria: {', '.join(intent.success_criteria)}\n\n"
        f"Agent outputs:\n{results_text}\n\n"
        f"Synthesize these into a coherent {intent.desired_output_format}."
    )
    
    try:
        llm = _get_llm()
        text = llm.chat(system, user, temperature=0.2, max_tokens=4096)
        data = llm.parse_json(text)
        
        synthesis = SynthesisResult(
            final_report=data.get("final_report", "Synthesis unavailable."),
            key_findings=data.get("key_findings", []),
            confidence_score=float(data.get("confidence_score", 0.5)),
            gaps_or_uncertainties=data.get("gaps_or_uncertainties", []),
        )
        
        # Build contributions map
        for result in agent_results:
            task = next((t for t in agent_tasks if t.agent_id == result.agent_id), None)
            if task:
                synthesis.agent_contributions[result.agent_id] = (
                    f"{task.role_name}: {'✓' if result.success else '✗'} — "
                    f"{str(result.result_data)[:200]}"
                )
        
        console.print(f"[green]Synthesis complete. Confidence: {synthesis.confidence_score:.0%}[/green]")
        console.print(f"[dim]Findings: {len(synthesis.key_findings)} | Gaps: {len(synthesis.gaps_or_uncertainties)}[/dim]")
        
        return synthesis
        
    except Exception as exc:
        console.print(f"[red]Synthesis failed: {exc}. Using fallback aggregation.[/red]")
        
        # Fallback: concatenate all results
        parts = []
        for result in agent_results:
            task = next((t for t in agent_tasks if t.agent_id == result.agent_id), None)
            role = task.role_name if task else f"Agent {result.agent_id}"
            parts.append(f"\n## {role}\n{str(result.result_data)[:1000]}")
        
        return SynthesisResult(
            final_report=f"# Combined Results\n" + "\n".join(parts),
            key_findings=[f"Agent {r.agent_id} contributed: {str(r.result_data)[:100]}" for r in agent_results if r.success],
            confidence_score=sum(1 for r in agent_results if r.success) / max(1, len(agent_results)),
            gaps_or_uncertainties=["Synthesis LLM failed; using raw aggregation."],
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_swarm_orchestration(task: str, num_agents: int = 6) -> SwarmResult:
    """Full 3-step orchestrated swarm: understand -> decompose -> run -> synthesize."""
    
    started = time.time()
    
    # Step 1: Understand intent
    intent = understand_intent(task)
    num_agents = min(max(2, num_agents), 12)
    
    # Step 2: Decompose into subtasks
    agent_tasks = decompose_task(intent, num_agents)
    
    # Step 3 is done by the caller (scaling.py) after running agents
    # We return the decomposition for execution
    
    return SwarmResult(
        intent=intent,
        agent_tasks=agent_tasks,
        agent_results=[],
        synthesis=SynthesisResult(
            final_report="",
            key_findings=[],
            confidence_score=0.0,
            gaps_or_uncertainties=[],
        ),
        total_duration_s=time.time() - started,
        success=False,  # Will be set after execution
    )
