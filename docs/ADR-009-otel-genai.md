# ADR-009: OpenTelemetry with GenAI semantic conventions

Context: Traces and metrics must be vendor-neutral and comparable across providers.
Decision: Runtime emits OTel spans/metrics using `gen_ai.*` attributes; OTel Collector fans out to Tempo, Prometheus, Loki and Langfuse.
Alternative rejected: Provider SDK callbacks; Langfuse SDK only.
Why: One instrumentation path feeds every backend; GenAI conventions give model-agnostic token and latency fields.
Consequence: Custom `adcp_*` metrics carry rollout labels the conventions lack.
