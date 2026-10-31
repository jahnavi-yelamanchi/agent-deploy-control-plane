"""Anthropic Messages API adapter (first-party, or Bedrock via AnthropicBedrockMantle).

Prompt caching: cache_control ephemeral on the system block and on the last tool (tools render
before system, so both breakpoints cover the stable prefix). No temperature/top_p/thinking params.
"""

from __future__ import annotations

import time
from typing import Any

from adcp.providers._convert import anthropic_messages
from adcp.providers.base import ModelRequest, ModelResponse
from adcp.types import Message, ToolCall, Usage

EPHEMERAL = {"type": "ephemeral"}


def build_anthropic_request(req: ModelRequest, model: str | None = None) -> dict[str, Any]:
    cache = req.params.prompt_caching
    system: dict[str, Any] = {"type": "text", "text": req.system}
    tools: list[dict[str, Any]] = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in req.tools
    ]
    if cache:
        system["cache_control"] = EPHEMERAL
        if tools:
            tools[-1]["cache_control"] = EPHEMERAL
    body: dict[str, Any] = {
        "model": model or req.model_id,
        "max_tokens": req.params.max_tokens,
        "system": [system],
        "messages": anthropic_messages(req.messages),
    }
    if tools:
        body["tools"] = tools
    return body


def parse_anthropic_response(resp: Any) -> tuple[Message, Usage]:
    text, calls = [], []
    for b in resp.content:
        if b.type == "text":
            text.append(b.text)
        elif b.type == "tool_use":
            calls.append(ToolCall(id=b.id, name=b.name, arguments=dict(b.input)))
    u = resp.usage
    usage = Usage(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return Message(role="assistant", content="\n".join(text), tool_calls=calls), usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: Any = None, model_prefix: str = "") -> None:
        self._client = client
        self._prefix = model_prefix

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def _create(self, body: dict[str, Any]) -> Any:
        return await self.client.messages.create(**body)

    async def generate(self, req: ModelRequest) -> ModelResponse:
        model = (
            req.model_id if req.model_id.startswith(self._prefix) else self._prefix + req.model_id
        )
        t0 = time.perf_counter()
        resp = await self._create(build_anthropic_request(req, model))
        msg, usage = parse_anthropic_response(resp)
        return ModelResponse(
            message=msg,
            usage=usage,
            latency_ms=(time.perf_counter() - t0) * 1000,
            finish_reason=resp.stop_reason or "end_turn",
            model_id=resp.model,
            provider=self.name,
        )
