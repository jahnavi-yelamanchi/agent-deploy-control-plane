"""Provider adapters. MODEL_PROVIDER unset/mock -> MockProvider for every spec."""

from __future__ import annotations

import os

from adcp.providers.base import ModelProvider, ModelRequest, ModelResponse
from adcp.providers.mock import MockProvider
from adcp.spec import Provider

__all__ = ["ModelProvider", "ModelRequest", "ModelResponse", "MockProvider", "get_provider"]


def get_provider(provider: Provider | str) -> ModelProvider:
    if os.getenv("MODEL_PROVIDER", "mock").lower() in ("", "mock"):
        return MockProvider()
    p = Provider(provider)
    if p is Provider.anthropic:
        from adcp.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if p is Provider.openai:
        from adcp.providers.openai_compat import OpenAIProvider

        return OpenAIProvider()
    if p is Provider.vllm:
        from adcp.providers.openai_compat import VLLMProvider

        return VLLMProvider()
    if p is Provider.sglang:
        from adcp.providers.openai_compat import SGLangProvider

        return SGLangProvider()
    if p is Provider.google:
        from adcp.providers.google_provider import GoogleProvider

        return GoogleProvider()
    if p is Provider.aws_bedrock:
        from adcp.providers.bedrock_provider import bedrock_provider

        return bedrock_provider()  # type: ignore[no-any-return]
    if p is Provider.azure_ai_foundry:
        from adcp.providers.azure_provider import azure_provider

        return azure_provider()  # type: ignore[no-any-return]
    return MockProvider()
