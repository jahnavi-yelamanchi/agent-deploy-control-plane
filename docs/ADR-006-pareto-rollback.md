# ADR-006: Pareto rule for multi-objective rollback

Context: Canary decisions trade cost, p95 latency and eval score; independent thresholds roll back good trade-offs.
Decision: Roll back automatically only if the canary is Pareto-dominated by stable (worse or equal on all three within tolerance, strictly worse on one); promote if it dominates; otherwise mark needs-human and open a GitHub issue.
Alternative rejected: Per-metric static thresholds (naive rule).
Why: Dominance is scale-free and never auto-rejects a deliberate trade-off; the experiment counts false rollbacks of both rules.
Consequence: Hard SLO breaches still roll back immediately, outside the Pareto rule.
