"""Run a spec on a task (fresh env per task) and score it.

Fault hooks (adcp.chaos), each optional: fault.mutate_spec(spec) -> spec,
fault.wrap_provider(provider) -> provider, fault.wrap_tools(tool_client) -> tool_client.
Flags are evaluated with context {track, rolloutStep}; rolloutStep defaults to ADCP_ROLLOUT_STEP.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Literal

from adcp.env.scoring import score
from adcp.env.tasks import Task
from adcp.providers import get_provider
from adcp.providers.base import ModelProvider
from adcp.runtime.agent import AgentDeps, run_agent
from adcp.runtime.flags import Flags
from adcp.runtime.mcp_client import MCPToolClient
from adcp.runtime.telemetry import get_telemetry
from adcp.spec import AgentSpec
from adcp.types import EvalResult, RunRequest, RunResult

ToolMode = Literal["inprocess", "stdio", "http"]


def rollout_step_default() -> int:
    try:
        return int(os.getenv("ADCP_ROLLOUT_STEP", "0"))
    except ValueError:
        return 0


def _run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """asyncio.run, or a worker thread when called from inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["v"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    th = threading.Thread(target=target)
    th.start()
    th.join()
    if "e" in box:
        raise box["e"]
    return box["v"]  # type: ignore[no-any-return]


async def arun_request(
    spec: AgentSpec,
    req: RunRequest,
    tool_client: Any,
    *,
    provider: ModelProvider | None = None,
    fault: Any = None,
    rollout_step: int | None = None,
) -> RunResult:
    """Core run: flags -> tools/context -> LangGraph loop -> RunResult (+ telemetry)."""
    if fault is not None and (m := getattr(fault, "mutate_spec", None)):
        spec = m(spec)
    step = rollout_step_default() if rollout_step is None else rollout_step
    flags = Flags(req.track, step, **({"session_id": req.session_id} if req.session_id else {}))
    tool_specs = flags.enabled_tools(spec.tools)
    strategy = flags.context_strategy(spec)
    prov = provider or get_provider(spec.provider)
    tools = tool_client
    if fault is not None:
        if wp := getattr(fault, "wrap_provider", None):
            prov = wp(prov)
        if wt := getattr(fault, "wrap_tools", None):
            tools = wt(tools)
    tel = get_telemetry()
    labels = tel.labels(spec.version, req.track, spec.model_id)
    result = RunResult(
        spec_name=spec.name,
        spec_version=spec.version,
        spec_fingerprint=spec.fingerprint(),
        model_id=spec.model_id,
        provider=spec.provider.value,
        track=req.track,
        task_id=req.task_id,
    )
    attrs = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": spec.name,
        "gen_ai.provider.name": spec.provider.value,
        "gen_ai.request.model": spec.model_id,
        "adcp.agent_version": spec.version,
        "adcp.track": req.track,
        "adcp.task_id": req.task_id,
        "adcp.context_strategy": strategy.value,
        "adcp.flags.canary_step": flags.string("canary-step", "stable"),
    }
    with tel.span(f"invoke_agent {spec.name}", **attrs) as sp:
        result.run_id = format(sp.get_span_context().trace_id, "032x") or result.run_id
        deps = AgentDeps(
            spec=spec,
            provider=prov,
            tools=tools,
            tool_specs=tool_specs,
            strategy=strategy,
            user_input=req.input,
            metadata={"task_id": req.task_id, "session_id": req.session_id, **req.metadata},
            labels=labels,
            telemetry=tel,
        )
        try:
            state, _wall_ms = await asyncio.wait_for(run_agent(deps), timeout=spec.params.timeout_s)
            result.output = state.get("output", "")
            result.structured = state.get("structured")
            result.tool_calls = state.get("tool_calls", [])
            result.usage = state["usage"]
            result.cost_usd = round(state["cost_usd"], 8)
            result.latency_ms = round(state["latency_ms"], 2)
            result.steps = state["steps"]
            result.status = state.get("status", "ok")  # type: ignore[assignment]
            result.error = state.get("error")
        except Exception as e:  # noqa: BLE001 - provider/tool crash -> error run
            result.status, result.error = "error", f"{type(e).__name__}: {e}"
        sp.set_attribute("gen_ai.usage.input_tokens", result.usage.input_tokens)
        sp.set_attribute("gen_ai.usage.output_tokens", result.usage.output_tokens)
        sp.set_attribute("adcp.cost_usd", result.cost_usd)
        sp.set_attribute("adcp.status", result.status)
    tel.record_run(result.cost_usd, result.latency_ms, result.status, labels)
    return result


async def arun_task(
    spec: AgentSpec,
    task: Task,
    track: Literal["stable", "canary"] = "stable",
    fault: Any = None,
    *,
    provider: ModelProvider | None = None,
    rollout_step: int | None = None,
    tool_mode: ToolMode = "inprocess",
) -> tuple[RunResult, EvalResult]:
    req = RunRequest(input=task.user_input, task_id=task.id, track=track)
    if tool_mode == "inprocess":
        client = MCPToolClient.for_state(task.initial_state)
        result = await arun_request(
            spec, req, client, provider=provider, fault=fault, rollout_step=rollout_step
        )
        final_state = await client.final_state()
    else:
        async with MCPToolClient(tool_mode, task_id=task.id) as client:
            result = await arun_request(
                spec, req, client, provider=provider, fault=fault, rollout_step=rollout_step
            )
            final_state = await client.final_state()
    ev = score(task, result, final_state)
    get_telemetry().record_eval(
        ev.score, get_telemetry().labels(spec.version, track, spec.model_id)
    )
    return result, ev


def run_task(
    spec: AgentSpec,
    task: Task,
    track: Literal["stable", "canary"] = "stable",
    fault: Any = None,
    **kw: Any,
) -> tuple[RunResult, EvalResult]:
    return _run_sync(arun_task(spec, task, track, fault, **kw))


async def arun_batch(
    spec: AgentSpec,
    tasks: list[Task],
    track: Literal["stable", "canary"] = "stable",
    fault: Any = None,
    concurrency: int = 8,
    on_result: Callable[[RunResult, EvalResult], Awaitable[None] | None] | None = None,
    **kw: Any,
) -> list[tuple[RunResult, EvalResult]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(t: Task) -> tuple[RunResult, EvalResult]:
        async with sem:
            rr, ev = await arun_task(spec, t, track, fault, **kw)
        if on_result is not None:
            maybe = on_result(rr, ev)
            if asyncio.iscoroutine(maybe):
                await maybe
        return rr, ev

    return list(await asyncio.gather(*(one(t) for t in tasks)))


def run_batch(
    spec: AgentSpec,
    tasks: list[Task],
    track: Literal["stable", "canary"] = "stable",
    fault: Any = None,
    concurrency: int = 8,
    **kw: Any,
) -> list[tuple[RunResult, EvalResult]]:
    return _run_sync(arun_batch(spec, tasks, track, fault, concurrency, **kw))
