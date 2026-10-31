"""OpenTelemetry tracing + metrics (GenAI semantic conventions + adcp_* custom metrics).

OTEL_EXPORTER_OTLP_ENDPOINT set -> OTLP/HTTP exporters (collector -> Tempo / Prometheus / Langfuse).
Unset -> spans are not exported; metrics go to an InMemoryMetricReader (tests, local runs).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from adcp.config import get_settings

# seconds; 8 s = latency SLO threshold
RUN_DURATION_BUCKETS = (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 60)
# 0.7 = quality SLO threshold
EVAL_SCORE_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
GENAI_DURATION_BUCKETS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48)
GENAI_TOKEN_BUCKETS = (1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576)


class Telemetry:
    def __init__(self) -> None:
        s = get_settings()
        resource = Resource.create({"service.name": s.service_name, "service.namespace": "adcp"})
        readers: list[MetricReader] = []
        self.reader: InMemoryMetricReader | None = None
        tp = TracerProvider(resource=resource)
        if s.otel_exporter_otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            ep = s.otel_exporter_otlp_endpoint.rstrip("/")
            tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{ep}/v1/traces")))
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{ep}/v1/metrics"), export_interval_millis=10000
                )
            )
        else:
            self.reader = InMemoryMetricReader()
            readers.append(self.reader)
        views = [
            View(
                instrument_name="adcp_run_duration_seconds",
                aggregation=ExplicitBucketHistogramAggregation(RUN_DURATION_BUCKETS),
            ),
            View(
                instrument_name="adcp_eval_score",
                aggregation=ExplicitBucketHistogramAggregation(EVAL_SCORE_BUCKETS),
            ),
            View(
                instrument_name="gen_ai.client.operation.duration",
                aggregation=ExplicitBucketHistogramAggregation(GENAI_DURATION_BUCKETS),
            ),
            View(
                instrument_name="gen_ai.client.token.usage",
                aggregation=ExplicitBucketHistogramAggregation(GENAI_TOKEN_BUCKETS),
            ),
        ]
        mp = MeterProvider(resource=resource, metric_readers=readers, views=views)
        self.tracer_provider, self.meter_provider = tp, mp
        self.tracer = tp.get_tracer("adcp.runtime")
        m = mp.get_meter("adcp.runtime")
        self.op_duration = m.create_histogram(
            "gen_ai.client.operation.duration",
            unit="s",
            description="GenAI operation duration",
            explicit_bucket_boundaries_advisory=list(GENAI_DURATION_BUCKETS),
        )
        self.token_usage = m.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="GenAI token usage",
            explicit_bucket_boundaries_advisory=list(GENAI_TOKEN_BUCKETS),
        )
        self.runs = m.create_counter("adcp_runs_total", description="Agent runs")
        self.run_cost = m.create_counter("adcp_run_cost_usd_total", description="Run cost USD")
        self.run_duration = m.create_histogram(
            "adcp_run_duration_seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=list(RUN_DURATION_BUCKETS),
        )
        self.eval_score = m.create_histogram(
            "adcp_eval_score", explicit_bucket_boundaries_advisory=list(EVAL_SCORE_BUCKETS)
        )
        self.tokens = m.create_counter("adcp_tokens_total", description="Tokens by type")
        self.tool_calls = m.create_counter("adcp_tool_calls_total", description="Tool calls")
        self.rollbacks = m.create_counter(
            "adcp_rollback_events_total", description="Rollback controller decisions"
        )
        self.canary_weight = m.create_gauge("adcp_canary_weight", description="Canary weight %")

    # spans
    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        with self.tracer.start_as_current_span(name) as sp:
            for k, v in attrs.items():
                if v is not None:
                    sp.set_attribute(k, v)
            try:
                yield sp
            except Exception as e:
                sp.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    # metrics
    @staticmethod
    def labels(agent_version: str, track: str, model: str, **extra: str) -> dict[str, str]:
        return {"agent_version": agent_version, "track": track, "model": model, **extra}

    def record_chat(
        self, provider: str, model: str, duration_s: float, usage: Any, base: dict[str, str]
    ) -> None:
        g = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": provider}
        g["gen_ai.request.model"] = model
        self.op_duration.record(duration_s, g)
        in_tok = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
        self.token_usage.record(in_tok, {**g, "gen_ai.token.type": "input"})
        self.token_usage.record(usage.output_tokens, {**g, "gen_ai.token.type": "output"})
        for typ, n in (
            ("input", usage.input_tokens),
            ("output", usage.output_tokens),
            ("cache_read", usage.cache_read_tokens),
            ("cache_write", usage.cache_write_tokens),
        ):
            if n:
                self.tokens.add(n, {**base, "type": typ})

    def record_tool(self, name: str, ok: bool, base: dict[str, str]) -> None:
        self.tool_calls.add(1, {**base, "tool": name, "status": "ok" if ok else "error"})

    def record_run(
        self, cost_usd: float, latency_ms: float, status: str, base: dict[str, str]
    ) -> None:
        lab = {**base, "status": status}
        self.runs.add(1, lab)
        self.run_cost.add(cost_usd, base)
        self.run_duration.record(latency_ms / 1000.0, lab)

    def record_eval(self, score: float, base: dict[str, str]) -> None:
        self.eval_score.record(score, base)

    def record_rollback(
        self, decision: str, reason: str, base: dict[str, str] | None = None
    ) -> None:
        self.rollbacks.add(1, {**(base or {}), "decision": decision, "reason": reason})

    def set_canary_weight(self, weight: float, base: dict[str, str] | None = None) -> None:
        self.canary_weight.set(weight, base or {})

    def metric_points(self) -> dict[str, list[Any]]:
        """Collected points by metric name (in-memory reader only)."""
        if self.reader is None:
            return {}
        out: dict[str, list[Any]] = {}
        data = self.reader.get_metrics_data()
        for rm in data.resource_metrics if data else []:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    out.setdefault(metric.name, []).extend(metric.data.data_points)
        return out


_tel: Telemetry | None = None
_lock = threading.Lock()


def get_telemetry() -> Telemetry:
    global _tel
    with _lock:
        if _tel is None:
            _tel = Telemetry()
            trace.set_tracer_provider(_tel.tracer_provider)
            metrics.set_meter_provider(_tel.meter_provider)
        return _tel
