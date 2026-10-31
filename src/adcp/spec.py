"""AgentSpec: the versioned, deployable artifact (prompt + model + tool manifest + params)."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Provider(StrEnum):
    anthropic = "anthropic"
    openai = "openai"
    google = "google"
    aws_bedrock = "aws_bedrock"
    azure_ai_foundry = "azure_ai_foundry"
    vllm = "vllm"
    sglang = "sglang"
    mock = "mock"


class ContextStrategy(StrEnum):
    full = "full"
    summarized = "summarized"
    retrieved = "retrieved"


class Stage(StrEnum):
    candidate = "candidate"
    canary = "canary"
    production = "production"
    rolled_back = "rolled_back"


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    server: str = "tau"
    flag: str | None = None  # OpenFeature flag key gating this tool


class ModelParams(BaseModel):
    temperature: float | None = None
    max_tokens: int = 1024
    prompt_caching: bool = True
    max_steps: int = 8
    timeout_s: float = 30.0


class Budget(BaseModel):
    max_cost_usd_per_run: float = 0.05
    max_latency_ms: float = 15000.0


class AgentSpec(BaseModel):
    name: str
    version: str
    system_prompt: str
    model_id: str
    provider: Provider
    tools: list[ToolSpec] = Field(default_factory=list)
    context_strategy: ContextStrategy = ContextStrategy.full
    params: ModelParams = Field(default_factory=ModelParams)
    budget: Budget = Field(default_factory=Budget)
    labels: dict[str, str] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @classmethod
    def from_yaml(cls, path: str | Path) -> AgentSpec:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False)
