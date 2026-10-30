"""Provider adapter interface. Every provider returns the same ModelResponse shape."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from adcp.spec import ModelParams, ToolSpec
from adcp.types import Message, Usage


class ModelRequest(BaseModel):
    model_id: str
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    params: ModelParams = Field(default_factory=ModelParams)
    metadata: dict[str, Any] = Field(default_factory=dict)  # task_id etc; mock replays by task_id


class ModelResponse(BaseModel):
    message: Message
    usage: Usage
    latency_ms: float
    finish_reason: str
    model_id: str
    provider: str


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def generate(self, req: ModelRequest) -> ModelResponse: ...
