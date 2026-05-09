from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Step(BaseModel):
    ts: datetime = Field(default_factory=datetime.utcnow)
    action_type: str
    action_args: dict[str, Any] = Field(default_factory=dict)
    screenshot_url: str | None = None
    model_message: str | None = None


class Trajectory(BaseModel):
    task: str
    url: str | None = None
    steps: list[Step] = Field(default_factory=list)
    final_message: str | None = None
    extracted: Any = None
    error: str | None = None


class VerifierResult(BaseModel):
    success: bool
    rows_extracted: int = 0
    schema_valid: bool = False
    reason: str = ""


class AttemptResult(BaseModel):
    attempt_index: int
    trajectory: Trajectory
    verifier: VerifierResult
    duration_s: float


class RunResult(BaseModel):
    task: str
    url: str | None = None
    success: bool
    attempts: list[AttemptResult]
    extracted: Any = None
    total_duration_s: float = 0.0
