"""Azure AI Foundry: azure-ai-inference ChatCompletionsClient with Entra ID (DefaultAzureCredential,
which resolves to workload identity on AKS). Azure OpenAI deployments use AzureOpenAIProvider.
Env: AZURE_AI_ENDPOINT (Foundry models endpoint) or AZURE_OPENAI_ENDPOINT.
"""

from __future__ import annotations

import os
import time
from typing import Any

from adcp.providers._convert import openai_messages, openai_tools, parse_openai_choice
from adcp.providers.base import ModelRequest, ModelResponse
from adcp.providers.openai_compat import AzureOpenAIProvider, openai_usage

SCOPE = "https://cognitiveservices.azure.com/.default"


class AzureAIInferenceProvider:
    name = "azure_ai_foundry"

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from azure.ai.inference.aio import ChatCompletionsClient
            from azure.identity.aio import DefaultAzureCredential

            self._client = ChatCompletionsClient(
                endpoint=os.environ["AZURE_AI_ENDPOINT"],
                credential=DefaultAzureCredential(),
                credential_scopes=[SCOPE],
            )
        return self._client

    async def generate(self, req: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": req.model_id.removeprefix("azure/"),
            "messages": openai_messages(req.system, req.messages),
            "max_tokens": req.params.max_tokens,
        }
        if req.tools:
            kwargs["tools"] = openai_tools(req.tools)
        t0 = time.perf_counter()
        resp = await self.client.complete(**kwargs)
        choice = resp.choices[0]
        return ModelResponse(
            message=parse_openai_choice(choice.message),
            usage=openai_usage(resp.usage),
            latency_ms=(time.perf_counter() - t0) * 1000,
            finish_reason=str(choice.finish_reason or "stop"),
            model_id=resp.model or req.model_id,
            provider=self.name,
        )


def azure_provider() -> Any:
    if os.getenv("AZURE_OPENAI_ENDPOINT"):
        return AzureOpenAIProvider()
    return AzureAIInferenceProvider()
