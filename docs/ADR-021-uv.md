# ADR-021: uv for Python packaging

Context: Need reproducible installs fast in CI.
Decision: uv with `uv.lock`; extras for providers, evals, deep, serve.
Alternative rejected: Poetry; pip-tools.
Why: Order-of-magnitude faster resolves, single lockfile.
Consequence: Contributors need uv installed.
