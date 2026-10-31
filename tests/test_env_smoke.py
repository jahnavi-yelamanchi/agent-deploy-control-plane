from adcp.env import TauEnv, ToolError, load_tasks, score, state_hash
from adcp.env.scoring import expected_state_hash
from adcp.types import RunResult


def test_tasks_and_policy() -> None:
    tasks = load_tasks()
    assert len(tasks) == 50 and len({t.id for t in tasks}) == 50
    t = next(t for t in tasks if "cancel_reservation" in t.tags)
    env = TauEnv(t.initial_state)
    cancel = t.expected_actions[-1]
    try:
        env.call(cancel["tool"], cancel["arguments"])
        raise AssertionError("mutation without verification must fail")
    except ToolError as e:
        assert "PolicyViolation" in str(e)
    for a in t.expected_actions:
        env.call(a["tool"], a["arguments"])
    assert state_hash(env.state) == expected_state_hash(t)


def test_score_weights() -> None:
    t = next(t for t in load_tasks() if "cancel_order" in t.tags)
    rr = RunResult(
        spec_name="s", spec_version="v", spec_fingerprint="f", model_id="m", provider="mock",
        output=" ".join(t.expected_outputs),
    )  # fmt: skip
    assert score(t, rr, t.initial_state).score == 0.3  # state mismatch, outputs ok, no tool errors
    good = TauEnv(t.initial_state)
    for a in t.expected_actions:
        good.call(a["tool"], a["arguments"])
    ev = score(t, rr, good.state)
    assert ev.success and ev.score == 1.0
