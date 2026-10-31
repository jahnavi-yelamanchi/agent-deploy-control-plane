"""Multi-objective (Pareto) rollback controller for canary rollouts."""

from adcp.rollback.pareto import (
    ArmSnapshot,
    Comparison,
    Decision,
    ParetoPolicy,
    Verdict,
    compare,
    naive_decision,
)

__all__ = [
    "ArmSnapshot",
    "Comparison",
    "Decision",
    "ParetoPolicy",
    "Verdict",
    "compare",
    "naive_decision",
]
