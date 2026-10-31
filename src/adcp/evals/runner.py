"""Offline eval: run every frozen task through the harness, print metrics, append a runs/ row.

    python -m adcp.evals.runner --spec specs/v1.yaml [--tasks evals/frozen/v1/tasks.jsonl] \
        [--out runs/offline.jsonl] [--experiment offline] [--variant v1]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from adcp.env.tasks import Task, load_tasks
from adcp.evals.metrics import Pair, aggregate, append_rows, make_row
from adcp.spec import AgentSpec
from adcp.types import Track

REPO_ROOT = Path(__file__).resolve().parents[3]
FROZEN_TASKS = REPO_ROOT / "evals" / "frozen" / "v1" / "tasks.jsonl"
CONCURRENCY = 1  # mock prompt cache is process-global; sequential keeps cache hits deterministic


def reset_mock_cache() -> None:
    """Cold simulated prompt cache per evaluation so variants do not warm each other."""
    try:
        from adcp.providers.mock import MockProvider

        MockProvider.reset_cache()
    except (ImportError, AttributeError):
        pass


def default_tasks_path() -> Path:
    return FROZEN_TASKS if FROZEN_TASKS.exists() else REPO_ROOT / "evals" / "tasks" / "tasks.jsonl"


async def aevaluate_pairs(
    spec: AgentSpec, tasks: Sequence[Task], track: Track = "stable", fault: Any = None
) -> list[Pair]:
    from adcp.runtime.harness import arun_task

    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(task: Task) -> Pair:
        async with sem:
            return await arun_task(spec, task, track=track, fault=fault)

    return list(await asyncio.gather(*(one(t) for t in tasks)))


def evaluate_spec(
    spec: AgentSpec | str | Path,
    tasks: Sequence[Task] | None = None,
    track: Track = "stable",
    fault: Any = None,
) -> tuple[list[Pair], dict[str, Any]]:
    """Run `tasks` (default: frozen set) against `spec`. Returns (per-task pairs, metrics)."""
    if not isinstance(spec, AgentSpec):
        spec = AgentSpec.from_yaml(spec)
    tasks = list(tasks) if tasks is not None else load_tasks(default_tasks_path())
    reset_mock_cache()
    pairs = asyncio.run(aevaluate_pairs(spec, tasks, track=track, fault=fault))
    return pairs, aggregate(pairs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="offline eval of an AgentSpec on the frozen set")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--out", default="runs/offline.jsonl")
    ap.add_argument("--experiment", default="offline")
    ap.add_argument("--variant", default=None)
    args = ap.parse_args(argv)
    spec = AgentSpec.from_yaml(args.spec)
    tasks = load_tasks(args.tasks or default_tasks_path())
    _, metrics = evaluate_spec(spec, tasks)
    row = make_row(metrics, experiment=args.experiment, variant=args.variant, spec=spec)
    append_rows(args.out, [row])
    print(json.dumps({"spec": f"{spec.name}@{spec.version}", **metrics}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
