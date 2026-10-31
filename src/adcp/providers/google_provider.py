"""Gemini adapter via google-genai (GOOGLE_API_KEY, or Vertex with GOOGLE_GENAI_USE_VERTEXAI).

Gemini 2.5 applies implicit context caching; cached tokens: usage_metadata.cached_content_token_count.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from adcp.providers.base import ModelRequest, ModelResponse
from adcp.types import Message, ToolCall, Usage


def gemini_contents(messages: list[Message]) -> list[dict[str, Any]]:
    names = {tc.id: tc.name for m in messages for tc in m.tool_calls}
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "parts": [{"text": m.content}]})
        elif m.role == "assistant":
            parts: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
            parts += [
                {"function_call": {"name": t.name, "args": t.arguments}} for t in m.tool_calls
            ]
            out.append({"role": "model", "parts": parts})
        elif m.role == "tool":
            name = names.get(m.tool_call_id or "", m.name or "tool")
            part = {"function_response": {"name": name, "response": {"result": m.content}}}
            out.append({"role": "user", "parts": [part]})
    return out


class GoogleProvider:
    name = "google"

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google import genai  # type: ignore[attr-defined]

            self._client = genai.Client()
        return self._client

    async def generate(self, req: ModelRequest) -> ModelResponse:
        config: dict[str, Any] = {
            "system_instruction": req.system,
            "max_output_tokens": req.params.max_tokens,
        }
        if req.tools:
            config["tools"] = [
                {
                    "function_declarations": [
                        {"name": t.name, "description": t.description, "parameters": t.input_schema}
                        for t in req.tools
                    ]
                }
            ]
        t0 = time.perf_counter()
        resp = await self.client.aio.models.generate_content(
            model=req.model_id, contents=gemini_contents(req.messages), config=config
        )
        calls = [
            ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=fc.name, arguments=dict(fc.args or {}))
            for fc in (resp.function_calls or [])
        ]
        text = "" if calls else (resp.text or "")
        um = resp.usage_metadata
        cached = int(getattr(um, "cached_content_token_count", 0) or 0)
        usage = Usage(
            input_tokens=max(0, int(um.prompt_token_count or 0) - cached),
            output_tokens=int(um.candidates_token_count or 0),
            cache_read_tokens=cached,
        )
        return ModelResponse(
            message=Message(role="assistant", content=text, tool_calls=calls),
            usage=usage,
            latency_ms=(time.perf_counter() - t0) * 1000,
            finish_reason="tool_use" if calls else "stop",
            model_id=req.model_id,
            provider=self.name,
        )
