"""Rollback controller service (:8090).

    python -m adcp.rollback.service

GET  /healthz
POST /decide   body {"stable": ArmMetrics, "canary": ArmMetrics} -> pure comparison (no actions);
               empty body -> one controller step against Prometheus (acts).
GET  /history  last decisions.
BACKEND=services starts a loop calling `step()` every ADCP_ROLLBACK_INTERVAL_S (default 30).
In-cluster ServiceAccount -> real Argo client; GITHUB_TOKEN set -> real issues; else dry-run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Body, FastAPI

from adcp.config import get_settings
from adcp.rollback.argo import SA_DIR, ArgoRolloutsClient, DryRunArgoClient, RolloutsClient
from adcp.rollback.controller import RollbackController
from adcp.rollback.github import DryRunIssueClient, GitHubIssueClient, IssueClient
from adcp.rollback.metrics_source import PrometheusSource
from adcp.rollback.pareto import RULES, ArmSnapshot, ParetoPolicy

log = logging.getLogger("adcp.rollback")


def build_controller() -> RollbackController:
    s = get_settings()
    argo: RolloutsClient
    if (SA_DIR / "token").exists():
        argo = ArgoRolloutsClient.in_cluster(s.argo_rollout_name, s.argo_rollouts_namespace)
    else:
        argo = DryRunArgoClient()
    issues: IssueClient = (
        GitHubIssueClient(s.github_repo, s.github_token) if s.github_token else DryRunIssueClient()
    )
    bus = None
    try:
        from adcp.runtime.events import get_event_bus

        bus = get_event_bus()
    except Exception as e:
        log.info("no event bus: %s", e)
    registry = None
    if s.backend == "services":
        try:
            from adcp.registry import SpecRegistry

            registry = SpecRegistry()
        except Exception as e:
            log.info("no registry: %s", e)
    return RollbackController(
        PrometheusSource(s.prometheus_url),
        argo=argo,
        issues=issues,
        policy=ParetoPolicy(),
        registry=registry,
        bus=bus,
        rule=os.environ.get("ADCP_ROLLBACK_RULE", "pareto"),
    )


_state: dict[str, Any] = {}


async def _loop(ctrl: RollbackController, interval: float) -> None:
    while True:
        try:
            rec = await ctrl.step()
            log.info("decision %s %s", rec.decision, rec.comparison.reason)
        except Exception as e:
            log.warning("step failed: %s", e)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ctrl = _state.setdefault("controller", build_controller())
    task = None
    if get_settings().backend == "services":
        interval = float(os.environ.get("ADCP_ROLLBACK_INTERVAL_S", "30"))
        task = asyncio.create_task(_loop(ctrl, interval))
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="adcp rollback controller", lifespan=lifespan)


def controller() -> RollbackController:
    return _state.setdefault("controller", build_controller())  # type: ignore[no-any-return]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/decide")
async def decide(body: Annotated[dict[str, Any] | None, Body()] = None) -> dict[str, Any]:
    ctrl = controller()
    if body and "stable" in body and "canary" in body:
        rule = RULES[body.get("rule", "pareto")]
        policy = ParetoPolicy(**body.get("policy", {})) if body.get("policy") else ctrl.policy
        comp = rule(
            ArmSnapshot.model_validate(body["stable"]),
            ArmSnapshot.model_validate(body["canary"]),
            policy,
        )
        return comp.model_dump(mode="json")
    rec = await ctrl.step()
    return rec.event()


@app.get("/history")
async def history(limit: int = 20) -> list[dict[str, Any]]:
    return [r.event() for r in controller().history[-limit:]]


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8090")))


if __name__ == "__main__":
    main()
