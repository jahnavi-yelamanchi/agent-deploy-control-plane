"""Per-arm metric sources for the controller: Prometheus recording rules or in-memory results."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from typing import Protocol

import httpx

from adcp.rollback.pareto import ArmSnapshot
from adcp.types import EvalResult, RunResult, Track

TRACKS: tuple[Track, Track] = ("stable", "canary")


class MetricsSource(Protocol):
    async def arms(self) -> tuple[ArmSnapshot, ArmSnapshot]:
        """Return (stable, canary)."""
        ...


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 100]."""
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _se_mean(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n)


def _se_quantile(xs: list[float], q: float) -> float:
    """Distribution-free SE of the q-quantile: half-width of the +/-1 sd order-statistic band."""
    n = len(xs)
    if n < 2:
        return 0.0
    s = sorted(xs)
    p = q / 100
    h = math.sqrt(n * p * (1 - p))
    lo = max(0, math.floor(n * p - h) - 1)
    hi = min(n - 1, math.ceil(n * p + h) - 1)
    return (s[hi] - s[lo]) / 2


def aggregate(
    track: Track, pairs: Iterable[tuple[RunResult, EvalResult]], spec_version: str = ""
) -> ArmSnapshot:
    rows = list(pairs)
    n = len(rows)
    if not n:
        return ArmSnapshot(
            track=track,
            spec_version=spec_version,
            n=0,
            cost_per_task=0.0,
            p50_latency_ms=0.0,
            p95_latency_ms=0.0,
            eval_score=0.0,
        )
    lat = [r.latency_ms for r, _ in rows]
    tokens_prompt = sum(
        r.usage.input_tokens + r.usage.cache_read_tokens + r.usage.cache_write_tokens
        for r, _ in rows
    )
    calls = sum(len(r.tool_calls) for r, _ in rows)
    bad_calls = sum(sum(not t.ok for t in r.tool_calls) for r, _ in rows)
    return ArmSnapshot(
        track=track,
        spec_version=spec_version or rows[-1][0].spec_version,
        n=n,
        cost_per_task=sum(r.cost_usd for r, _ in rows) / n,
        p50_latency_ms=percentile(lat, 50),
        p95_latency_ms=percentile(lat, 95),
        eval_score=sum(e.score for _, e in rows) / n,
        success_rate=sum(e.success for _, e in rows) / n,
        tool_error_rate=bad_calls / calls if calls else 0.0,
        cache_hit_rate=sum(r.usage.cache_read_tokens for r, _ in rows) / tokens_prompt
        if tokens_prompt
        else 0.0,
        error_ratio=sum(r.status == "error" for r, _ in rows) / n,
        tokens_in=tokens_prompt,
        tokens_out=sum(r.usage.output_tokens for r, _ in rows),
        cost_se=_se_mean([r.cost_usd for r, _ in rows]),
        p95_se=_se_quantile(lat, 95),
        score_se=_se_mean([e.score for _, e in rows]),
    )


class InMemorySource:
    """Accumulates (RunResult, EvalResult) per track. `window` keeps only the last N per arm."""

    def __init__(self, window: int | None = None) -> None:
        self.window = window
        self._rows: dict[str, deque[tuple[RunResult, EvalResult]]] = {
            t: deque(maxlen=window) for t in TRACKS
        }

    def record(self, run: RunResult, ev: EvalResult, track: Track | None = None) -> None:
        self._rows[track or run.track].append((run, ev))

    def extend(self, pairs: Iterable[tuple[RunResult, EvalResult]]) -> None:
        for r, e in pairs:
            self.record(r, e)

    def on_event(self, topic: str, payload: dict) -> None:
        """Feed from the event bus: `agent.evals` payloads carrying `run` + eval fields."""
        if topic == "agent.evals" and "run" in payload:
            run = RunResult.model_validate(payload["run"])
            self.record(run, EvalResult.model_validate(payload), payload.get("track"))

    def reset(self) -> None:
        for d in self._rows.values():
            d.clear()

    def count(self, track: Track) -> int:
        return len(self._rows[track])

    def snapshot(self, track: Track) -> ArmSnapshot:
        return aggregate(track, self._rows[track])

    async def arms(self) -> tuple[ArmSnapshot, ArmSnapshot]:
        return self.snapshot("stable"), self.snapshot("canary")


