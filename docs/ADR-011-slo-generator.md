# ADR-011: Hand-written Sloth-compatible SLO rule generator

Context: SLOs and burn-rate alerts must be generated from one file and checked in CI.
Decision: `infra/slo/slo.yaml` in Sloth format; `scripts/gen_slo_rules.py` emits recording rules and multiwindow multi-burn-rate alerts, `--check` fails on drift.
Alternative rejected: Sloth binary.
Why: No extra binary in CI; same spec stays Sloth-compatible if adopted later.
Consequence: Generator covers ratio SLIs only.
