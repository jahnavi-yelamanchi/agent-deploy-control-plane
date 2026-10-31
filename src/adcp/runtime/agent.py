"""LangGraph agent loop: plan -> call_model <-> call_tools -> finalize.

`plan` writes a Deep Agents-style todo list into state (no model call). `call_model` applies the
context strategy, calls the provider, accounts cost/latency and enforces the per-run budget.
`finalize` parses the structured AgentAnswer (JSON) from the last message, else plain text.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from adcp.finops.cost import cost_usd
from adcp.providers.base import ModelProvider, ModelRequest
from adcp.runtime.budget import check_run_budget
from adcp.runtime.context import apply_context
from adcp.runtime.telemetry import Telemetry, get_telemetry
from adcp.spec import AgentSpec, ContextStrategy, ToolSpec
from adcp.types import Message, ToolCallRecord, Usage


class AgentAnswer(BaseModel):
    answer: str
    actions_taken: list[str] = Field(default_factory=list)
    needs_human: bool = False


class ToolCaller(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> Any: ...


class AgentState(TypedDict, total=False):
    messages: list[Message]
    todos: list[dict[str, str]]
    steps: int
    usage: Usage
    cost_usd: float
    latency_ms: float
    tool_calls: list[ToolCallRecord]
    status: str
    error: str | None
    output: str
    structured: dict[str, Any] | None


@dataclass
class AgentDeps:
    spec: AgentSpec
    provider: ModelProvider
    tools: ToolCaller
    tool_specs: list[ToolSpec]
    strategy: ContextStrategy
    user_input: str
    metadata: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    telemetry: Telemetry = field(default_factory=get_telemetry)


_JSON_OBJ = re.compile(r"\{.*\}", re.S)
_TODO_RULES = [
    (
        ("cancel", "move", "change", "return", "exchange", "refund", "compensation"),
        "verify identity",
    ),
    (("reservation", "flight"), "look up reservation / flights"),
    (("order",), "look up order / products"),
    (
        ("cancel", "move", "change", "return", "exchange", "refund", "compensation"),
        "apply change if policy allows",
    ),
    (("supervisor", "legal", "complaint", "human"), "transfer to human agent"),
]


def parse_answer(text: str) -> tuple[str, dict[str, Any] | None]:
    m = _JSON_OBJ.search(text or "")
    if m:
        try:
            ans = AgentAnswer.model_validate(json.loads(m.group(0)))
            return ans.answer, ans.model_dump()
        except (json.JSONDecodeError, ValidationError):
            pass
    return text or "", None


def _deps(config: RunnableConfig) -> AgentDeps:
    return config["configurable"]["deps"]  # type: ignore[no-any-return]


def plan(state: AgentState, config: RunnableConfig) -> AgentState:
    text = _deps(config).user_input.lower()
    todos, seen = [], set()
    for keys, item in _TODO_RULES:
        if any(k in text for k in keys) and item not in seen:
            seen.add(item)
            todos.append({"content": item, "status": "pending"})
    todos.append({"content": "answer with outcome", "status": "pending"})
    return {"todos": todos}


async def call_model(state: AgentState, config: RunnableConfig) -> AgentState:
    d = _deps(config)
    spec = d.spec
    msgs, tools = apply_context(d.strategy, state["messages"], d.tool_specs, d.user_input)
    req = ModelRequest(
        model_id=spec.model_id,
        system=spec.system_prompt,
        messages=msgs,
        tools=tools,
        params=spec.params,
        metadata=d.metadata,
    )
    with d.telemetry.span(
        f"chat {spec.model_id}",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": spec.provider.value,
            "gen_ai.request.model": spec.model_id,
        },
    ) as sp:
        resp = await d.provider.generate(req)
        sp.set_attribute("gen_ai.response.model", resp.model_id)
        sp.set_attribute("gen_ai.response.finish_reasons", [resp.finish_reason])
        sp.set_attribute(
            "gen_ai.usage.input_tokens",
            resp.usage.input_tokens + resp.usage.cache_read_tokens + resp.usage.cache_write_tokens,
        )
        sp.set_attribute("gen_ai.usage.output_tokens", resp.usage.output_tokens)
        sp.set_attribute("adcp.usage.cache_read_tokens", resp.usage.cache_read_tokens)
    d.telemetry.record_chat(
        spec.provider.value, spec.model_id, resp.latency_ms / 1000.0, resp.usage, d.labels
    )
    try:
        step_cost = cost_usd(resp.usage, spec.model_id)
    except KeyError:
        step_cost = 0.0
    usage = state["usage"] + resp.usage
    cost = state["cost_usd"] + step_cost
    latency = state["latency_ms"] + resp.latency_ms
    out: AgentState = {
        "messages": [*state["messages"], resp.message],
        "steps": state["steps"] + 1,
        "usage": usage,
        "cost_usd": cost,
        "latency_ms": latency,
    }
    if reason := check_run_budget(spec.budget, cost, latency):
        out["status"], out["error"] = "budget_exceeded", reason
    return out


async def call_tools(state: AgentState, config: RunnableConfig) -> AgentState:
    d = _deps(config)
    allowed = {t.name for t in d.tool_specs}
    msgs, records = list(state["messages"]), list(state["tool_calls"])
    latency = state["latency_ms"]
    for tc in msgs[-1].tool_calls:
        with d.telemetry.span(
            f"execute_tool {tc.name}",
            **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tc.name},
        ) as sp:
            if tc.name not in allowed:
                ok, text, err, ms = (
                    False,
                    f"ERROR: tool {tc.name} not available",
                    "unavailable",
                    0.0,
                )
            else:
                r = await d.tools.call(tc.name, tc.arguments)
                ok, text, err, ms = r.ok, r.as_text(), r.error, r.latency_ms
            sp.set_attribute("adcp.tool.ok", ok)
        d.telemetry.record_tool(tc.name, ok, d.labels)
        records.append(
            ToolCallRecord(name=tc.name, arguments=tc.arguments, ok=ok, error=err, latency_ms=ms)
        )
        msgs.append(Message(role="tool", content=text, tool_call_id=tc.id, name=tc.name))
        latency += ms
    return {"messages": msgs, "tool_calls": records, "latency_ms": latency}


def finalize(state: AgentState, config: RunnableConfig) -> AgentState:
    last = state["messages"][-1] if state["messages"] else None
    out: AgentState = {"todos": [{**t, "status": "done"} for t in state.get("todos", [])]}
    if last is None or last.role != "assistant" or last.tool_calls:
        out["output"], out["structured"] = "", None
        if state.get("status", "ok") == "ok":
            out["error"] = "max_steps reached"
        return out
    out["output"], out["structured"] = parse_answer(last.content)
    return out


def route_after_model(state: AgentState, config: RunnableConfig) -> str:
    if state.get("status", "ok") != "ok":
        return "finalize"
    last = state["messages"][-1]
    if last.tool_calls and state["steps"] < _deps(config).spec.params.max_steps:
        return "call_tools"
    return "finalize"


def build_graph() -> Any:
    g = StateGraph(AgentState)
    g.add_node("plan", plan)
    g.add_node("call_model", call_model)
    g.add_node("call_tools", call_tools)
    g.add_node("finalize", finalize)
    g.add_edge(START, "plan")
    g.add_edge("plan", "call_model")
    g.add_conditional_edges("call_model", route_after_model, ["call_tools", "finalize"])
    g.add_edge("call_tools", "call_model")
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build_graph()


async def run_agent(deps: AgentDeps) -> tuple[AgentState, float]:
    """Returns final state and wall-clock ms."""
    init: AgentState = {
        "messages": [Message(role="user", content=deps.user_input)],
        "todos": [],
        "steps": 0,
        "usage": Usage(),
        "cost_usd": 0.0,
        "latency_ms": 0.0,
        "tool_calls": [],
        "status": "ok",
        "error": None,
    }
    t0 = time.perf_counter()
    final = await GRAPH.ainvoke(
        init,
        config={
            "configurable": {"deps": deps},
            "recursion_limit": 4 * deps.spec.params.max_steps + 10,
        },
    )
    return final, (time.perf_counter() - t0) * 1000
