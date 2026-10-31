"""Pareto rule for canary vs stable.

Objectives: cost_per_task (min), p95_latency_ms (min), eval_score (max).

Each objective gets a verdict for the canary relative to stable: `better`, `worse` or `equal`,
where `equal` means the difference is inside the noise band. Band = max(fixed tolerance,
z * combined standard error): relative for cost and latency, absolute for eval score. Standard
errors come from the in-memory source (mean: sd/sqrt(n); p95: order-statistic interval);
sources without them (Prometheus) fall back to the fixed tolerance.

Decision, in order:
1. rollback     hard SLO guard: canary error ratio, p95 latency or tool-error delta over the SLO.
                Applies once the canary has `slo_min_samples` runs (fewer than `min_samples`).
2. hold         either arm has fewer than `min_samples` runs.
3. rollback     canary is Pareto-dominated by stable: no objective better, at least one worse.
4. promote      canary dominates stable or is equal on every objective (no objective worse).
5. needs_human  trade-off: at least one objective better and at least one worse.

`naive_decision` is the baseline: roll back if any single objective is worse than stable beyond
a fixed threshold (no standard-error band). It rolls back deliberate trade-offs (cheaper but slightly worse) and
any noise excursion on one metric; `adcp.rollout.experiments` counts the difference.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from adcp.types import ArmMetrics


class Verdict(StrEnum):
    better = "better"
    worse = "worse"
    equal = "equal"


class Decision(StrEnum):
    rollback = "rollback"
    promote = "promote"
    needs_human = "needs_human"
    hold = "hold"


Objective = Literal["cost_per_task", "p95_latency_ms", "eval_score"]
OBJECTIVES: tuple[Objective, ...] = ("cost_per_task", "p95_latency_ms", "eval_score")
_MINIMIZE = {"cost_per_task": True, "p95_latency_ms": True, "eval_score": False}


class ArmSnapshot(ArmMetrics):
    """ArmMetrics plus run-level error ratio (status=error / all runs) and token totals."""

    error_ratio: float = 0.0
    tokens_in: int = 0  # uncached + cache read + cache write
    tokens_out: int = 0
    # standard errors of cost_per_task, p95_latency_ms, eval_score; 0 = unknown
    cost_se: float = 0.0
    p95_se: float = 0.0
    score_se: float = 0.0


class ParetoPolicy(BaseModel):
    cost_rel_tol: float = 0.03
    latency_rel_tol: float = 0.05
    score_abs_tol: float = 0.02
    z: float = 3.0  # noise band width in standard errors
    min_samples: int = 20
    # hard SLO guard (canary only); None disables a check
    slo_min_samples: int = 10
    slo_max_error_ratio: float | None = 0.05
    slo_max_p95_latency_ms: float | None = 15000.0
    slo_max_tool_error_delta: float | None = 0.15  # canary - stable tool error rate

    def tolerance(self, objective: Objective) -> float:
        return {
            "cost_per_task": self.cost_rel_tol,
            "p95_latency_ms": self.latency_rel_tol,
            "eval_score": self.score_abs_tol,
        }[objective]


class ObjectiveResult(BaseModel):
    objective: Objective
    stable: float
    canary: float
    delta: float  # relative (cost, latency) or absolute (score); canary - stable
    tolerance: float
    verdict: Verdict


class Comparison(BaseModel):
    decision: Decision
    reason: str
    rule: str = "pareto"
    objectives: list[ObjectiveResult] = Field(default_factory=list)
    slo_breaches: list[str] = Field(default_factory=list)
    stable: ArmSnapshot
    canary: ArmSnapshot

    def verdicts(self) -> dict[str, Verdict]:
        return {o.objective: o.verdict for o in self.objectives}

    def to_markdown(self) -> str:
        s, c = self.stable, self.canary
        lines = [
            f"**Decision:** `{self.decision}` ({self.rule}) - {self.reason}",
            "",
            f"| objective | stable {s.spec_version} | canary {c.spec_version} | delta | tol | verdict |",
            "|---|---|---|---|---|---|",
        ]
        for o in self.objectives:
            rel = o.objective != "eval_score"
            delta = f"{o.delta:+.1%}" if rel else f"{o.delta:+.3f}"
            tol = f"{o.tolerance:.0%}" if rel else f"{o.tolerance:.3f}"
            lines.append(
                f"| {o.objective} | {o.stable:.4g} | {o.canary:.4g} | {delta} | {tol} | {o.verdict} |"
            )
        lines += [
            f"| n | {s.n} | {c.n} | | | |",
            f"| error_ratio | {s.error_ratio:.3f} | {c.error_ratio:.3f} | | | |",
            f"| tool_error_rate | {s.tool_error_rate:.3f} | {c.tool_error_rate:.3f} | | | |",
        ]
        if self.slo_breaches:
            lines += ["", "SLO breaches: " + "; ".join(self.slo_breaches)]
        return "\n".join(lines)


def snapshot(m: ArmMetrics) -> ArmSnapshot:
    return m if isinstance(m, ArmSnapshot) else ArmSnapshot(**m.model_dump())


_SE = {"cost_per_task": "cost_se", "p95_latency_ms": "p95_se", "eval_score": "score_se"}


def objective_verdict(
    objective: Objective, stable: float, canary: float, tol: float, noise: float = 0.0
) -> ObjectiveResult:
    """Canary vs stable on one objective; |delta| <= max(tol, noise) is `equal`.

    `noise` is z * combined standard error in the objective's units.
    """
    if objective == "eval_score":
        delta = canary - stable
        gain = delta  # maximize
        tol = max(tol, noise)
    else:
        delta = (canary - stable) / stable if stable else (0.0 if canary == stable else 1.0)
        gain = -delta  # minimize
        tol = max(tol, noise / stable if stable else 0.0)
    v = Verdict.equal if abs(delta) <= tol else Verdict.better if gain > 0 else Verdict.worse
    return ObjectiveResult(
        objective=objective, stable=stable, canary=canary, delta=delta, tolerance=tol, verdict=v
    )


def objective_results(
    stable: ArmMetrics, canary: ArmMetrics, policy: ParetoPolicy, use_se: bool = True
) -> list[ObjectiveResult]:
    s, c = snapshot(stable), snapshot(canary)
    out = []
    for o in OBJECTIVES:
        se = (getattr(s, _SE[o]) ** 2 + getattr(c, _SE[o]) ** 2) ** 0.5 if use_se else 0.0
        out.append(
            objective_verdict(o, getattr(s, o), getattr(c, o), policy.tolerance(o), policy.z * se)
        )
    return out


def slo_breaches(stable: ArmSnapshot, canary: ArmSnapshot, policy: ParetoPolicy) -> list[str]:
    out: list[str] = []
    if canary.n < policy.slo_min_samples:
        return out
    if policy.slo_max_error_ratio is not None and canary.error_ratio > policy.slo_max_error_ratio:
        out.append(f"error_ratio {canary.error_ratio:.3f} > {policy.slo_max_error_ratio}")
    if (
        policy.slo_max_p95_latency_ms is not None
        and canary.p95_latency_ms > policy.slo_max_p95_latency_ms
    ):
        out.append(f"p95 {canary.p95_latency_ms:.0f}ms > {policy.slo_max_p95_latency_ms:.0f}ms")
    if policy.slo_max_tool_error_delta is not None:
        d = canary.tool_error_rate - stable.tool_error_rate
        if d > policy.slo_max_tool_error_delta:
            out.append(f"tool_error_rate +{d:.3f} > +{policy.slo_max_tool_error_delta}")
    return out


def _insufficient(s: ArmSnapshot, c: ArmSnapshot, policy: ParetoPolicy) -> bool:
    return min(s.n, c.n) < policy.min_samples


def compare(
    stable: ArmMetrics, canary: ArmMetrics, policy: ParetoPolicy | None = None
) -> Comparison:
    """Pareto decision for canary vs stable. See module docstring for the rule order."""
    policy = policy or ParetoPolicy()
    s, c = snapshot(stable), snapshot(canary)
    objs = objective_results(s, c, policy)
    breaches = slo_breaches(s, c, policy)
    base: dict[str, Any] = {"stable": s, "canary": c, "objectives": objs, "slo_breaches": breaches}
    if breaches:
        return Comparison(decision=Decision.rollback, reason="slo_breach", **base)
    if _insufficient(s, c, policy):
        return Comparison(decision=Decision.hold, reason="insufficient_samples", **base)
    worse = [o.objective for o in objs if o.verdict == Verdict.worse]
    better = [o.objective for o in objs if o.verdict == Verdict.better]
    if worse and not better:
        return Comparison(decision=Decision.rollback, reason="dominated:" + ",".join(worse), **base)
    if not worse:
        reason = "dominates:" + ",".join(better) if better else "equal"
        return Comparison(decision=Decision.promote, reason=reason, **base)
    return Comparison(
        decision=Decision.needs_human,
        reason=f"tradeoff:better={','.join(better)};worse={','.join(worse)}",
        **base,
    )


def naive_decision(
    stable: ArmMetrics,
    canary: ArmMetrics,
    policy: ParetoPolicy | None = None,
    use_se: bool = False,
) -> Comparison:
    """Baseline threshold rule: rollback if any objective is worse beyond its threshold.

    Fixed thresholds (the policy tolerances, no standard-error band), as in a static
    AnalysisTemplate. `use_se=True` adds the same noise band as `compare`, isolating the
    effect of the decision rule. Same sample minimum and SLO guard as `compare`.
    """
    policy = policy or ParetoPolicy()
    s, c = snapshot(stable), snapshot(canary)
    objs = objective_results(s, c, policy, use_se=use_se)
    breaches = slo_breaches(s, c, policy)
    base: dict[str, Any] = {
        "stable": s,
        "canary": c,
        "objectives": objs,
        "slo_breaches": breaches,
        "rule": "naive_se" if use_se else "naive",
    }
    if breaches:
        return Comparison(decision=Decision.rollback, reason="slo_breach", **base)
    if _insufficient(s, c, policy):
        return Comparison(decision=Decision.hold, reason="insufficient_samples", **base)
    worse = [o.objective for o in objs if o.verdict == Verdict.worse]
    if worse:
        return Comparison(decision=Decision.rollback, reason="threshold:" + ",".join(worse), **base)
    return Comparison(decision=Decision.promote, reason="within_thresholds", **base)


def naive_se_decision(
    stable: ArmMetrics, canary: ArmMetrics, policy: ParetoPolicy | None = None
) -> Comparison:
    return naive_decision(stable, canary, policy, use_se=True)


RULES = {"pareto": compare, "naive": naive_decision, "naive_se": naive_se_decision}
