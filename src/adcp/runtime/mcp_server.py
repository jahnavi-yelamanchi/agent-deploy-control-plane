"""MCP server "tau" exposing the airline/retail env tools.

`python -m adcp.runtime.mcp_server` serves stdio. TAU_TASK_ID selects a task's initial DB;
unset merges all task DBs. MCP_TRANSPORT=streamable-http serves HTTP instead.
Resource tau://state returns the current DB (used by out-of-process clients for scoring).
"""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError

from adcp.env.tools import TOOL_NAMES, TauEnv, ToolError, empty_db


def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def run(**kwargs: Any) -> Any:
        try:
            return fn(**kwargs)
        except ToolError as e:
            raise MCPToolError(str(e)) from e

    return run


def build_server(env: TauEnv | None = None, tools: set[str] | None = None) -> MCPServer:
    env = env or TauEnv()
    server = MCPServer(
        "tau", instructions="tau-bench style airline + retail customer-service tools"
    )
    for name in TOOL_NAMES:
        if tools is not None and name not in tools:
            continue
        fn = getattr(env, name)
        server.tool(name=name, description=(fn.__doc__ or name).strip())(_wrap(fn))

    @server.resource("tau://state", mime_type="application/json")
    def state() -> str:
        return json.dumps(env.state, sort_keys=True)

    server.env = env  # type: ignore[attr-defined]
    return server


def default_state() -> dict[str, Any]:
    from adcp.env.tasks import get_task, load_tasks

    if tid := os.getenv("TAU_TASK_ID"):
        return get_task(tid).initial_state
    merged = empty_db()
    for t in load_tasks():
        for k, v in t.initial_state.items():
            if isinstance(v, dict):
                merged[k].update(v)
            else:
                merged[k].extend(v)
    return merged


def main() -> None:
    server = build_server(TauEnv(default_state()))
    if os.getenv("MCP_TRANSPORT", "stdio") == "streamable-http":
        server.run("streamable-http", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")))
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
