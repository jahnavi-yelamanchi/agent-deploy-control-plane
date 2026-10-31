from fastapi.testclient import TestClient

from adcp.env import load_tasks
from adcp.runtime.flags import Flags
from adcp.runtime.harness import run_task
from adcp.spec import AgentSpec


def test_run_task_and_flags() -> None:
    spec = AgentSpec.from_yaml("specs/v1.yaml")
    task = next(t for t in load_tasks() if t.id == "airline-001")
    rr, ev = run_task(spec, task)
    assert rr.status == "ok" and rr.steps >= 2 and rr.cost_usd > 0 and 0 <= ev.score <= 1
    refund = next(t for t in load_tasks() if "new_tool" in t.tags)
    assert not run_task(spec, refund)[1].success  # issue_refund flag off on stable
    assert Flags("canary", 2).bool("tool-issue-refund")
    assert run_task(spec, refund, track="canary", rollout_step=2)[1].success


def test_app_run(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SPEC_PATH", "specs/v3-tradeoff.yaml")
    from adcp.runtime.app import app

    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/spec").json()["spec"]["version"] == "v3"
        r = c.post("/run", json={"input": "status?", "task_id": "retail-020"})
        assert r.status_code == 200 and r.json()["spec_version"] == "v3"
        r = c.post("/run", json={"input": "hello"}, headers={"X-ADCP-Track": "canary"})
        assert r.json()["track"] == "canary"
