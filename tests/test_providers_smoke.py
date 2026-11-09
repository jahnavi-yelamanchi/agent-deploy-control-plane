import asyncio

from adcp.env import load_tasks
from adcp.providers import MockProvider, get_provider
from adcp.providers.anthropic_provider import build_anthropic_request
from adcp.providers.base import ModelRequest
from adcp.spec import AgentSpec, ModelParams
from adcp.types import Message


def _req(spec: AgentSpec, caching: bool = True) -> ModelRequest:
    task = load_tasks()[0]
    return ModelRequest(
        model_id=spec.model_id,
        system=spec.system_prompt,
        messages=[Message(role="user", content=task.user_input)],
        tools=spec.tools,
        params=ModelParams(prompt_caching=caching),
        metadata={"task_id": task.id},
    )


def test_mock_default_and_caching(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    assert isinstance(get_provider("anthropic"), MockProvider)
    spec = AgentSpec.from_yaml("specs/v1.yaml")
    MockProvider.reset_cache()
    p = MockProvider()
    first = asyncio.run(p.generate(_req(spec)))
    second = asyncio.run(p.generate(_req(spec)))
    assert first.message.tool_calls[0].name == "verify_identity"
    assert first.usage.cache_write_tokens > 0 and second.usage.cache_read_tokens > 0
    off = asyncio.run(p.generate(_req(spec, caching=False)))
    assert off.usage.cache_read_tokens == 0 and off.latency_ms > second.latency_ms


def test_anthropic_request_shape() -> None:
    body = build_anthropic_request(_req(AgentSpec.from_yaml("specs/v1.yaml")))
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in body and "thinking" not in body


def test_real_adapters_against_sdks() -> None:
    """Anthropic + OpenAI adapters through the real SDKs; only the HTTP reply is faked."""
    import json

    import pytest

    anthropic = pytest.importorskip("anthropic")
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx")
    httpx2 = pytest.importorskip("httpx2")
    from adcp.providers.anthropic_provider import AnthropicProvider
    from adcp.providers.openai_compat import OpenAIProvider

    req = _req(AgentSpec.from_yaml("specs/v1.yaml"))
    sent: dict[str, dict] = {}

    def anth(r):  # type: ignore[no-untyped-def]
        sent["anthropic"] = json.loads(r.content)
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                        {"type": "tool_use", "id": "t", "name": "verify_identity", "input": {}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 90},
        })  # fmt: skip

    def oai(r):  # type: ignore[no-untyped-def]
        sent["openai"] = json.loads(r.content)
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 1, "model": "gpt-5",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "verify_identity", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5,
                      "prompt_tokens_details": {"cached_tokens": 80}},
        })  # fmt: skip

    a = AnthropicProvider(client=anthropic.AsyncAnthropic(
        api_key="x", base_url="http://t",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(anth))))  # fmt: skip
    o = OpenAIProvider(client=openai.AsyncOpenAI(
        api_key="x", base_url="http://t/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(oai))))  # fmt: skip
    ra, ro = asyncio.run(a.generate(req)), asyncio.run(o.generate(req))
    assert ra.message.tool_calls[0].name == ro.message.tool_calls[0].name == "verify_identity"
    assert ra.usage.cache_read_tokens == 90 and ro.usage.cache_read_tokens == 80
    assert "temperature" not in sent["anthropic"] and "max_completion_tokens" in sent["openai"]
    from adcp.providers._convert import anthropic_messages

    resent = anthropic_messages([ra.message])[0]["content"]  # thinking goes back unchanged
    assert resent[0] == {"type": "thinking", "thinking": "", "signature": "sig"}
