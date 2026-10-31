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
