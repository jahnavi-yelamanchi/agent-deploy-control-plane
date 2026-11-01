"""In-process canary / blue-green simulator driving the real harness and RollbackController.

Simulated clock: requests arrive at `rps`; each is routed to canary with probability
weight/100, the task is drawn from the frozen set with a seeded RNG, and executed with
`adcp.runtime.harness.arun_task` (mock provider). After every `batch` requests the controller
decides once both arms have `policy.min_samples` runs.

Canary: steps [10, 25, 50, 100]. rollback / needs_human act as soon as the controller sees them;
promote advances only after `step_duration_s` dwell (Argo pause), so evidence grows with weight.
rollback -> abort (weight 0); needs_human -> paused at current weight; hold until
`max_duration_s` -> outcome hold.
Blue-green: preview traffic mirrored to both arms; rollback / needs_human end the preview,
promote needs `preview_samples` first, then a 0 -> 100 switch. Users never hit the preview.

Pareto rollback / needs_human must repeat on `confirm_looks` consecutive looks (one look per
batch) before the controller acts, which bounds false positives from repeated looks; SLO
breaches act on the first look.

time_to_detect_s: simulated seconds from canary start to the rollback / needs_human decision.
time_to_rollback_s: detect + `abort_latency_s` (Argo abort + Istio route update).
Latency gets seeded multiplicative jitter (`latency_jitter`, network noise); the mock provider
is otherwise deterministic per (spec, task).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from adcp.rollback.argo import DryRunArgoClient
from adcp.rollback.controller import DecisionRecord, RollbackController, Rule
from adcp.rollback.github import IssueClient, NullIssueClient
from adcp.rollback.metrics_source import InMemorySource, PairedSource
from adcp.rollback.pareto import ArmSnapshot, Comparison, Decision, ParetoPolicy
from adcp.spec import AgentSpec
from adcp.types import EvalResult, RunResult, Track


class RolloutConfig(BaseModel):
    mode: Literal["canary", "bluegreen"] = "canary"
    steps: list[int] = Field(default_factory=lambda: [10, 25, 50, 100])
    rps: float = 2.0
    batch: int = 10
    step_duration_s: float = 300.0  # dwell per step before a promote advances (Argo pause)
    max_duration_s: float = 3600.0
    abort_latency_s: float = 5.0
    preview_samples: int = 100  # blue-green: min mirrored samples before promote
    latency_jitter: float = 0.08
    seed: int = 0
    rule: str = "pareto"
    confirm_looks: int = 3  # consecutive identical rollback/needs_human looks before acting
    flags_follow_step: bool = False  # pass the step index as flag context rolloutStep


class Event(BaseModel):
    t_s: float
    weight: int
    decision: Decision
    reason: str


class RolloutOutcome(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    mode: str
    stable_version: str
    canary_version: str
    fault: str | None = None
    rule: str = "pareto"
    decision: Decision
    final_weight: int
    first_weight: int
    time_to_detect_s: float | None
    time_to_rollback_s: float | None
    duration_s: float
    n_requests: int
    canary_requests: int
    stable: ArmSnapshot
    canary: ArmSnapshot
    comparison: Comparison | None = None
    events: list[Event] = Field(default_factory=list)
    issue: str | None = None
    expected: Decision | None = None

    @property
    def rollback_triggered(self) -> bool:
        return self.decision == Decision.rollback

    @property
    def false_rollback(self) -> bool | None:
        if self.expected is None:
            return None
        return self.rollback_triggered and self.expected != Decision.rollback


class _Runner:
    """Executes tasks through the harness; memoizes the deterministic part per (spec, task, fault)."""

    def __init__(self, cache: dict[Any, tuple[RunResult, EvalResult]] | None = None) -> None:
        from adcp.runtime.harness import arun_task

        self._arun = arun_task
        self.cache = cache if cache is not None else {}

    async def warm(self, spec: AgentSpec, task: Any, fault: Any) -> None:
        # first call per prompt prefix pays the cache write; keep it out of the measured window
        await self._arun(spec, task, track="stable", fault=fault, rollout_step=0)

    async def run(
        self, spec: AgentSpec, task: Any, track: Track, fault: Any, step: int = 0
    ) -> tuple[RunResult, EvalResult]:
        key = (spec.fingerprint(), task.id, repr(fault) if fault else None, track, step)
        if key not in self.cache:
            self.cache[key] = await self._arun(
                spec, task, track=track, fault=fault, rollout_step=step
            )
        return self.cache[key]


_SHARED_CACHE: dict[Any, tuple[RunResult, EvalResult]] = {}


def _jitter(run: RunResult, rng: random.Random, sigma: float, track: Track) -> RunResult:
    f = rng.lognormvariate(0.0, sigma) if sigma else 1.0
    return run.model_copy(update={"latency_ms": run.latency_ms * f, "track": track})


async def arun_rollout(
    stable: AgentSpec,
    canary: AgentSpec,
    config: RolloutConfig | None = None,
    policy: ParetoPolicy | None = None,
    fault: Any = None,
    controller: RollbackController | None = None,
    tasks: list[Any] | None = None,
    expected: Decision | None = None,
    issues: IssueClient | None = None,
    canary_registry_version: int | None = None,
    rule: str | Rule | None = None,
    cache: dict[Any, tuple[RunResult, EvalResult]] | None = None,
) -> RolloutOutcome:
    from adcp.env.tasks import load_tasks

    cfg = config or RolloutConfig()
    tasks = tasks or load_tasks()
    rng = random.Random(cfg.seed)
    runner = _Runner(_SHARED_CACHE if cache is None else cache)
    source = PairedSource() if cfg.mode == "bluegreen" else InMemorySource()
    argo = DryRunArgoClient(revision=f"{canary.version}-{canary.fingerprint()[:8]}")
    if controller is None:
        controller = RollbackController(
            source,
            argo=argo,
            issues=issues or NullIssueClient(),
            policy=policy,
            rule=rule or cfg.rule,
        )
    else:
        controller.source = source
        if isinstance(controller.argo, DryRunArgoClient):
            controller.argo.new_revision(argo.state["revision"])
            argo = controller.argo
    pol = controller.policy
    rule_name = rule if isinstance(rule, str) else cfg.rule
    await runner.warm(stable, tasks[0], None)
    await runner.warm(canary, tasks[0], fault)

    t = 0.0
    dt = 1.0 / cfg.rps
    events: list[Event] = []
    detect: float | None = None
    last: DecisionRecord | None = None
    n_req = can_req = 0

    async def serve(track: Track, task: Any, step: int = 0) -> None:
        spec, f = (canary, fault) if track == "canary" else (stable, None)
        run, ev = await runner.run(spec, task, track, f, step if cfg.flags_follow_step else 0)
        source.record(_jitter(run, rng, cfg.latency_jitter, track), ev, track)

    async def ask(weight: int, step: int, final: bool) -> DecisionRecord:
        rec = await controller.step(
            step=step, final_step=final, canary_registry_version=canary_registry_version
        )
        events.append(
            Event(t_s=t, weight=weight, decision=rec.decision, reason=rec.comparison.reason)
        )
        return rec

    decision = Decision.hold
    if cfg.mode == "bluegreen":
        weight = 0
        prev, streak = Decision.hold, 0
        while t < cfg.max_duration_s:
            for _ in range(cfg.batch):
                task = tasks[rng.randrange(len(tasks))]
                await serve("stable", task)
                await serve("canary", task)  # mirrored preview traffic, not user-facing
                n_req += 1
                t += dt
            if source.count("canary") < pol.slo_min_samples:
                continue
            comp = await controller.decide()
            streak = streak + 1 if comp.decision == prev else 1
            prev = comp.decision
            enough = source.count("canary") >= cfg.preview_samples
            if comp.decision == Decision.hold or (comp.decision == Decision.promote and not enough):
                continue
            if (
                comp.decision != Decision.promote
                and not comp.slo_breaches
                and streak < cfg.confirm_looks
            ):
                continue
            last = await ask(0, 0, True)
            decision = last.decision
            if decision != Decision.promote:
                detect = t
            weight = 100 if decision == Decision.promote else 0
            break
    else:
        weight = cfg.steps[0]
        step_idx = 0
        step_start = 0.0
        prev, streak = Decision.hold, 0
        while t < cfg.max_duration_s:
            argo.set_weight(weight)
            for _ in range(cfg.batch):
                task = tasks[rng.randrange(len(tasks))]
                track: Track = "canary" if rng.random() * 100 < weight else "stable"
                await serve(track, task, step_idx)
                n_req += 1
                can_req += track == "canary"
                t += dt
            if source.count("canary") < pol.slo_min_samples:
                continue
            comp = await controller.decide()
            d = comp.decision
            streak = streak + 1 if d == prev else 1
            prev = d
            dwell_done = t - step_start >= cfg.step_duration_s
            if d == Decision.hold or (d == Decision.promote and not dwell_done):
                continue
            if d != Decision.promote and not comp.slo_breaches and streak < cfg.confirm_looks:
                continue
            final = step_idx == len(cfg.steps) - 1
            last = await ask(weight, step_idx, final)
            d = last.decision
            if d == Decision.promote and not final:
                step_idx += 1
                weight = cfg.steps[step_idx]
                step_start = t
                continue
            decision = d
            if d != Decision.promote:
                detect = t
            if d == Decision.rollback:
                weight = 0
            break

    stable_snap, canary_snap = await source.arms()
    ttr = (detect + cfg.abort_latency_s) if decision == Decision.rollback and detect else None
    return RolloutOutcome(
        mode=cfg.mode,
        stable_version=stable.version,
        canary_version=canary.version,
        fault=getattr(fault, "name", None),
        rule=rule_name if isinstance(rule_name, str) else "custom",
        decision=decision,
        final_weight=weight,
        first_weight=cfg.steps[0] if cfg.mode == "canary" else 0,
        time_to_detect_s=detect,
        time_to_rollback_s=ttr,
        duration_s=t,
        n_requests=n_req,
        canary_requests=can_req,
        stable=stable_snap,
        canary=canary_snap,
        comparison=last.comparison if last else None,
        events=events,
        issue=last.issue if last else None,
        expected=expected,
    )


def run_rollout(stable: AgentSpec, canary: AgentSpec, **kw: Any) -> RolloutOutcome:
    return asyncio.run(arun_rollout(stable, canary, **kw))
