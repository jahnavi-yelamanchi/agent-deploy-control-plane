"""RollbackController: read both arms -> Pareto decision -> act (Argo, registry, GitHub, events).

Actions per decision:
  rollback     Argo abort; registry stage rolled_back for the canary version.
  promote      final step: Argo promote --full + registry production. Earlier steps: Argo
               promote (clear pause) so the rollout advances to the next weight.
  needs_human  GitHub issue with the comparison table; rollout stays paused at its weight.
  hold         nothing.
Every non-hold decision is acted on at most once per (rollout revision, decision, step).
Each decision increments `adcp_rollback_events_total{decision,reason}` and is published to
`agent.rollbacks`.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from opentelemetry import metrics
from pydantic import BaseModel, Field

from adcp.rollback.argo import DryRunArgoClient, RolloutsClient
from adcp.rollback.github import DryRunIssueClient, IssueClient, issue_title
from adcp.rollback.metrics_source import MetricsSource
from adcp.rollback.pareto import RULES, Comparison, Decision, ParetoPolicy
from adcp.spec import Stage
from adcp.types import ArmMetrics

log = logging.getLogger(__name__)
TOPIC = "agent.rollbacks"
Rule = Callable[[ArmMetrics, ArmMetrics, ParetoPolicy], Comparison]

_meter = metrics.get_meter("adcp.rollback")
_events = _meter.create_counter(
    "adcp_rollback_events_total", unit="1", description="Rollback controller decisions"
)


class DecisionRecord(BaseModel):
    revision: str
    step: int | None = None
    final_step: bool = True
    comparison: Comparison
    actions: list[str] = Field(default_factory=list)
    issue: str | None = None
    duplicate: bool = False
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def decision(self) -> Decision:
        return self.comparison.decision

    def event(self) -> dict[str, Any]:
        c = self.comparison
        return {
            "decision": c.decision.value,
            "reason": c.reason,
            "rule": c.rule,
            "revision": self.revision,
            "step": self.step,
            "stable_version": c.stable.spec_version,
            "canary_version": c.canary.spec_version,
            "verdicts": {k: v.value for k, v in c.verdicts().items()},
            "slo_breaches": c.slo_breaches,
            "stable": c.stable.model_dump(),
            "canary": c.canary.model_dump(),
            "actions": self.actions,
            "issue": self.issue,
            "ts": self.ts.isoformat(),
        }


async def _maybe_await(x: Any) -> Any:
    return await x if inspect.isawaitable(x) else x


class RollbackController:
    def __init__(
        self,
        source: MetricsSource,
        argo: RolloutsClient | None = None,
        issues: IssueClient | None = None,
        policy: ParetoPolicy | None = None,
        registry: Any = None,
        bus: Any = None,
        rule: str | Rule = "pareto",
    ) -> None:
        self.source = source
        self.argo = argo or DryRunArgoClient()
        self.issues = issues or DryRunIssueClient()
        self.policy = policy or ParetoPolicy()
        self.registry = registry
        self.bus = bus
        self.rule: Rule = RULES[rule] if isinstance(rule, str) else rule
        self.canary_registry_version: int | None = None
        self._acted: dict[tuple[str, str, int | None], DecisionRecord] = {}
        self.history: list[DecisionRecord] = []

    async def decide(self) -> Comparison:
        stable, canary = await self.source.arms()
        return self.rule(stable, canary, self.policy)

    async def _revision(self) -> str:
        try:
            st = await self.argo.get_status()
            return str(st.get("revision") or "")
        except Exception as e:  # cluster unreachable -> still decide, key on versions
            log.warning("argo status failed: %s", e)
            return ""

    async def step(
        self,
        revision: str | None = None,
        step: int | None = None,
        final_step: bool = True,
        canary_registry_version: int | None = None,
    ) -> DecisionRecord:
        comp = await self.decide()
        rev = revision if revision is not None else await self._revision()
        rev = rev or f"{comp.stable.spec_version}->{comp.canary.spec_version}"
        if canary_registry_version is not None:
            self.canary_registry_version = canary_registry_version
        d = comp.decision
        key_step = step if d == Decision.promote and not final_step else None
        key = (rev, d.value, key_step)
        if d != Decision.hold and key in self._acted:
            prev = self._acted[key]
            rec = DecisionRecord(
                revision=rev,
                step=step,
                final_step=final_step,
                comparison=comp,
                issue=prev.issue,
                duplicate=True,
            )
            self.history.append(rec)
            return rec
        rec = DecisionRecord(revision=rev, step=step, final_step=final_step, comparison=comp)
        await self._act(rec)
        if d != Decision.hold:
            self._acted[key] = rec
        _events.add(1, {"decision": d.value, "reason": comp.reason.split(":")[0]})
        await self._publish(rec)
        self.history.append(rec)
        return rec

    async def _act(self, rec: DecisionRecord) -> None:
        comp = rec.comparison
        if comp.decision == Decision.rollback:
            await self.argo.abort(reason=comp.reason)
            rec.actions.append("argo:abort")
            if self._set_stage(Stage.rolled_back):
                rec.actions.append("registry:rolled_back")
        elif comp.decision == Decision.promote:
            await self.argo.promote(full=rec.final_step)
            rec.actions.append("argo:promote_full" if rec.final_step else "argo:promote_step")
            if rec.final_step and self._set_stage(Stage.production):
                rec.actions.append("registry:production")
        elif comp.decision == Decision.needs_human:
            body = (
                f"Rollout revision `{rec.revision}`, step {rec.step}. Canary paused; "
                "not dominated and not dominating. Promote or abort manually.\n\n"
                + comp.to_markdown()
            )
            rec.issue = await self.issues.create_issue(
                issue_title(comp.canary.spec_version), body, ["rollout", "needs-human"]
            )
            rec.actions.append("github:issue")

    def _set_stage(self, stage: Stage) -> bool:
        if self.registry is None:
            return False
        try:
            if self.canary_registry_version is None:
                self.canary_registry_version = int(self.registry.resolve(Stage.canary))
            self.registry.set_stage(self.canary_registry_version, stage)
            return True
        except Exception as e:
            log.warning("registry set_stage failed: %s", e)
            return False

    async def _publish(self, rec: DecisionRecord) -> None:
        if self.bus is None:
            return
        try:
            await _maybe_await(self.bus.publish(TOPIC, rec.event()))
        except Exception as e:
            log.warning("publish %s failed: %s", TOPIC, e)
