# ADR-002: MLflow Model Registry with aliases for stages

Context: Need a versioned store with lineage for AgentSpec, self-hostable, no SaaS.
Decision: MLflow Model Registry (Postgres backend, MinIO artifacts); aliases `candidate`/`canary`/`production`, `stage` tag incl. `rolled_back`.
Alternative rejected: MLflow legacy stages (deprecated since 2.9); a bare Git tag scheme.
Why: Aliases are the supported API and allow one version per role; MLflow already gives run lineage for eval results.
Consequence: `rolled_back` is a tag, not an alias, so history keeps every rejected version.
