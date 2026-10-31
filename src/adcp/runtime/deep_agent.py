"""Pattern reference (not used by default): the same agent as a LangChain Deep Agent.

Requires the `deep` extra (deepagents) plus langchain-mcp-adapters and a LangChain chat model.
Deep Agents adds the built-in write_todos planner, virtual filesystem and sub-agents on top of a
LangGraph loop; the runtime's own graph (adcp.runtime.agent) mirrors the planner as the `plan`
node so cost/latency accounting and flag gating stay explicit.
"""

from __future__ import annotations

from typing import Any

from adcp.spec import AgentSpec

try:
    from deepagents import create_deep_agent

    HAVE_DEEPAGENTS = True
except ImportError:  # optional extra
    create_deep_agent = None
    HAVE_DEEPAGENTS = False


async def build_deep_agent(spec: AgentSpec, mcp_url: str | None = None, model: Any = None) -> Any:
    """Build a Deep Agent wired to the tau MCP server (stdio by default, or streamable-http)."""
    if not HAVE_DEEPAGENTS:
        raise RuntimeError("install the 'deep' extra: uv sync --extra deep")
    from langchain_mcp_adapters.client import MultiServerMCPClient

    conn: dict[str, Any] = (
        {"transport": "streamable_http", "url": mcp_url}
        if mcp_url
        else {"transport": "stdio", "command": "python", "args": ["-m", "adcp.runtime.mcp_server"]}
    )
    tools = await MultiServerMCPClient({"tau": conn}).get_tools()
    allowed = {t.name for t in spec.tools}
    return create_deep_agent(
        model=model or f"{spec.provider.value}:{spec.model_id}",
        tools=[t for t in tools if t.name in allowed],
        system_prompt=spec.system_prompt,
    )
