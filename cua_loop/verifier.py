"""LLM-as-judge verifier. Cheap, structured, fast."""

from __future__ import annotations

import json
import os
import re

from anthropic import Anthropic

from cua_loop.types import Trajectory, VerifierResult

VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "claude-haiku-4-5-20251001")

PROMPT = """You are a strict QA verifier judging whether a CUA agent succeeded at a scraping task.

TASK: {task}
URL: {url}
AGENT FINAL MESSAGE: {final_message}
AGENT EXTRACTED OUTPUT: {extracted}
NUM STEPS TAKEN: {num_steps}
ERROR (if any): {error}

Score strictly. A run only "succeeds" if the agent produced a clearly-structured
result that matches the task. Saying "I clicked some things" is not success.

Respond with ONLY a JSON object on a single line:
{{"success": bool, "rows_extracted": int, "schema_valid": bool, "reason": "<<=80 chars"}}
"""


_client: Anthropic | None = None


def _client_singleton() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def verify(traj: Trajectory) -> VerifierResult:
    prompt = PROMPT.format(
        task=traj.task,
        url=traj.url or "(none)",
        final_message=(traj.final_message or "")[:2000],
        extracted=json.dumps(traj.extracted)[:2000] if traj.extracted is not None else "(none)",
        num_steps=len(traj.steps),
        error=traj.error or "(none)",
    )

    msg = _client_singleton().messages.create(
        model=VERIFIER_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return VerifierResult(success=False, reason=f"no JSON in verifier output: {text[:120]}")
    try:
        data = json.loads(match.group(0))
        return VerifierResult(**data)
    except Exception as e:
        return VerifierResult(success=False, reason=f"verifier parse error: {e}")
