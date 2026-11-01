"""Fault injectors applied to the canary arm via harness hooks.

Hooks (each optional, looked up with getattr by `adcp.runtime.harness`):
  mutate_spec(spec) -> spec
  wrap_provider(provider) -> provider
  wrap_tools(tool_client) -> tool_client
"""

from __future__ import annotations

import re
from typing import Any

from adcp.env.tools import ToolError
from adcp.providers.base import ModelProvider, ModelRequest, ModelResponse
from adcp.spec import AgentSpec

_POLICY_RE = re.compile(r"^## Policy\b.*?(?=^## |\Z)", re.S | re.M)


class Fault:
    name = "fault"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name})"


class DegradePrompt(Fault):
    """Drops the `## Policy` section of the system prompt (bad prompt edit)."""

    name = "degrade_prompt"

    def mutate_spec(self, spec: AgentSpec) -> AgentSpec:
        prompt = _POLICY_RE.sub("", spec.system_prompt).rstrip() + "\n"
        return spec.model_copy(update={"system_prompt": prompt})


class _LatencyProvider:
    def __init__(self, inner: ModelProvider, add_ms: float) -> None:
        self._inner, self._add = inner, add_ms
        self.name = inner.name

    async def generate(self, req: ModelRequest) -> ModelResponse:
        resp = await self._inner.generate(req)
        return resp.model_copy(update={"latency_ms": resp.latency_ms + self._add})

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class AddLatency(Fault):
    """Adds `ms` to every provider-reported latency (slow upstream / saturated GPU)."""

    def __init__(self, ms: float = 1500.0) -> None:
        self.ms = ms
        self.name = f"add_latency_{int(ms)}ms"

    def wrap_provider(self, provider: ModelProvider) -> ModelProvider:
        return _LatencyProvider(provider, self.ms)


class _KilledTools:
    """Proxy over MCPToolClient: calls to the killed tool fail like a tool outage.

    `call(name, arguments)` returns ToolResult(ok=False) (the client's error contract); direct
    method access on a TauEnv-like object raises ToolError.
    """

    def __init__(self, inner: Any, tool: str) -> None:
        self._inner, self._tool = inner, tool

    @property
    def error(self) -> str:
        return f"ToolUnavailable: {self._tool} killed by chaos"

    async def __aenter__(self) -> _KilledTools:
        if hasattr(self._inner, "__aenter__"):
            await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if hasattr(self._inner, "__aexit__"):
            await self._inner.__aexit__(*exc)

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == self._tool:
            from adcp.runtime.mcp_client import ToolResult

            return ToolResult(ok=False, error=self.error)
        return await self._inner.call(name, arguments)

    def __getattr__(self, item: str) -> Any:
        if item == self._tool:

            def dead(*_a: Any, **_k: Any) -> Any:
                raise ToolError(self.error)

            return dead
        return getattr(self._inner, item)


class KillTool(Fault):
    """Makes one tool fail on every call (dependency outage)."""

    def __init__(self, tool: str = "get_reservation") -> None:
        self.tool = tool
        self.name = f"kill_tool_{tool}"

    def wrap_tools(self, tool_client: Any) -> Any:
        return _KilledTools(tool_client, self.tool)


def default_faults() -> list[Fault]:
    """Default chaos suite."""
    return [DegradePrompt(), AddLatency(1500.0), KillTool("get_reservation"), KillTool("get_order")]
