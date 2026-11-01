"""deploy -> eval gate -> canary -> Pareto rollback / needs_human, all in-process with mocks."""

import json
from pathlib import Path

from adcp.demo import run_demo


def test_demo_loop(tmp_path):
    res = run_demo(tmp_path, quiet=True)
    assert res["decisions"] == {"v2-bad": "rollback", "v3-tradeoff": "needs_human"}
    stages = {h["spec_version"]: h["stage"] for h in res["registry"]}
    assert stages == {"v1": "production", "v2": "rolled_back", "v3": "canary"}
    assert [c for c, _ in res["argo_calls"]][0] == "abort"
    assert [e["decision"] for e in res["events"]][-1] == "needs_human"
    assert len(res["issues"]) == 1
    rows = [json.loads(x) for x in Path(res["demo_jsonl"]).read_text().splitlines()]
    assert rows and all(r["experiment"] == "demo" for r in rows)
    assert res["elapsed_s"] < 60
