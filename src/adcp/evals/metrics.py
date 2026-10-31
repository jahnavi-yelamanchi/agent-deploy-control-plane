"""Aggregate per-run results into arm metrics and `runs/*.jsonl` rows.

Row schema (one JSON object per line in runs/*.jsonl; written by evals runner/matrix and by the
rollback agent for runs/demo.jsonl, runs/chaos.jsonl, runs/rollout.jsonl). Missing fields are null.

    experiment          str    offline|gate|models|caching|context|canary_weight|rollback_rule|...
    variant             str    row label (spec version, arm name, "cache-on", ...)
    spec_version        str
    model_id            str
    provider            str
    context_strategy    str    full|summarized|retrieved
    prompt_caching      bool
    canary_weight       float  percent of traffic on canary (rollout rows)
    n                   int    runs aggregated
    task_success        float  fraction of tasks with EvalResult.success
    eval_score          float  mean EvalResult.score (0..1)
    cost_per_task       float  mean RunResult.cost_usd (USD)
    p50_latency_ms      float
    p95_latency_ms      float
    tool_error_rate     float  failed tool calls / tool calls, pooled
    tokens_in           int    total input tokens (uncached + cache read + cache write)
    tokens_out          int    total output tokens
    cache_hit_rate      float  cache_read / tokens_in, pooled
    rollback_triggered  bool
    time_to_detect_s    float  fault/regression start -> rollback decision
    false_rollbacks     int
    ts                  str    ISO-8601 UTC
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adcp.spec import AgentSpec
from adcp.types import ArmMetrics, EvalResult, RunResult, Track

ROW_FIELDS: tuple[str, ...] = (
    "experiment",
    "variant",
    "spec_version",
    "model_id",
    "provider",
    "context_strategy",
    "prompt_caching",
    "canary_weight",
    "n",
    "task_success",
    "eval_score",
    "cost_per_task",
    "p50_latency_ms",
    "p95_latency_ms",
    "tool_error_rate",
    "tokens_in",
    "tokens_out",
    "cache_hit_rate",
    "rollback_triggered",
    "time_to_detect_s",
    "false_rollbacks",
    "ts",
)

Pair = tuple[RunResult, EvalResult]


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 100]."""
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * q / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def aggregate(pairs: Iterable[Pair]) -> dict[str, Any]:
    """Metrics dict over (RunResult, EvalResult) pairs. Empty input -> zeros with n=0."""
    pairs = list(pairs)
    n = len(pairs)
    runs = [r for r, _ in pairs]
    evals = [e for _, e in pairs]
    lat = [r.latency_ms for r in runs]
    calls = sum(len(r.tool_calls) for r in runs)
    failed = sum(sum(not t.ok for t in r.tool_calls) for r in runs)
    uncached = sum(r.usage.input_tokens for r in runs)
    cache_read = sum(r.usage.cache_read_tokens for r in runs)
    cache_write = sum(r.usage.cache_write_tokens for r in runs)
    tokens_in = uncached + cache_read + cache_write
    return {
        "n": n,
        "task_success": round(sum(e.success for e in evals) / n, 4) if n else 0.0,
        "eval_score": round(sum(e.score for e in evals) / n, 4) if n else 0.0,
        "cost_per_task": round(sum(r.cost_usd for r in runs) / n, 6) if n else 0.0,
        "p50_latency_ms": round(percentile(lat, 50), 1),
        "p95_latency_ms": round(percentile(lat, 95), 1),
        "tool_error_rate": round(failed / calls, 4) if calls else 0.0,
        "tokens_in": tokens_in,
        "tokens_out": sum(r.usage.output_tokens for r in runs),
        "cache_hit_rate": round(cache_read / tokens_in, 4) if tokens_in else 0.0,
    }


def arm_metrics(pairs: Iterable[Pair], track: Track, spec_version: str) -> ArmMetrics:
    m = aggregate(pairs)
    return ArmMetrics(
        track=track,
        spec_version=spec_version,
        n=m["n"],
        cost_per_task=m["cost_per_task"],
        p50_latency_ms=m["p50_latency_ms"],
        p95_latency_ms=m["p95_latency_ms"],
        eval_score=m["eval_score"],
        success_rate=m["task_success"],
        tool_error_rate=m["tool_error_rate"],
        cache_hit_rate=m["cache_hit_rate"],
    )


def make_row(
    metrics: dict[str, Any],
    *,
    experiment: str,
    variant: str | None = None,
    spec: AgentSpec | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a schema row; unknown keys in `extra` are rejected."""
    row: dict[str, Any] = dict.fromkeys(ROW_FIELDS)
    if spec is not None:
        row.update(
            spec_version=spec.version,
            model_id=spec.model_id,
            provider=str(spec.provider),
            context_strategy=str(spec.context_strategy),
            prompt_caching=spec.params.prompt_caching,
        )
    row.update({k: v for k, v in metrics.items() if k in row})
    bad = set(extra) - set(ROW_FIELDS)
    if bad:
        raise KeyError(f"unknown row fields: {sorted(bad)}")
    row.update(extra)
    row["experiment"] = experiment
    row["variant"] = variant or row["spec_version"]
    row["ts"] = row["ts"] or datetime.now(UTC).isoformat(timespec="seconds")
    return row


def append_rows(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=False) + "\n")


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read a jsonl file, normalizing every row to the schema (missing -> None)."""
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            raw = json.loads(line)
            rows.append({k: raw.get(k) for k in ROW_FIELDS})
    return rows
