"""Full loop in-process with mocks: register -> gate -> canary -> Pareto decision -> act.

    python -m adcp.demo [--out-dir .]      (make demo-local)

1. v1 -> MLflow registry (fresh sqlite under <out-dir>/.mlflow/demo) -> gate -> production.
2. v2-bad -> candidate -> gate (absolute thresholds, passes) -> canary -> simulated rollout
   -> Pareto: dominated -> Argo abort (dry run) + registry rolled_back.
3. v3-tradeoff -> candidate -> gate -> canary -> Pareto: trade-off -> needs_human ->
   GitHub issue (dry run, <out-dir>/runs/issues/*.md); rollout paused.
Writes <out-dir>/runs/demo.jsonl (shared runs schema, experiment=demo).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("MODEL_PROVIDER", "mock")

from adcp.rollback.argo import DryRunArgoClient  # noqa: E402
from adcp.rollback.controller import RollbackController  # noqa: E402
from adcp.rollback.github import DryRunIssueClient  # noqa: E402
from adcp.rollback.metrics_source import InMemorySource  # noqa: E402
from adcp.rollback.pareto import Decision, ParetoPolicy  # noqa: E402
from adcp.rollout.rows import append_rows, arm_row  # noqa: E402
from adcp.rollout.sim import RolloutConfig, RolloutOutcome, arun_rollout  # noqa: E402
from adcp.spec import AgentSpec, Stage  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def gate(spec: AgentSpec) -> tuple[bool, dict[str, Any]]:
    """Offline eval gate (adcp.evals.gate thresholds) on the frozen set."""
    from adcp.evals.gate import evaluate_gate, load_thresholds
    from adcp.evals.runner import evaluate_spec

    _, m = evaluate_spec(spec)
    checks = evaluate_gate(m, load_thresholds())
    return all(c.passed for c in checks), m


def _fmt_ttd(x: float | None) -> str:
    return f"{x:.0f}s" if x is not None else "-"


def run_demo(out_dir: Path | str = ".", seed: int = 7, quiet: bool = False) -> dict[str, Any]:
    from adcp.registry import SpecRegistry

    t_start = time.perf_counter()
    out = Path(out_dir).resolve()
    say = (lambda *_a: None) if quiet else print
    mlflow_dir = out / ".mlflow" / "demo"
    shutil.rmtree(mlflow_dir, ignore_errors=True)
    mlflow_dir.mkdir(parents=True)
    runs_dir = out / "runs"
    demo_jsonl = runs_dir / "demo.jsonl"
    demo_jsonl.unlink(missing_ok=True)
    reg = SpecRegistry(
        tracking_uri=f"sqlite:///{mlflow_dir / 'mlflow.db'}",
        model_name="support-agent",
        artifact_root=str(mlflow_dir / "artifacts"),
    )
    argo = DryRunArgoClient()
    issues = DryRunIssueClient(runs_dir / "issues")
    from adcp.runtime.events import InMemoryEventBus

    bus = InMemoryEventBus()
    controller = RollbackController(
        InMemorySource(), argo=argo, issues=issues, policy=ParetoPolicy(), registry=reg, bus=bus
    )
    specs = {
        k: AgentSpec.from_yaml(ROOT / "specs" / f"{k}.yaml")
        for k in ("v1", "v2-bad", "v3-tradeoff")
    }
    transitions: list[tuple[str, int, str]] = []
    rows: list[dict[str, Any]] = []

    def stage(v: int, label: str, s: Stage) -> None:
        reg.set_stage(v, s)
        transitions.append((label, v, s.value))

    say("== 1. baseline v1")
    v1 = reg.register(specs["v1"])
    transitions.append(("v1", v1, Stage.candidate.value))
    ok, m = gate(specs["v1"])
    say(f"   gate v1: {'PASS' if ok else 'FAIL'}  eval_score={m['eval_score']:.3f}")
    rows.append(arm_row("demo", "gate:v1", None, spec=specs["v1"], **_gate_fields(m)))
    stage(v1, "v1", Stage.production)

    outcomes: dict[str, RolloutOutcome] = {}
    for label, expected in (("v2-bad", Decision.rollback), ("v3-tradeoff", Decision.needs_human)):
        say(f"== {'2' if label == 'v2-bad' else '3'}. candidate {label}")
        spec = specs[label]
        v = reg.register(spec)
        transitions.append((label, v, Stage.candidate.value))
        ok, m = gate(spec)
        say(f"   gate {label}: {'PASS' if ok else 'FAIL'}  eval_score={m['eval_score']:.3f}")
        rows.append(arm_row("demo", f"gate:{label}", None, spec=spec, **_gate_fields(m)))
        if not ok:
            say("   gate failed; stopping this candidate")
            continue
        stage(v, label, Stage.canary)
        o = asyncio.run(
            arun_rollout(
                specs["v1"],
                spec,
                config=RolloutConfig(seed=seed),
                controller=controller,
                expected=expected,
                canary_registry_version=v,
            )
        )
        outcomes[label] = o
        hist = {h["version"]: h["stage"] for h in reg.history()}
        paused = " (paused: needs_human)" if o.decision == Decision.needs_human else ""
        transitions.append((label, v, f"{hist.get(v)}{paused}"))
        say(
            f"   rollout: {o.decision.value} ({o.comparison.reason if o.comparison else ''}) at "
            f"weight {o.events[-1].weight if o.events else 0}%, detect {_fmt_ttd(o.time_to_detect_s)}, "
            f"rollback {_fmt_ttd(o.time_to_rollback_s)}"
        )
        if o.issue:
            say(f"   issue: {o.issue}")
        for arm, a in (("stable", o.stable), ("canary", o.canary)):
            rows.append(
                arm_row(
                    "demo",
                    f"{label}:{arm}",
                    a,
                    spec=specs["v1"] if arm == "stable" else spec,
                    canary_weight=float(o.events[-1].weight if o.events else 0),
                    rollback_triggered=o.rollback_triggered,
                    time_to_detect_s=o.time_to_detect_s,
                    false_rollbacks=int(bool(o.false_rollback)),
                )
            )

    append_rows(demo_jsonl, rows)
    elapsed = time.perf_counter() - t_start
    if not quiet:
        _print_tables(transitions, outcomes, argo, reg)
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        print(
            f"OTel: spans/metrics exported to {endpoint}"
            if endpoint
            else "OTel: OTEL_EXPORTER_OTLP_ENDPOINT unset; spans and metrics not exported."
        )
        print(f"wrote {demo_jsonl} ({len(rows)} rows) in {elapsed:.1f}s")
    return {
        "decisions": {k: o.decision.value for k, o in outcomes.items()},
        "outcomes": outcomes,
        "registry": reg.history(),
        "argo_calls": argo.calls,
        "events": bus.history.get("agent.rollbacks", []),
        "issues": [str(p) for p in issues.created],
        "demo_jsonl": str(demo_jsonl),
        "elapsed_s": elapsed,
    }


def _gate_fields(m: dict[str, Any]) -> dict[str, Any]:
    keys = ("n", "task_success", "eval_score", "cost_per_task", "p50_latency_ms", "p95_latency_ms")
    return {k: m[k] for k in keys if k in m}


def _print_tables(
    transitions: list[tuple[str, int, str]],
    outcomes: dict[str, RolloutOutcome],
    argo: DryRunArgoClient,
    reg: Any,
) -> None:
    print("\nStage transitions (MLflow registry)")
    print(f"  {'spec':<12} {'ver':>3}  stage")
    for label, v, s in transitions:
        print(f"  {label:<12} {v:>3}  {s}")
    print("\nPer-arm metrics at decision")
    hdr = f"  {'rollout':<12} {'arm':<7} {'n':>4} {'cost/task':>10} {'p95 ms':>8} {'score':>6} {'tool err':>8}"
    print(hdr)
    for label, o in outcomes.items():
        for arm, a in (("stable", o.stable), ("canary", o.canary)):
            print(
                f"  {label:<12} {arm:<7} {a.n:>4} {a.cost_per_task:>10.5f} "
                f"{a.p95_latency_ms:>8.0f} {a.eval_score:>6.3f} {a.tool_error_rate:>8.3f}"
            )
    print("\nDecisions")
    print(
        f"  {'rollout':<12} {'decision':<12} {'verdicts (cost/p95/score)':<26} {'TTD':>6} {'TTR':>6}"
    )
    for label, o in outcomes.items():
        verd = o.comparison.verdicts() if o.comparison else {}
        vs = "/".join(
            str(verd.get(k, "-")) for k in ("cost_per_task", "p95_latency_ms", "eval_score")
        )
        print(
            f"  {label:<12} {o.decision.value:<12} {vs:<26} "
            f"{_fmt_ttd(o.time_to_detect_s):>6} {_fmt_ttd(o.time_to_rollback_s):>6}"
        )
    print(f"\nArgo (dry run) calls: {[c for c, _ in argo.calls]}")
    print("Registry: " + ", ".join(f"v{h['version']}={h['stage']}" for h in reg.history()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="in-process deploy -> eval -> canary -> rollback demo")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    res = run_demo(args.out_dir, args.seed)
    ok = res["decisions"] == {"v2-bad": "rollback", "v3-tradeoff": "needs_human"}
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
