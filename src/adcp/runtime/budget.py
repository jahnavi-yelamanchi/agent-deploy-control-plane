"""Budgets and rate limits. backend=services -> Redis counters + Lua token bucket;
backend=local -> in-process equivalents with the same interface."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from adcp.config import get_settings
from adcp.spec import Budget

_TOKEN_BUCKET_LUA = """
local key, rate, burst, now, cost = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local s = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(s[1]) or burst
local ts = tonumber(s[2]) or now
tokens = math.min(burst, tokens + (now - ts) * rate)
local ok = 0
if tokens >= cost then tokens = tokens - cost; ok = 1 end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(burst / rate) + 60)
return ok
"""


def check_run_budget(budget: Budget, cost_usd: float, latency_ms: float) -> str | None:
    """Reason string if the per-run budget is exceeded, else None."""
    if cost_usd > budget.max_cost_usd_per_run:
        return f"cost {cost_usd:.4f} > {budget.max_cost_usd_per_run}"
    if latency_ms > budget.max_latency_ms:
        return f"latency {latency_ms:.0f}ms > {budget.max_latency_ms:.0f}ms"
    return None


class BudgetStore:
    def __init__(self, backend: str | None = None, redis_url: str | None = None) -> None:
        s = get_settings()
        self.backend = backend or s.backend
        self._redis: Any = None
        if self.backend == "services":
            import redis

            self._redis = redis.Redis.from_url(redis_url or s.redis_url, decode_responses=True)
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._buckets: dict[str, tuple[float, float]] = {}

    @staticmethod
    def _day() -> str:
        return datetime.now(UTC).strftime("%Y%m%d")

    def add_spend(self, spec_version: str, track: str, cost_usd: float) -> float:
        """Daily spend counter per version/track. Returns the new total."""
        key = f"adcp:spend:{spec_version}:{track}:{self._day()}"
        if self._redis is not None:
            pipe = self._redis.pipeline()
            pipe.incrbyfloat(key, cost_usd)
            pipe.expire(key, 3 * 86400)
            return float(pipe.execute()[0])
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + cost_usd
            return self._counters[key]

    def spend(self, spec_version: str, track: str) -> float:
        key = f"adcp:spend:{spec_version}:{track}:{self._day()}"
        if self._redis is not None:
            return float(self._redis.get(key) or 0.0)
        return self._counters.get(key, 0.0)

    def allow(
        self, key: str, rate_per_s: float = 5.0, burst: float = 20.0, cost: float = 1.0
    ) -> bool:
        """Token-bucket rate limit."""
        now = time.time()
        if self._redis is not None:
            ok = self._redis.eval(
                _TOKEN_BUCKET_LUA, 1, f"adcp:rl:{key}", rate_per_s, burst, now, cost
            )
            return bool(ok)
        with self._lock:
            tokens, ts = self._buckets.get(key, (burst, now))
            tokens = min(burst, tokens + (now - ts) * rate_per_s)
            ok = tokens >= cost
            self._buckets[key] = (tokens - cost if ok else tokens, now)
            return ok
