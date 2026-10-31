"""promptfoo Python provider: runs one frozen task through the adcp harness (mock provider).

The promptfoo "prompt" is the AgentSpec itself (specs/*.yaml, passed through as YAML or JSON), so
each prompt column is a deployable artifact version. vars.task_id picks the frozen task.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
os.environ.setdefault("MODEL_PROVIDER", "mock")

from adcp.env.tasks import get_task  # noqa: E402
from adcp.runtime.harness import run_task  # noqa: E402
from adcp.spec import AgentSpec  # noqa: E402

FROZEN = REPO_ROOT / "evals" / "frozen" / "v1" / "tasks.jsonl"


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        spec = AgentSpec.model_validate(yaml.safe_load(prompt))
        task = get_task(str(context["vars"]["task_id"]), FROZEN)
        run, ev = run_task(spec, task)
    except Exception as e:  # promptfoo shows provider errors per cell
        return {"error": f"{type(e).__name__}: {e}"}
    u = run.usage
    return {
        "output": run.output,
        "cost": run.cost_usd,
        "tokenUsage": {
            "prompt": u.input_tokens + u.cache_read_tokens + u.cache_write_tokens,
            "completion": u.output_tokens,
            "total": u.input_tokens + u.cache_read_tokens + u.cache_write_tokens + u.output_tokens,
            "cached": u.cache_read_tokens,
        },
        "metadata": {
            "spec_version": spec.version,
            "tools": [t.name for t in run.tool_calls],
            "tool_errors": sum(not t.ok for t in run.tool_calls),
            "latency_ms": run.latency_ms,
            "status": run.status,
            "eval_score": ev.score,
            "success": ev.success,
        },
    }
