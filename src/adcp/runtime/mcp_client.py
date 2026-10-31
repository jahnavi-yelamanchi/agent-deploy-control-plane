"""MCPToolClient: in-process (direct MCPServer.call_tool on a per-task env) or remote
(stdio subprocess / streamable-http URL via mcp.Client). Tool errors -> ToolResult(ok=False)."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Literal

from pydantic import BaseModel

from adcp.env.tools import TauEnv
from adcp.runtime.mcp_server import build_server
from adcp.spec import ToolSpec

Mode = Literal["inprocess", "stdio", "http"]


class ToolResult(BaseModel):
    ok: bool
    content: Any = None
    error: str | None = None
    latency_ms: float = 0.0

    def as_text(self) -> str:
        if not self.ok:
            return f"ERROR: {self.error}"
        return self.content if isinstance(self.content, str) else json.dumps(self.content)


def _parse(result: Any) -> ToolResult:
    texts = [getattr(c, "text", "") for c in (result.content or [])]
    if result.is_error:
        return ToolResult(ok=False, error=" ".join(texts) or "tool error")
    if result.structured_content is not None:
        sc = result.structured_content
        content = sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        return ToolResult(ok=True, content=content)
    raw = "".join(texts)
    try:
        return ToolResult(ok=True, content=json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        return ToolResult(ok=True, content=raw)


class MCPToolClient:
    def __init__(
        self,
        mode: Mode = "inprocess",
        env: TauEnv | None = None,
        url: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self.mode = mode
        self.env = env or TauEnv()
        self.url = url or os.getenv("TAU_MCP_URL", "http://localhost:8000/mcp")
        self.task_id = task_id
        self.server = build_server(self.env) if mode == "inprocess" else None
        self._client: Any = None

    @classmethod
    def for_state(cls, state: dict[str, Any]) -> MCPToolClient:
        return cls("inprocess", env=TauEnv(state))

    async def __aenter__(self) -> MCPToolClient:
        if self.mode != "inprocess":
            from mcp import Client
            from mcp.client.stdio import StdioServerParameters

            target: Any
            if self.mode == "stdio":
                env = {**os.environ, **({"TAU_TASK_ID": self.task_id} if self.task_id else {})}
                target = StdioServerParameters(
                    command=sys.executable, args=["-m", "adcp.runtime.mcp_server"], env=env
                )
            else:
                target = self.url
            self._client = Client(target)
            await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def list_tools(self) -> list[ToolSpec]:
        if self.server is not None:
            tools = await self.server.list_tools()
        else:
            tools = (await self._client.list_tools()).tools
        return [
            ToolSpec(name=t.name, description=t.description or "", input_schema=t.input_schema)
            for t in tools
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        t0 = time.perf_counter()
        try:
            if self.server is not None:
                res = _parse(await self.server.call_tool(name, arguments))
            else:
                res = _parse(await self._client.call_tool(name, arguments))
        except Exception as e:  # noqa: BLE001 - ToolError/validation -> structured error
            msg = str(e.__cause__ or e) if e.__cause__ else str(e)
            res = ToolResult(ok=False, error=msg)
        res.latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        return res

    async def final_state(self) -> dict[str, Any]:
        if self.server is not None:
            return self.env.state
        rr = await self._client.read_resource("tau://state")
        return json.loads(rr.contents[0].text)  # type: ignore[no-any-return]
