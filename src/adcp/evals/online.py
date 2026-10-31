"""Online evaluator: agent.runs -> score -> agent.evals + rolling per-arm metrics.

    python -m adcp.evals.online [--from-start] [--window 200] [--selftest]

Runs whose task_id is in the frozen set are scored by replaying RunResult.tool_calls on the task's
initial state (the env is deterministic, so replay == final state) and applying env scoring.
Publishes `agent.evals` payloads `{**EvalResult, track, spec_version, run}` (the shape
adcp.rollback.metrics_source.InMemorySource.on_event consumes) and records `adcp_eval_score`.
Unknown task ids are counted, not scored. Backend: Kafka when ADCP backend=services, else in-memory.
--selftest runs the frozen set through the harness on the in-memory bus and consumes it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import deque
from typing import Any

from adcp.env.scoring import score
from adcp.env.tasks import Task, load_tasks
from adcp.env.tools import apply_actions
from adcp.evals.metrics import Pair, arm_metrics
from adcp.evals.runner import default_tasks_path
from adcp.runtime.events import TOPIC_EVALS, TOPIC_RUNS, EventBus, get_event_bus
from adcp.types import ArmMetrics, EvalResult, RunResult

log = logging.getLogger("adcp.evals.online")


class OnlineEvaluator:
    def __init__(self, tasks: list[Task], window: int = 200) -> None:
        self.tasks = {t.id: t for t in tasks}
        self.window = window
        self.arms: dict[tuple[str, str], deque[Pair]] = {}
        self.seen: deque[str] = deque(maxlen=10_000)
        self.unscored = 0

    def score_run(self, run: RunResult) -> EvalResult | None:
        task = self.tasks.get(run.task_id or "")
        if task is None:
            self.unscored += 1
            return None
        actions = [{"tool": t.name, "arguments": t.arguments} for t in run.tool_calls]
        return score(task, run, apply_actions(task.initial_state, actions))

    def handle(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        run = RunResult.model_validate(payload)
        if run.run_id in self.seen:
            return None
        self.seen.append(run.run_id)
        ev = self.score_run(run)
        if ev is None:
            return None
        key = (run.track, run.spec_version)
        self.arms.setdefault(key, deque(maxlen=self.window)).append((run, ev))
        try:
            from adcp.runtime.telemetry import get_telemetry

            tel = get_telemetry()
            tel.record_eval(ev.score, tel.labels(run.spec_version, run.track, run.model_id))
        except Exception:  # telemetry is best-effort here
            log.debug("telemetry unavailable", exc_info=True)
        return {
            **ev.model_dump(mode="json"),
            "track": run.track,
            "spec_version": run.spec_version,
            "run": run.model_dump(mode="json"),
        }

    def snapshot(self) -> list[ArmMetrics]:
        return [
            arm_metrics(pairs, track, version)  # type: ignore[arg-type]
            for (track, version), pairs in sorted(self.arms.items())
        ]


async def serve(
    bus: EventBus,
    evaluator: OnlineEvaluator,
    from_start: bool = False,
    max_events: int | None = None,
    log_every: int = 50,
) -> None:
    n = 0
    sub: Any = bus.subscribe(TOPIC_RUNS, from_start=from_start)  # type: ignore[call-arg]
    async for msg in sub:
        out = evaluator.handle(msg)
        if out is not None:
            await bus.publish(TOPIC_EVALS, out)
        n += 1
        if n % log_every == 0 or n == max_events:
            for m in evaluator.snapshot():
                log.info("arm %s", m.model_dump_json())
        if max_events is not None and n >= max_events:
            break


async def selftest(window: int) -> list[ArmMetrics]:
    from adcp.runtime.events import InMemoryEventBus
    from adcp.runtime.harness import arun_batch
    from adcp.spec import AgentSpec

    tasks = load_tasks(default_tasks_path())
    bus = InMemoryEventBus()
    ev = OnlineEvaluator(tasks, window)
    pairs = await arun_batch(AgentSpec.from_yaml("specs/v1.yaml"), tasks)
    for run, _ in pairs:
        await bus.publish(TOPIC_RUNS, run)
    await serve(bus, ev, from_start=True, max_events=len(pairs))
    mismatch = sum(
        abs(e.score - (ev.score_run(r) or e).score) > 1e-9 for r, e in pairs
    )  # replay must equal harness scoring
    if mismatch:
        raise SystemExit(f"replay scoring disagrees with harness on {mismatch} runs")
    return ev.snapshot()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="online evaluator")
    ap.add_argument("--from-start", action="store_true")
    ap.add_argument("--window", type=int, default=200)
    ap.add_argument("--max-events", type=int, default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.selftest:
        for m in asyncio.run(selftest(args.window)):
            print(json.dumps(m.model_dump(mode="json")))
        return 0
    evaluator = OnlineEvaluator(load_tasks(default_tasks_path()), args.window)
    asyncio.run(serve(get_event_bus(), evaluator, args.from_start, args.max_events))
    return 0


if __name__ == "__main__":
    sys.exit(main())
