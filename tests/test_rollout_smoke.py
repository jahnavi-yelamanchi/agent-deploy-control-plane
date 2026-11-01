from adcp.rollback.github import DryRunIssueClient
from adcp.rollback.pareto import Decision
from adcp.rollout.sim import RolloutConfig, run_rollout
from adcp.spec import AgentSpec

V1 = AgentSpec.from_yaml("specs/v1.yaml")


def test_canary_rolls_back_v2(tmp_path):
    o = run_rollout(
        V1,
        AgentSpec.from_yaml("specs/v2-bad.yaml"),
        config=RolloutConfig(seed=1),
        expected=Decision.rollback,
        issues=DryRunIssueClient(tmp_path),
    )
    assert o.decision == Decision.rollback and o.final_weight == 0
    assert o.time_to_rollback_s == o.time_to_detect_s + 5.0
    assert o.false_rollback is False


def test_bluegreen_promotes_noop(tmp_path):
    o = run_rollout(
        V1, V1, config=RolloutConfig(mode="bluegreen", seed=2), issues=DryRunIssueClient(tmp_path)
    )
    assert o.decision == Decision.promote and o.final_weight == 100 and o.canary_requests == 0
