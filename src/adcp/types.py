"""Shared run/eval/event models used by runtime, evals, rollback controller."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Track = Literal["stable", "canary"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    raw: list[dict[str, Any]] | None = (
        None  # provider-native blocks (e.g. Claude thinking), resent as-is
    )


class Usage(BaseModel):
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def cache_hit_rate(self) -> float:
        total = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return self.cache_read_tokens / total if total else 0.0


class RunRequest(BaseModel):
    input: str
    task_id: str | None = None
    session_id: str | None = None
    track: Track = "stable"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    latency_ms: float = 0.0


class RunResult(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    spec_name: str
    spec_version: str
    spec_fingerprint: str
    model_id: str
    provider: str
    track: Track = "stable"
    task_id: str | None = None
    output: str = ""
    structured: dict[str, Any] | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    steps: int = 0
    status: Literal["ok", "error", "budget_exceeded"] = "ok"
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def tool_error_rate(self) -> float:
        if not self.tool_calls:
            return 0.0
        return sum(not t.ok for t in self.tool_calls) / len(self.tool_calls)


class EvalResult(BaseModel):
    run_id: str
    task_id: str
    success: bool
    score: float  # 0..1
    details: dict[str, Any] = Field(default_factory=dict)


class ArmMetrics(BaseModel):
    """Aggregates for one rollout arm (stable or canary) over a window."""

    track: Track
    spec_version: str
    n: int
    cost_per_task: float
    p50_latency_ms: float
    p95_latency_ms: float
    eval_score: float
    success_rate: float = 0.0
    tool_error_rate: float = 0.0
    cache_hit_rate: float = 0.0
