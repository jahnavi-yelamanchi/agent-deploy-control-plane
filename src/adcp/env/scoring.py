"""tau-bench-style scoring: final DB state vs ground-truth replay + required output substrings."""

from __future__ import annotations

from typing import Any

from adcp.env.tasks import Task
from adcp.env.tools import apply_actions, state_hash
from adcp.types import EvalResult, RunResult

W_STATE, W_OUTPUTS, W_TOOLS = 0.7, 0.2, 0.1


def expected_state_hash(task: Task) -> str:
    return state_hash(apply_actions(task.initial_state, task.expected_actions))


def score(task: Task, run_result: RunResult, final_state: dict[str, Any]) -> EvalResult:
    """success iff state hash matches AND all expected outputs appear (case-insensitive)."""
    expected = expected_state_hash(task)
    actual = state_hash(final_state)
    state_match = float(expected == actual)
    text = (run_result.output or "").lower()
    missing = [o for o in task.expected_outputs if o.lower() not in text]
    n_out = len(task.expected_outputs)
    outputs_fraction = (n_out - len(missing)) / n_out if n_out else 1.0
    ter = run_result.tool_error_rate
    value = W_STATE * state_match + W_OUTPUTS * outputs_fraction + W_TOOLS * (1.0 - ter)
    success = bool(state_match) and not missing and run_result.status == "ok"
    return EvalResult(
        run_id=run_result.run_id,
        task_id=task.id,
        success=success,
        score=round(value, 4),
        details={
            "state_match": bool(state_match),
            "expected_state_hash": expected,
            "actual_state_hash": actual,
            "outputs_fraction": round(outputs_fraction, 4),
            "missing_outputs": missing,
            "tool_error_rate": round(ter, 4),
            "status": run_result.status,
            "tags": task.tags,
        },
    )
