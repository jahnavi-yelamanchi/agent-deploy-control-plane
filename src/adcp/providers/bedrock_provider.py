"""AWS Bedrock: Anthropic SDK AnthropicBedrockMantle (Messages API) or boto3 Converse fallback.

Credentials: default AWS chain (IRSA on EKS). Region: AWS_REGION. Model ids take `anthropic.`.
Converse caching uses cachePoint blocks after system and tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from adcp.providers.anthropic_provider import AnthropicProvider
from adcp.providers.base import ModelRequest, ModelResponse
from adcp.types import Message, ToolCall, Usage

CACHE_POINT = {"cachePoint": {"type": "default"}}


class _MantleSync(AnthropicProvider):
    """Wraps the sync Mantle client in a worker thread."""

    async def _create(self, body: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self.client.messages.create, **body)


def converse_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            block = {"toolResult": {"toolUseId": m.tool_call_id, "content": [{"text": m.content}]}}
            if out and out[-1]["role"] == "user" and "toolResult" in out[-1]["content"][0]:
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant":
            content: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
            content += [
                {"toolUse": {"toolUseId": t.id, "name": t.name, "input": t.arguments}}
                for t in m.tool_calls
            ]
            out.append({"role": "assistant", "content": content})
        elif m.role == "user":
            out.append({"role": "user", "content": [{"text": m.content}]})
    return out


class BedrockConverseProvider:
    name = "aws_bedrock"

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION"))
        return self._client

    async def generate(self, req: ModelRequest) -> ModelResponse:
        cache = req.params.prompt_caching
        system: list[dict[str, Any]] = [{"text": req.system}] + ([CACHE_POINT] if cache else [])
        kwargs: dict[str, Any] = {
            "modelId": req.model_id,
            "system": system,
            "messages": converse_messages(req.messages),
            "inferenceConfig": {"maxTokens": req.params.max_tokens},
        }
        if req.tools:
            tools: list[dict[str, Any]] = [
                {
                    "toolSpec": {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": {"json": t.input_schema},
                    }
                }
                for t in req.tools
            ]
            kwargs["toolConfig"] = {"tools": tools + ([CACHE_POINT] if cache else [])}
        t0 = time.perf_counter()
        resp = await asyncio.to_thread(self.client.converse, **kwargs)
        text, calls = [], []
        for b in resp["output"]["message"]["content"]:
            if "text" in b:
                text.append(b["text"])
            elif "toolUse" in b:
                tu = b["toolUse"]
                inp = tu["input"] if isinstance(tu["input"], dict) else json.loads(tu["input"])
                calls.append(ToolCall(id=tu["toolUseId"], name=tu["name"], arguments=inp))
        u = resp["usage"]
        usage = Usage(
            input_tokens=u.get("inputTokens", 0),
            output_tokens=u.get("outputTokens", 0),
            cache_read_tokens=u.get("cacheReadInputTokens", 0),
            cache_write_tokens=u.get("cacheWriteInputTokens", 0),
        )
        return ModelResponse(
            message=Message(role="assistant", content="\n".join(text), tool_calls=calls),
            usage=usage,
            latency_ms=(time.perf_counter() - t0) * 1000,
            finish_reason=resp.get("stopReason", "end_turn"),
            model_id=req.model_id,
            provider=self.name,
        )


def bedrock_provider() -> Any:
    """Prefer the Mantle client (same request shape as first-party, incl. cache_control)."""
    try:
        from anthropic import AnthropicBedrockMantle  # type: ignore[attr-defined]
    except ImportError:
        return BedrockConverseProvider()
    client = AnthropicBedrockMantle(aws_region=os.getenv("AWS_REGION", "us-east-1"))
    p = _MantleSync(client=client, model_prefix="anthropic.")
    p.name = "aws_bedrock"
    return p
