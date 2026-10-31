"""Inspect AI building blocks for the tau-style suite: dataset, harness solver, state scorer.

The solver ignores Inspect's model (run with `mockllm/model`) and drives the adcp harness with the
AgentSpec's own provider, so the Inspect log records exactly what the deployed artifact would do.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inspect_ai import Task as InspectTask
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ModelOutput
from inspect_ai.scorer import (
    Metric,
    SampleScore,
    Score,
    Scorer,
    Target,
    accuracy,
    mean,
    metric,
    scorer,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver

from adcp.env.tasks import Task, load_tasks
from adcp.evals.metrics import percentile
from adcp.spec import AgentSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
FROZEN_TASKS = REPO_ROOT / "evals" / "frozen" / "v1" / "tasks.jsonl"


def _meta(scores: list[SampleScore], key: str) -> list[float]:
    return [float((s.score.metadata or {}).get(key, 0.0)) for s in scores]


@metric
def task_success() -> Metric:
    def m(scores: list[SampleScore]) -> float:
        xs = _meta(scores, "success")
        return sum(xs) / len(xs) if xs else 0.0

    return m


@metric
def cost_per_task() -> Metric:
    def m(scores: list[SampleScore]) -> float:
        xs = _meta(scores, "cost_usd")
        return sum(xs) / len(xs) if xs else 0.0

    return m


@metric
def p95_latency_ms() -> Metric:
    def m(scores: list[SampleScore]) -> float:
        return percentile(_meta(scores, "latency_ms"), 95)

    return m


def build_dataset(tasks: list[Task]) -> MemoryDataset:
    samples = [
        Sample(
            id=t.id,
            input=t.user_input,
            target=json.dumps(t.expected_outputs),
            metadata={"task_id": t.id, "domain": t.domain, "tags": t.tags},
        )
        for t in tasks
    ]
    return MemoryDataset(samples, name="tau-mock-frozen-v1")


@solver
def adcp_harness(spec_path: str, tasks_path: str) -> Solver:
    spec = AgentSpec.from_yaml(spec_path)
    by_id = {t.id: t for t in load_tasks(tasks_path)}

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        from adcp.runtime.harness import arun_task

        task = by_id[str(state.metadata["task_id"])]
        run, ev = await arun_task(spec, task, track="stable")
        state.metadata["run_result"] = run.model_dump(mode="json")
        state.metadata["eval_result"] = ev.model_dump(mode="json")
        state.output = ModelOutput.from_content(model=f"adcp/{spec.model_id}", content=run.output)
        state.completed = True
        return state

    return solve


@scorer(metrics=[accuracy(), mean(), task_success(), cost_per_task(), p95_latency_ms()])
def tau_state_scorer() -> Scorer:
    """Value = harness EvalResult.score (0.7 state match + 0.2 outputs + 0.1 tool health)."""

    async def score(state: TaskState, target: Target) -> Score:
        ev: dict[str, Any] = state.metadata["eval_result"]
        run: dict[str, Any] = state.metadata["run_result"]
        return Score(
            value=float(ev["score"]),
            answer=state.output.completion,
            explanation=json.dumps(ev.get("details", {}))[:2000],
            metadata={
                "success": float(ev["success"]),
                "cost_usd": run["cost_usd"],
                "latency_ms": run["latency_ms"],
                "status": run["status"],
                "tools": [t["name"] for t in run["tool_calls"]],
            },
        )

    return score


def build_task(spec_path: str = "specs/v1.yaml", tasks_path: str | None = None) -> InspectTask:
    tp = tasks_path or str(FROZEN_TASKS)
    sp = spec_path if Path(spec_path).is_absolute() else str(REPO_ROOT / spec_path)
    spec = AgentSpec.from_yaml(sp)
    return InspectTask(
        dataset=build_dataset(load_tasks(tp)),
        solver=adcp_harness(sp, tp),
        scorer=tau_state_scorer(),
        name=f"tau_mock_{spec.version}",
        metadata={"spec": spec.name, "spec_version": spec.version, "model_id": spec.model_id},
    )
