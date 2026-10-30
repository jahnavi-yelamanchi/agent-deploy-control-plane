# ADR-010: Langfuse fed from the OTel pipeline

Context: Need an LLM trace UI without a second instrumentation SDK.
Decision: Self-hosted Langfuse v3 receives traces via its OTLP endpoint from the collector.
Alternative rejected: LangSmith (SaaS); Langfuse Python SDK.
Why: Self-hosted, no extra client code, same trace IDs as Tempo.
Consequence: Langfuse-specific features (prompt management, scores API) unused.
