from adcp.registry import SpecRegistry
from adcp.spec import AgentSpec, Stage


def test_register_promote_rollback(tmp_path) -> None:
    reg = SpecRegistry(
        tracking_uri=f"sqlite:///{tmp_path}/mlflow.db", artifact_root=str(tmp_path / "a")
    )
    v1 = reg.register(AgentSpec.from_yaml("specs/v1.yaml"))
    reg.set_stage(v1, Stage.production)
    v2 = reg.register(AgentSpec.from_yaml("specs/v2-bad.yaml"))
    reg.set_stage(v2, Stage.canary)
    assert reg.get("canary").version == "v2" and reg.get(Stage.production).version == "v1"
    reg.set_stage(v2, Stage.rolled_back)
    hist = {h["version"]: h for h in reg.history()}
    assert hist[v2]["stage"] == "rolled_back" and "canary" not in hist[v2]["aliases"]
    assert "production" in hist[v1]["aliases"]
