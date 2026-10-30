# ADR-003: Mock provider replays recorded fixtures

Context: No API keys in this environment or in public CI; the full pipeline must still run green.
Decision: Provider adapter interface with a `mock` adapter that replays per-model fixtures keyed by task and prompt variant; tokens computed from the real request, caching simulated by prefix hash.
Alternative rejected: Hard-coded canned responses; VCR-style HTTP cassettes.
Why: Fixtures exercise the real loop, context strategies and cache math; cassettes would tie fixtures to one SDK version per provider.
Consequence: Mock numbers are synthetic; `make bootstrap` + `make fixtures` re-records from real providers.
