"""ArmSnapshot / RolloutOutcome -> shared runs/*.jsonl rows (schema in adcp.evals.metrics)."""

from __future__ import annotations

from typing import Any

from adcp.evals.metrics import append_rows, make_row
from adcp.rollback.pareto import ArmSnapshot
from adcp.spec import AgentSpec

__all__ = ["append_rows", "arm_row", "snapshot_metrics"]


def snapshot_metrics(a: ArmSnapshot) -> dict[str, Any]:
    return {
        "n": a.n,
        "task_success": round(a.success_rate, 4),
        "eval_score": round(a.eval_score, 4),
        "cost_per_task": round(a.cost_per_task, 6),
        "p50_latency_ms": round(a.p50_latency_ms, 1),
        "p95_latency_ms": round(a.p95_latency_ms, 1),
        "tool_error_rate": round(a.tool_error_rate, 4),
        "tokens_in": a.tokens_in,
        "tokens_out": a.tokens_out,
        "cache_hit_rate": round(a.cache_hit_rate, 4),
    }


def arm_row(
    experiment: str,
    variant: str,
    arm: ArmSnapshot | None,
    spec: AgentSpec | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return make_row(
        snapshot_metrics(arm) if arm else {},
        experiment=experiment,
        variant=variant,
        spec=spec,
        **extra,
    )
