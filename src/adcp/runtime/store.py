"""Run/eval persistence. backend=services -> Postgres (psycopg); local -> SQLite (file or :memory:)."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from adcp.config import get_settings
from adcp.types import EvalResult, RunResult

DDL = [
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL,
        spec_name TEXT NOT NULL,
        spec_version TEXT NOT NULL,
        spec_fingerprint TEXT NOT NULL,
        model_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        track TEXT NOT NULL,
        task_id TEXT,
        status TEXT NOT NULL,
        cost_usd DOUBLE PRECISION NOT NULL,
        latency_ms DOUBLE PRECISION NOT NULL,
        steps INTEGER NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cache_read_tokens INTEGER NOT NULL,
        cache_write_tokens INTEGER NOT NULL,
        tool_error_rate DOUBLE PRECISION NOT NULL,
        payload TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS runs_version_track ON runs (spec_version, track, started_at)",
    """CREATE TABLE IF NOT EXISTS eval_results (
        run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        spec_version TEXT NOT NULL,
        track TEXT NOT NULL,
        success BOOLEAN NOT NULL,
        score DOUBLE PRECISION NOT NULL,
        details TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS evals_version_track ON eval_results (spec_version, track)",
]
_RUN_COLS = (
    "run_id, started_at, spec_name, spec_version, spec_fingerprint, model_id, provider, track, "
    "task_id, status, cost_usd, latency_ms, steps, input_tokens, output_tokens, "
    "cache_read_tokens, cache_write_tokens, tool_error_rate, payload"
)


class RunStore:
    def __init__(self, dsn: str | None = None, backend: str | None = None) -> None:
        s = get_settings()
        self.backend = backend or s.backend
        self._lock = threading.Lock()
        if self.backend == "services":
            import psycopg

            self.conn: Any = psycopg.connect(dsn or s.postgres_dsn, autocommit=True)
            self.ph = "%s"
        else:
            self.conn = sqlite3.connect(dsn or ":memory:", check_same_thread=False)
            self.ph = "?"
        with self._lock:
            for stmt in DDL:
                self.conn.execute(stmt)
            if self.backend != "services":
                self.conn.commit()

    def _exec(self, sql: str, params: tuple[Any, ...]) -> Any:
        with self._lock:
            cur = self.conn.execute(sql.replace("?", self.ph), params)
            if self.backend != "services":
                self.conn.commit()
            return cur

    def save_run(self, r: RunResult) -> None:
        u = r.usage
        vals = (
            r.run_id, r.started_at.isoformat(), r.spec_name, r.spec_version, r.spec_fingerprint,
            r.model_id, r.provider, r.track, r.task_id, r.status, r.cost_usd, r.latency_ms, r.steps,
            u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens,
            r.tool_error_rate, r.model_dump_json(),
        )  # fmt: skip
        marks = ", ".join("?" * len(vals))
        self._exec(
            f"INSERT INTO runs ({_RUN_COLS}) VALUES ({marks}) ON CONFLICT (run_id) DO NOTHING", vals
        )

    def save_eval(self, ev: EvalResult, spec_version: str, track: str) -> None:
        self._exec(
            "INSERT INTO eval_results (run_id, task_id, spec_version, track, success, score, details)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id) DO NOTHING",
            (
                ev.run_id,
                ev.task_id,
                spec_version,
                track,
                ev.success,
                ev.score,
                json.dumps(ev.details),
            ),
        )

    def recent_runs(self, spec_version: str, track: str, limit: int = 200) -> list[RunResult]:
        cur = self._exec(
            "SELECT payload FROM runs WHERE spec_version = ? AND track = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (spec_version, track, limit),
        )
        return [RunResult.model_validate_json(row[0]) for row in cur.fetchall()]

    def eval_summary(self, spec_version: str, track: str) -> dict[str, float]:
        cur = self._exec(
            "SELECT COUNT(*), AVG(score), AVG(CASE WHEN success THEN 1.0 ELSE 0.0 END) "
            "FROM eval_results WHERE spec_version = ? AND track = ?",
            (spec_version, track),
        )
        n, score, succ = cur.fetchone()
        return {
            "n": float(n or 0),
            "eval_score": float(score or 0),
            "success_rate": float(succ or 0),
        }
