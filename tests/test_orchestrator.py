"""Tests for the AEGIS orchestrator."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cua_loop.orchestrator import (
    AgentTask,
    IntentAnalysis,
    SEARCH_STRATEGIES,
    SynthesisResult,
    SwarmResult,
    OrchestratorResult,
    _SharedState,
    _assign_strategies,
    decompose_task,
    orchestrate,
    run_swarm_orchestration,
    understand_intent,
)
import cua_loop.orchestrator as orchestrator_module


class TestSharedState:
    def test_add_listings_accumulates(self):
        state = _SharedState(early_stop=10)
        state.add_listings([{"title": "A"}])
        state.add_listings([{"title": "B"}, {"title": "C"}])
        assert state.count == 3
        assert len(state.listings) == 3

    def test_early_stop_fires(self):
        state = _SharedState(early_stop=3)
        assert not state.should_stop.is_set()
        state.add_listings([{"title": f"item_{i}"} for i in range(5)])
        assert state.should_stop.is_set()

    def test_thread_safe(self):
        import threading
        state = _SharedState(early_stop=1000)
        def add_batch():
            for _ in range(50):
                state.add_listings([{"title": "x"}])
        threads = [threading.Thread(target=add_batch) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state.count == 200


class TestStrategyAssignment:
    def test_assigns_diverse_strategies(self):
        urls = {"craigslist": "https://cl.example.com", "reverb": "https://reverb.example.com",
                "mercari": "https://mercari.example.com", "offerup": "https://offerup.example.com"}
        branches = _assign_strategies(urls)
        strategies = [b.strategy["name"] for b in branches]
        assert len(set(strategies)) == 4

    def test_branch_indices_sequential(self):
        urls = {"a": "https://a.com", "b": "https://b.com"}
        branches = _assign_strategies(urls)
        assert [b.branch_index for b in branches] == [0, 1]

    def test_all_strategies_exist(self):
        assert len(SEARCH_STRATEGIES) == 4
        for s in SEARCH_STRATEGIES:
            assert "name" in s
            assert "hint" in s


class TestOrchestrate:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CUA_TRAJ_DIR", str(tmp_path / "trajectories"))
        monkeypatch.setenv("CUA_MAX_STEPS", "3")
        monkeypatch.setenv("AEGIS_EARLY_STOP", "5")

    def _mock_run_single(self, extracted=None):
        def _run(**kwargs):
            from cua_loop.types import Trajectory
            traj = Trajectory(task=kwargs.get("task", ""), url=kwargs.get("url"))
            traj.extracted = extracted
            traj.final_message = "Found listings"
            return traj
        return _run

    def _mock_verify(self, success=True, rows=3):
        return MagicMock(return_value=MagicMock(
            success=success, rows_extracted=rows, schema_valid=True, reason="ok"
        ))

    def test_orchestrate_returns_result(self, monkeypatch):
        listings = [
            {"title": "Couch", "price": 150.0, "marketplace": "craigslist"},
            {"title": "Chair", "price": 100.0, "marketplace": "craigslist"},
        ]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=self._mock_run_single(listings)),
            patch("cua_loop.orchestrator.verify", self._mock_verify()),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"craigslist": "https://cl.example.com"}),
        ):
            result = orchestrate("used couch under $200", max_browsers=1)

        assert isinstance(result, OrchestratorResult)
        assert result.success
        assert result.total_listings_found >= 2


class TestSwarmOrchestration:
    def test_understand_intent_parses_llm_response(self, monkeypatch):
        def fake_chat_json(system, user, *, temperature=0.2, max_tokens=1024):
            assert "Analyze the user's task" in system
            assert "research AI companies" in user
            return {
                "true_goal": "Compare major AI companies",
                "desired_output_format": "Markdown comparison matrix",
                "success_criteria": ["covers products", "covers pricing"],
                "key_entities": ["OpenAI", "Anthropic", "Google DeepMind"],
                "suggested_num_agents": 4,
            }

        monkeypatch.setattr(orchestrator_module, "_chat_json", fake_chat_json)

        intent = understand_intent("research AI companies")

        assert intent.true_goal == "Compare major AI companies"
        assert intent.desired_output_format == "Markdown comparison matrix"
        assert intent.success_criteria == ["covers products", "covers pricing"]
        assert intent.key_entities == ["OpenAI", "Anthropic", "Google DeepMind"]
        assert intent.suggested_num_agents == 4

    def test_decompose_task_creates_complementary_agent_tasks(self, monkeypatch):
        intent = IntentAnalysis(
            true_goal="Compare major AI companies",
            desired_output_format="Markdown comparison matrix",
            success_criteria=["covers products", "covers pricing"],
            key_entities=["OpenAI", "Anthropic", "Google DeepMind"],
            suggested_num_agents=4,
        )

        def fake_chat_json(system, user, *, temperature=0.2, max_tokens=1024):
            assert "genuinely DIFFERENT" in system
            assert '"num_agents": 3' in user
            return {
                "tasks": [
                    {
                        "agent_id": 1,
                        "role_name": "OpenAI researcher",
                        "task_description": "Research OpenAI products and pricing",
                        "specific_instructions": "Focus on ChatGPT, API models, and enterprise tiers.",
                        "expected_output": "OpenAI product/pricing summary",
                        "dependencies": [],
                    },
                    {
                        "agent_id": 2,
                        "role_name": "Anthropic researcher",
                        "task_description": "Research Anthropic products and pricing",
                        "specific_instructions": "Focus on Claude plans, API pricing, and enterprise options.",
                        "expected_output": "Anthropic product/pricing summary",
                        "dependencies": [],
                    },
                    {
                        "agent_id": 3,
                        "role_name": "Google researcher",
                        "task_description": "Research Google DeepMind products and pricing",
                        "specific_instructions": "Focus on Gemini products, AI Studio, and Vertex AI.",
                        "expected_output": "Google product/pricing summary",
                        "dependencies": [],
                    },
                ]
            }

        monkeypatch.setattr(orchestrator_module, "_chat_json", fake_chat_json)

        tasks = decompose_task(intent, 3)

        assert [task.agent_id for task in tasks] == [1, 2, 3]
        assert len({task.task_description for task in tasks}) == 3
        assert tasks[0].role_name == "OpenAI researcher"
        assert tasks[1].dependencies == []

    def test_run_swarm_orchestration_combines_distinct_agent_work(self, monkeypatch):
        intent = IntentAnalysis(
            true_goal="Compare major AI companies",
            desired_output_format="Markdown comparison matrix",
            success_criteria=["covers products", "covers pricing"],
            key_entities=["OpenAI", "Anthropic"],
            suggested_num_agents=2,
        )
        tasks = [
            AgentTask(
                agent_id=1,
                role_name="OpenAI researcher",
                task_description="Research OpenAI products and pricing",
                specific_instructions="Find product lines and current pricing.",
                expected_output="OpenAI summary",
            ),
            AgentTask(
                agent_id=2,
                role_name="Anthropic researcher",
                task_description="Research Anthropic products and pricing",
                specific_instructions="Find Claude plans and API pricing.",
                expected_output="Anthropic summary",
            ),
        ]
        synthesis = SynthesisResult(
            final_report="## Competitive comparison\nOpenAI and Anthropic compared.",
            key_findings=["Both offer API and chat products"],
            confidence_score=0.8,
            gaps_or_uncertainties=["Pricing changes frequently"],
        )

        def fake_run_single_attempt(task, url=None, extra_context="", channel="", **kwargs):
            from cua_loop.types import Trajectory

            traj = Trajectory(task=task, url=url)
            traj.final_message = f"Completed: {task}"
            traj.extracted = {"summary": task}
            return traj

        monkeypatch.setattr(orchestrator_module, "understand_intent", lambda task: intent)
        monkeypatch.setattr(orchestrator_module, "decompose_task", lambda passed_intent, num_agents: tasks)
        monkeypatch.setattr(orchestrator_module, "run_single_attempt", fake_run_single_attempt)

        def fake_synthesize(agent_tasks, results, passed_intent):
            assert [task.task_description for task in agent_tasks] == [
                "Research OpenAI products and pricing",
                "Research Anthropic products and pricing",
            ]
            assert [result["agent_id"] for result in results] == [1, 2]
            assert all(result["success"] for result in results)
            return synthesis

        monkeypatch.setattr(orchestrator_module, "synthesize_results", fake_synthesize)

        result = run_swarm_orchestration("research AI companies", num_agents=2)

        assert isinstance(result, SwarmResult)
        assert result.success is True
        assert result.intent == intent
        assert result.agent_tasks == tasks
        assert result.synthesis == synthesis

    def test_max_browsers_limits_branches(self, monkeypatch):
        listings = [{"title": "Item", "price": 50.0, "marketplace": "cl"}]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=self._mock_run_single(listings)),
            patch("cua_loop.orchestrator.verify", self._mock_verify(rows=1)),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={f"site{i}": f"https://site{i}.com" for i in range(10)}),
        ):
            result = orchestrate("test", max_browsers=3)

        assert result.total_branches <= 3

    def test_all_fail_returns_failure(self, monkeypatch):
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=Exception("crash")),
            patch("cua_loop.orchestrator.extract_listings", return_value=[]),
            patch("cua_loop.orchestrator.run_fallback_extraction", return_value=[]),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"craigslist": "https://cl.example.com"}),
            patch("cua_loop.orchestrator.make_backend") as mock_be,
        ):
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_be.return_value = ctx

            result = orchestrate("nonexistent widget", max_browsers=1)

        assert result.success is False
        assert result.total_listings_found == 0

    def test_cascade_cua_to_dom_to_fallback(self, monkeypatch):
        fallback_data = [{"title": "Rescued", "price": "$75", "marketplace": "ebay"}]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=Exception("CUA fail")),
            patch("cua_loop.orchestrator.extract_listings", return_value=[]),
            patch("cua_loop.orchestrator.run_fallback_extraction", return_value=fallback_data),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"ebay": "https://ebay.com/sch"}),
            patch("cua_loop.orchestrator.make_backend") as mock_be,
        ):
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_be.return_value = ctx

            result = orchestrate("vintage camera", max_browsers=1)

        assert result.success is True
        assert result.total_listings_found == 1
        assert any(d.get("mode") == "fallback" for d in result.branch_details)

    def test_orchestrate_no_listings_triggers_fallback(self, monkeypatch):
        fallback_listings = [{"title": "Fallback Item", "price": "$50", "marketplace": "craigslist"}]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=Exception("CUA crash")),
            patch("cua_loop.orchestrator.extract_listings", return_value=[]),
            patch("cua_loop.orchestrator.run_fallback_extraction", return_value=fallback_listings),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"craigslist": "https://cl.example.com"}),
            patch("cua_loop.orchestrator.make_backend") as mock_be,
        ):
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            mock_be.return_value = ctx

            result = orchestrate("used couch under $200", max_browsers=1)

        assert result.success
        assert result.total_listings_found >= 1

    def test_early_stop_skips_remaining(self, monkeypatch):
        monkeypatch.setenv("AEGIS_EARLY_STOP", "2")
        listings = [{"title": f"Item {i}", "price": float(i * 100), "marketplace": "cl"} for i in range(5)]
        with (
            patch("cua_loop.orchestrator.run_single_attempt", side_effect=self._mock_run_single(listings)),
            patch("cua_loop.orchestrator.verify", self._mock_verify(rows=5)),
            patch("cua_loop.orchestrator.generate_all_filtered_urls",
                  return_value={"craigslist": "https://cl.example.com", "reverb": "https://reverb.example.com"}),
        ):
            result = orchestrate("chairs", max_browsers=2, early_stop=2)

        assert result.success
        assert result.total_listings_found >= 2