class PrometheusSource:
    """Reads the `adcp:*` recording rules (observability/prometheus/rules/recording.yml)."""

    QUERIES = {
        "cost_per_task": "adcp:cost_per_task:rate5m",
        "p50_latency_ms": "adcp:latency_p50:5m",
        "p95_latency_ms": "adcp:latency_p95:5m",
        "eval_score": "adcp:eval_score:mean5m",
        "error_ratio": "adcp:error_ratio:rate5m",
        "tool_error_rate": "adcp:tool_error_ratio:rate5m",
        "cache_hit_rate": "adcp:cache_hit_ratio:rate5m",
        "n": "sum by (track) (increase(adcp_runs_total[{window}]))",
        "spec_version": "count by (track, agent_version) (adcp_runs_total)",
    }
    SECONDS_TO_MS = {"p50_latency_ms", "p95_latency_ms"}

    def __init__(
        self,
        url: str = "http://localhost:9090",
        window: str = "5m",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.window = window
        self._transport = transport
        self._timeout = timeout_s

    async def _query(self, client: httpx.AsyncClient, expr: str) -> list[dict]:
        r = await client.get(f"{self.url}/api/v1/query", params={"query": expr})
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus query failed: {expr}: {body}")
        return body["data"]["result"]  # type: ignore[no-any-return]

    async def arms(self) -> tuple[ArmSnapshot, ArmSnapshot]:
        vals: dict[str, dict[str, float | str]] = {t: {} for t in TRACKS}
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            for field, q in self.QUERIES.items():
                for series in await self._query(client, q.format(window=self.window)):
                    track = series["metric"].get("track")
                    if track not in vals:
                        continue
                    if field == "spec_version":
                        vals[track][field] = series["metric"].get("agent_version", "")
                        continue
                    v = float(series["value"][1])
                    if math.isnan(v):
                        continue
                    vals[track][field] = v * 1000 if field in self.SECONDS_TO_MS else v
        out = []
        for t in TRACKS:
            d = vals[t]
            out.append(
                ArmSnapshot(
                    track=t,
                    spec_version=str(d.get("spec_version", "")),
                    n=round(float(d.get("n", 0))),
                    cost_per_task=float(d.get("cost_per_task", 0.0)),
                    p50_latency_ms=float(d.get("p50_latency_ms", 0.0)),
                    p95_latency_ms=float(d.get("p95_latency_ms", 0.0)),
                    eval_score=float(d.get("eval_score", 0.0)),
                    tool_error_rate=float(d.get("tool_error_rate", 0.0)),
                    cache_hit_rate=float(d.get("cache_hit_rate", 0.0)),
                    error_ratio=float(d.get("error_ratio", 0.0)),
                )
            )
        return out[0], out[1]


class PairedSource(InMemorySource):
    """Mirrored traffic (blue-green preview): the i-th stable and canary runs share a task.

    Cost and score standard errors come from per-request differences (paired), which removes
    task-mix variance; the combined SE is put on the canary arm and stable SE set to 0.
    """

    async def arms(self) -> tuple[ArmSnapshot, ArmSnapshot]:
        s, c = self.snapshot("stable"), self.snapshot("canary")
        pairs = list(zip(self._rows["stable"], self._rows["canary"], strict=False))
        if len(pairs) < 2:
            return s, c
        dc = [cr.cost_usd - sr.cost_usd for (sr, _), (cr, _) in pairs]
        ds = [ce.score - se.score for (_, se), (_, ce) in pairs]
        s = s.model_copy(update={"cost_se": 0.0, "score_se": 0.0})
        c = c.model_copy(update={"cost_se": _se_mean(dc), "score_se": _se_mean(ds)})
        return s, c
