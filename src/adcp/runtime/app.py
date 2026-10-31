"""FastAPI runtime: POST /run, GET /healthz, /readyz, /spec.

Spec: AGENT_SPEC_PATH (fallback ADCP_SPEC_PATH) or MLflow registry alias ADCP_SPEC_ALIAS
(e.g. production), else specs/v1.yaml. Track: pod label adcp.dev/track from
ADCP_TRACK_LABELS_FILE (downward API, flips on promotion) > X-ADCP-Track header > ADCP_TRACK > body.
Rollout step for flag targeting: X-Rollout-Step header > ADCP_ROLLOUT_STEP.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, Header, HTTPException

from adcp.config import get_settings
from adcp.env.tasks import Task, load_tasks, resolve_path
from adcp.runtime.budget import BudgetStore
from adcp.runtime.events import TOPIC_EVALS, TOPIC_RUNS, get_event_bus
from adcp.runtime.harness import arun_request, arun_task, rollout_step_default
from adcp.runtime.mcp_client import MCPToolClient
from adcp.runtime.mcp_server import default_state
from adcp.runtime.store import RunStore
from adcp.spec import AgentSpec
from adcp.types import RunRequest, RunResult

log = logging.getLogger("adcp.runtime")
TRACK_LABEL = "adcp.dev/track"


def load_spec() -> AgentSpec:
    path = os.getenv("AGENT_SPEC_PATH") or os.getenv("ADCP_SPEC_PATH")
    if path:
        return AgentSpec.from_yaml(resolve_path(path))
    alias = os.getenv("ADCP_SPEC_ALIAS")
    if alias:
        from adcp.registry import SpecRegistry

        return SpecRegistry().get(alias)
    return AgentSpec.from_yaml(resolve_path("specs/v1.yaml"))


def label_track() -> str | None:
    """adcp.dev/track from the downward-API labels file (flips on promotion)."""
    f = os.getenv("ADCP_TRACK_LABELS_FILE")
    if f and Path(f).exists():
        for line in Path(f).read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == TRACK_LABEL:
                return v.strip().strip('"')
    return None


def pod_track() -> str | None:
    return label_track() or os.getenv("ADCP_TRACK")


def resolve_track(header: str | None, body: str) -> Literal["stable", "canary"]:
    t = label_track() or header or os.getenv("ADCP_TRACK") or body
    return "canary" if t == "canary" else "stable"


class Runtime:
    def __init__(self) -> None:
        self.spec: AgentSpec | None = None
        self.tasks: dict[str, Task] = {}
        self.store: RunStore | None = None
        self.default_state: dict[str, Any] = {}
        self.budget = BudgetStore()

    def load(self) -> None:
        self.spec = load_spec()
        self.tasks = {t.id: t for t in load_tasks()}
        self.default_state = default_state()
        try:
            self.store = RunStore()
        except Exception as e:  # noqa: BLE001 - store optional for serving
            log.warning("run store unavailable: %s", e)


rt = Runtime()


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    rt.load()
    yield
    await get_event_bus().close()


app = FastAPI(title="agent-runtime", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    if rt.spec is None:
        raise HTTPException(503, "spec not loaded")
    return {"status": "ready", "spec_version": rt.spec.version, "track": pod_track() or "stable"}


@app.get("/spec")
def spec() -> dict[str, Any]:
    if rt.spec is None:
        raise HTTPException(503, "spec not loaded")
    return {"fingerprint": rt.spec.fingerprint(), "spec": rt.spec.model_dump(mode="json")}


@app.post("/run", response_model=RunResult)
async def run(
    req: RunRequest,
    x_adcp_track: str | None = Header(default=None),
    x_rollout_step: int | None = Header(default=None),
) -> RunResult:
    if rt.spec is None:
        raise HTTPException(503, "spec not loaded")
    spec_ = rt.spec
    req.track = resolve_track(x_adcp_track, req.track)
    if not rt.budget.allow(f"{spec_.version}:{req.session_id or 'anon'}"):
        raise HTTPException(429, "rate limited")
    step = x_rollout_step if x_rollout_step is not None else rollout_step_default()
    bus = get_event_bus()
    task = rt.tasks.get(req.task_id or "")
    if task is not None:
        # replay a known task: same env + scoring as offline eval; publishes the eval too
        task = task.model_copy(update={"user_input": req.input or task.user_input})
        result, ev = await arun_task(spec_, task, cast(Any, req.track), rollout_step=step)
        if os.environ.get("ADCP_INLINE_EVAL") == "1":  # else adcp.evals.online scores agent.runs
            evt = {**ev.model_dump(mode="json"), "track": req.track, "spec_version": spec_.version}
            await _safe(bus.publish(TOPIC_EVALS, evt))
        if rt.store:
            rt.store.save_eval(ev, spec_.version, req.track)
    else:
        result = await arun_request(
            spec_, req, MCPToolClient.for_state(rt.default_state), rollout_step=step
        )
    rt.budget.add_spend(spec_.version, req.track, result.cost_usd)
    await _safe(bus.publish(TOPIC_RUNS, result))
    if rt.store:
        rt.store.save_run(result)
    return result


async def _safe(coro: Any) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001 - event bus outage must not fail the request
        log.warning("event publish failed: %s", e)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    _ = get_settings()
    main()
