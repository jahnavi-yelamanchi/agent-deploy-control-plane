import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from adcp.rollback.argo import ArgoRolloutsClient, DryRunArgoClient
from adcp.rollback.controller import RollbackController
from adcp.rollback.github import DryRunIssueClient, GitHubIssueClient
from adcp.rollback.metrics_source import InMemorySource, PrometheusSource
from adcp.rollback.pareto import ArmSnapshot, Decision, ParetoPolicy, compare, naive_decision


def arm(track, cost=0.004, p95=5000.0, score=0.9, n=100, **kw):
    return ArmSnapshot(
        track=track, spec_version=track, n=n, cost_per_task=cost, p50_latency_ms=p95 / 2,
        p95_latency_ms=p95, eval_score=score, **kw,
    )  # fmt: skip


def test_pareto_rules():
    s = arm("stable")
    assert compare(s, arm("canary", cost=0.008, score=0.8)).decision == Decision.rollback
    assert compare(s, arm("canary", cost=0.0041)).decision == Decision.promote  # within 3%
    assert compare(s, arm("canary", cost=0.002, p95=4000)).decision == Decision.promote
    trade = arm("canary", cost=0.001, p95=3000, score=0.85)
    assert compare(s, trade).decision == Decision.needs_human
    assert naive_decision(s, trade).decision == Decision.rollback
    assert compare(s, arm("canary", n=5)).decision == Decision.hold
    slo = compare(s, arm("canary", n=12, error_ratio=0.2))
    assert slo.decision == Decision.rollback and slo.reason == "slo_breach"
    # standard error widens the band: same 0.05 drop is noise when SE is large
    noisy = arm("canary", score=0.85, score_se=0.05)
    assert compare(s, noisy).decision == Decision.promote
    assert "| eval_score |" in compare(s, trade).to_markdown()


async def test_controller_acts_once(tmp_path):
    src = InMemorySource()

    async def arms():
        return arm("stable"), arm("canary", cost=0.01, score=0.7)

    src.arms = arms  # type: ignore[method-assign]
    argo = DryRunArgoClient(revision="7")
    published = []

    class Bus:
        async def publish(self, topic, payload):
            published.append((topic, payload))

    class Reg:
        calls: list = []

        def set_stage(self, v, s):
            self.calls.append((v, s))

    reg = Reg()
    c = RollbackController(src, argo, DryRunIssueClient(tmp_path), ParetoPolicy(), reg, Bus())
    r1 = await c.step(canary_registry_version=3)
    r2 = await c.step()
    assert r1.decision == Decision.rollback and r1.actions == ["argo:abort", "registry:rolled_back"]
    assert r2.duplicate and [x for x, _ in argo.calls] == ["abort"]
    assert reg.calls[0][0] == 3 and published[0][0] == "agent.rollbacks"
    assert published[0][1]["verdicts"]["eval_score"] == "worse"


async def test_needs_human_issue(tmp_path):
    src = InMemorySource()

    async def arms():
        return arm("stable"), arm("canary", cost=0.001, score=0.8)

    src.arms = arms  # type: ignore[method-assign]
    rec = await RollbackController(src, issues=DryRunIssueClient(tmp_path)).step()
    assert rec.decision == Decision.needs_human
    assert "needs human review" in Path(rec.issue).read_text()


async def test_prometheus_argo_github_http():
    def prom(req: httpx.Request) -> httpx.Response:
        q = req.url.params["query"]
        val = {"adcp:latency_p95:5m": "5.5", "adcp:cost_per_task:rate5m": "0.004"}.get(q, "0.9")
        if "increase" in q:
            val = "120"
        res = [{"metric": {"track": t, "agent_version": f"v-{t}"}, "value": [0, val]}
               for t in ("stable", "canary")]  # fmt: skip
        return httpx.Response(200, json={"status": "success", "data": {"result": res}})

    s, c = await PrometheusSource(transport=httpx.MockTransport(prom)).arms()
    assert c.p95_latency_ms == 5500 and c.n == 120 and c.spec_version == "v-canary"

    seen = []

    def k8s(req: httpx.Request) -> httpx.Response:
        seen.append(
            (req.method, req.url.path, req.headers["content-type"], json.loads(req.content))
        )
        return httpx.Response(
            200, json={"metadata": {"annotations": {}}, "status": {"abort": True}}
        )

    argo = ArgoRolloutsClient(token="t", transport=httpx.MockTransport(k8s))
    st = await argo.abort()
    assert st["aborted"]
    assert seen[0] == (
        "PATCH",
        "/apis/argoproj.io/v1alpha1/namespaces/agents/rollouts/agent-runtime/status",
        "application/merge-patch+json",
        {"status": {"abort": True}},
    )

    def gh(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert req.headers["authorization"] == "Bearer tok" and body["labels"] == [
            "rollout",
            "needs-human",
        ]
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/1"})

    url = await GitHubIssueClient("o/r", "tok", transport=httpx.MockTransport(gh)).create_issue(
        "t", "b"
    )
    assert url.endswith("/issues/1")


def test_service_decide():
    from adcp.rollback.service import app

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        body = {
            "stable": arm("stable").model_dump(),
            "canary": arm("canary", cost=0.01).model_dump(),
        }
        assert client.post("/decide", json=body).json()["decision"] == "rollback"
