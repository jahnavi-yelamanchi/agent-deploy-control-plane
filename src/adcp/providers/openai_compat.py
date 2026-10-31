"""OpenAI chat.completions adapter; also serves vLLM / SGLang (OpenAI-compatible) and Azure OpenAI.

OpenAI caches prompts >=1024 tokens automatically; vLLM/SGLang prefix caching (KV cache reuse) is
server-side. Cached tokens come from usage.prompt_tokens_details.cached_tokens when returned.
"""

from __future__ import annotations

import os
import time
from typing import Any

from adcp.providers._convert import openai_messages, openai_tools, parse_openai_choice
from adcp.providers.base import ModelRequest, ModelResponse
from adcp.types import Usage


def openai_usage(u: Any) -> Usage:
    if u is None:
        return Usage()
    details = getattr(u, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    return Usage(
        input_tokens=max(0, u.prompt_tokens - cached),
        output_tokens=u.completion_tokens,
        cache_read_tokens=cached,
    )


class OpenAIProvider:
    name = "openai"
    base_url_env: str | None = None
    api_key_env = "OPENAI_API_KEY"

    def __init__(self, client: Any = None, base_url: str | None = None) -> None:
        self._client = client
        self._base_url = base_url or (os.getenv(self.base_url_env) if self.base_url_env else None)

    @property
    def client(self) -> Any:
        if self._client is None:
            import openai

            self._client = openai.AsyncOpenAI(
                base_url=self._base_url, api_key=os.getenv(self.api_key_env) or "EMPTY"
            )
        return self._client

    def body(self, req: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": req.model_id,
            "messages": openai_messages(req.system, req.messages),
            "max_completion_tokens": req.params.max_tokens,
        }
        if req.tools:
            body["tools"] = openai_tools(req.tools)
        if req.params.temperature is not None:
            body["temperature"] = req.params.temperature
        return body

    async def generate(self, req: ModelRequest) -> ModelResponse:
        t0 = time.perf_counter()
        resp = await self.client.chat.completions.create(**self.body(req))
        choice = resp.choices[0]
        return ModelResponse(
            message=parse_openai_choice(choice.message),
            usage=openai_usage(resp.usage),
            latency_ms=(time.perf_counter() - t0) * 1000,
            finish_reason=choice.finish_reason or "stop",
            model_id=resp.model or req.model_id,
            provider=self.name,
        )


class VLLMProvider(OpenAIProvider):
    """vLLM OpenAI server (--enable-prefix-caching). Base URL: VLLM_BASE_URL."""

    name = "vllm"
    base_url_env = "VLLM_BASE_URL"
    api_key_env = "VLLM_API_KEY"

    def body(self, req: ModelRequest) -> dict[str, Any]:
        b = super().body(req)
        b["max_tokens"] = b.pop("max_completion_tokens")
        return b


class SGLangProvider(VLLMProvider):
    """SGLang OpenAI server (RadixAttention prefix cache). Base URL: SGLANG_BASE_URL."""

    name = "sglang"
    base_url_env = "SGLANG_BASE_URL"
    api_key_env = "SGLANG_API_KEY"


class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI deployment via Entra ID (DefaultAzureCredential -> workload identity on AKS)."""

    name = "azure_ai_foundry"

    @property
    def client(self) -> Any:
        if self._client is None:
            import openai
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            token = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
            self._client = openai.AsyncAzureOpenAI(
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                azure_ad_token_provider=token,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            )
        return self._client

    def body(self, req: ModelRequest) -> dict[str, Any]:
        b = super().body(req)
        b["model"] = os.getenv("AZURE_OPENAI_DEPLOYMENT", req.model_id.removeprefix("azure/"))
        return b
