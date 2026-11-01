import asyncio

from adcp.chaos import AddLatency, DegradePrompt, KillTool
from adcp.env.tasks import load_tasks
from adcp.runtime.harness import run_task
from adcp.runtime.mcp_client import MCPToolClient
from adcp.spec import AgentSpec

V1 = AgentSpec.from_yaml("specs/v1.yaml")


def test_degrade_prompt():
    out = DegradePrompt().mutate_spec(V1)
    assert "## Policy" in V1.system_prompt and "## Policy" not in out.system_prompt
    assert "## Output" in out.system_prompt


def test_kill_tool_and_latency():
    task = next(t for t in load_tasks() if t.domain == "airline")
    tc = KillTool("get_user").wrap_tools(MCPToolClient.for_state(task.initial_state))
    res = asyncio.run(tc.call("get_user", {"user_id": "x"}))
    assert not res.ok and "killed" in res.error

    base, _ = run_task(V1, task)
    slow, _ = run_task(V1, task, fault=AddLatency(1000))
    assert slow.latency_ms >= base.latency_ms + 1000 * max(base.steps, 1) * 0.99
