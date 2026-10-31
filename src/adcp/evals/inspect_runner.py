"""Run the Inspect AI suite programmatically and reduce the log to the shared metrics dict."""

from __future__ import annotations

import os
from typing import Any

from adcp.evals.metrics import Pair, aggregate
from adcp.types import EvalResult, RunResult


def run_inspect(
    spec_path: str,
    log_dir: str = "logs/inspect",
    tasks_path: str | None = None,
    max_samples: int = 1,  # sequential: deterministic simulated prompt cache
) -> dict[str, Any]:
    from inspect_ai import eval as inspect_eval

    from adcp.evals.inspect_suite import build_task
    from adcp.evals.runner import reset_mock_cache

    os.environ.setdefault("MODEL_PROVIDER", "mock")
    reset_mock_cache()
    (log,) = inspect_eval(
        build_task(spec_path, tasks_path),
        model="mockllm/model",
        log_dir=log_dir,
        display="none",
        max_samples=max_samples,
        fail_on_error=False,
    )
    if log.status != "success":
        raise RuntimeError(f"inspect eval {log.status}: {log.error}")
    pairs: list[Pair] = [
        (
            RunResult.model_validate(s.metadata["run_result"]),
            EvalResult.model_validate(s.metadata["eval_result"]),
        )
        for s in (log.samples or [])
        if "run_result" in s.metadata
    ]
    out = aggregate(pairs)
    scores = log.results.scores if log.results else []
    out["inspect"] = {
        "log": log.location,
        "metrics": {k: round(v.value, 6) for s in scores for k, v in s.metrics.items()},
    }
    return out
