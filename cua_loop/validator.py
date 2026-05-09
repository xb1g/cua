"""LLM-based validator and guider for the CUA loop.

Provides intelligent validation and strategic guidance using a separate
model (e.g., Kimi K2.6 Turbo via Fireworks) while the CUA model
(e.g., Northstar) handles browser control.

Architecture:
  - CUA model (Northstar): controls the browser — screenshots → actions
  - Validator (Kimi): validates actions, guides strategy, verifies results

Configuration:
  VALIDATOR_PROVIDER=local|kimi  (default: local)
  VALIDATOR_MODEL=accounts/fireworks/routers/kimi-k2p6-turbo
  VALIDATOR_API_KEY=fpk_xxx
  VALIDATOR_BASE_URL=https://api.fireworks.ai/inference/v1
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VALIDATOR_PROVIDER = os.getenv("VALIDATOR_PROVIDER", "local").lower().strip()


def _flag(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    passed: bool
    reason: str
    guidance: str = ""


@dataclass
class GuidanceResult:
    advice: str
    confidence: float = 0.0


@dataclass
class ResultVerification:
    valid: bool
    score: float = 0.0
    feedback: str = ""
    improvements: list[str] | None = None


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------


class ValidatorProvider(ABC):
    @abstractmethod
    def validate_action(
        self,
        task: str,
        action_type: str,
        action_args: dict[str, Any],
        screenshot_url: str | None,
        history: list[dict[str, Any]],
    ) -> ValidationResult:
        """Validate whether a proposed action makes sense for the task."""

    @abstractmethod
    def guide_when_stuck(
        self,
        task: str,
        history: list[dict[str, Any]],
        current_url: str | None,
    ) -> GuidanceResult:
        """Provide strategic guidance when the agent appears stuck."""

    @abstractmethod
    def verify_results(
        self,
        task: str,
        extracted: Any,
        screenshots: list[str],
    ) -> ResultVerification:
        """Verify that extracted results match the task requirements."""


# ---------------------------------------------------------------------------
# Local validator — cheap heuristics, no API calls
# ---------------------------------------------------------------------------


class LocalValidator(ValidatorProvider):
    """Fast local validation using deterministic heuristics.

    No external API calls. Used by default or as fallback.
    """

    def validate_action(
        self,
        task: str,
        action_type: str,
        action_args: dict[str, Any],
        screenshot_url: str | None,
        history: list[dict[str, Any]],
    ) -> ValidationResult:
        return ValidationResult(passed=True, reason="local validator: no-op")

    def guide_when_stuck(
        self,
        task: str,
        history: list[dict[str, Any]],
        current_url: str | None,
    ) -> GuidanceResult:
        return GuidanceResult(
            advice="Try keyboard navigation (Tab/Enter) or scroll to reveal elements.",
            confidence=0.5,
        )

    def verify_results(
        self,
        task: str,
        extracted: Any,
        screenshots: list[str],
    ) -> ResultVerification:
        if not extracted:
            return ResultVerification(valid=False, score=0.0, feedback="No data extracted")
        if isinstance(extracted, list):
            return ResultVerification(
                valid=len(extracted) > 0,
                score=min(1.0, len(extracted) / 10),
                feedback=f"Extracted {len(extracted)} items",
            )
        return ResultVerification(valid=True, score=0.5, feedback="Data present")


# ---------------------------------------------------------------------------
# Kimi validator via Fireworks AI
# ---------------------------------------------------------------------------


class KimiValidator(ValidatorProvider):
    """Kimi K2.6 Turbo via Fireworks AI as an intelligent validator and guider."""

    def __init__(self) -> None:
        from openai import OpenAI

        api_key = os.getenv("VALIDATOR_API_KEY") or os.getenv("FIREWORKS_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "VALIDATOR_API_KEY or FIREWORKS_API_KEY is required when VALIDATOR_PROVIDER=kimi."
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

    def _chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """Send a chat request and return the assistant's text."""
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

    def _parse_json_response(self, text: str) -> dict[str, Any]:
        """Extract JSON object from model response text."""
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

    def validate_action(
        self,
        task: str,
        action_type: str,
        action_args: dict[str, Any],
        screenshot_url: str | None,
        history: list[dict[str, Any]],
    ) -> ValidationResult:
        system = (
            "You are an expert browser automation validator. "
            "Given a task and a proposed browser action, decide if the action is sensible. "
            "Respond with ONLY a JSON object: {\"passed\": bool, \"reason\": str, \"guidance\": str}. "
            "Be concise."
        )
        history_text = json.dumps(history[-5:], indent=2) if history else "None"
        user = (
            f"Task: {task}\n"
            f"Proposed action: {action_type} {json.dumps(action_args)}\n"
            f"Recent history: {history_text}\n"
            f"Does this action make sense?"
        )
        text = self._chat(system, user, temperature=0.1, max_tokens=512)
        data = self._parse_json_response(text)
        return ValidationResult(
            passed=data.get("passed", True),
            reason=data.get("reason", ""),
            guidance=data.get("guidance", ""),
        )

    def guide_when_stuck(
        self,
        task: str,
        history: list[dict[str, Any]],
        current_url: str | None,
    ) -> GuidanceResult:
        system = (
            "You are a strategic advisor for web scraping agents. "
            "Given a task and action history where the agent is stuck, suggest the best next move. "
            "Respond with ONLY a JSON object: {\"advice\": str, \"confidence\": float}. "
            "Advice should be 1-2 sentences."
        )
        history_text = json.dumps(history[-10:], indent=2) if history else "None"
        user = (
            f"Task: {task}\n"
            f"Current URL: {current_url or 'unknown'}\n"
            f"Recent actions (agent appears stuck): {history_text}\n"
            f"What should the agent do next?"
        )
        text = self._chat(system, user, temperature=0.3, max_tokens=512)
        data = self._parse_json_response(text)
        return GuidanceResult(
            advice=data.get("advice", "Try a different approach."),
            confidence=float(data.get("confidence", 0.5)),
        )

    def verify_results(
        self,
        task: str,
        extracted: Any,
        screenshots: list[str],
    ) -> ResultVerification:
        system = (
            "You are a data quality verifier for web scraping results. "
            "Given a task and extracted data, assess quality and completeness. "
            "Respond with ONLY a JSON object: "
            "{\"valid\": bool, \"score\": float (0-1), \"feedback\": str, \"improvements\": [str]}."
        )
        extracted_text = json.dumps(extracted, indent=2, default=str) if extracted else "None"
        user = (
            f"Task: {task}\n"
            f"Extracted data: {extracted_text[:4000]}\n"
            f"Does this data satisfy the task? What's missing or incorrect?"
        )
        text = self._chat(system, user, temperature=0.2, max_tokens=1024)
        data = self._parse_json_response(text)
        return ResultVerification(
            valid=data.get("valid", False),
            score=float(data.get("score", 0.0)),
            feedback=data.get("feedback", ""),
            improvements=data.get("improvements") or [],
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_validator: ValidatorProvider | None = None


def get_validator() -> ValidatorProvider:
    """Return the configured validator provider."""
    global _validator
    if _validator is None:
        name = VALIDATOR_PROVIDER
        if name in ("kimi", "fireworks"):
            _validator = KimiValidator()
        else:
            _validator = LocalValidator()
    return _validator


def reset_validator() -> None:
    """Reset the validator singleton (useful for testing)."""
    global _validator
    _validator = None
