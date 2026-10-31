"""CI eval gate: absolute thresholds from evals/gate.yaml on the frozen set.

    python -m adcp.evals.gate --spec specs/v1.yaml [--inspect] [--threshold 0.75] \
        [--summary $GITHUB_STEP_SUMMARY] [--out runs/gate.jsonl]

Exit 0 pass, 1 fail. --threshold overrides eval_score_min.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adcp.evals.metrics import append_rows, make_row
from adcp.spec import AgentSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_CONFIG = REPO_ROOT / "evals" / "gate.yaml"

# metric -> (threshold key, comparison)
CHECKS: tuple[tuple[str, str, str], ...] = (
    ("eval_score", "eval_score_min", ">="),
    ("task_success", "task_success_min", ">="),
    ("cost_per_task", "cost_per_task_max", "<="),
    ("p95_latency_ms", "p95_latency_ms_max", "<="),
)


@dataclass(frozen=True)
class Check:
    metric: str
    value: float
    op: str
    threshold: float

    @property
    def passed(self) -> bool:
        return self.value >= self.threshold if self.op == ">=" else self.value <= self.threshold


def load_thresholds(path: str | Path = GATE_CONFIG) -> dict[str, float]:
    return {k: float(v) for k, v in yaml.safe_load(Path(path).read_text())["thresholds"].items()}


def evaluate_gate(metrics: dict[str, Any], thresholds: dict[str, float]) -> list[Check]:
    return [
        Check(name, float(metrics[name]), op, thresholds[key])
        for name, key, op in CHECKS
        if key in thresholds and metrics.get(name) is not None
    ]


def render_summary(label: str, checks: list[Check], engine: str) -> str:
    ok = all(c.passed for c in checks)
    lines = [
        f"### Eval gate: `{label}` ({engine}) -> {'PASS' if ok else 'FAIL'}",
        "",
        "| metric | value | threshold | result |",
        "|---|---:|---:|---|",
    ]
    lines += [
        f"| {c.metric} | {c.value:.4g} | {c.op} {c.threshold:g} | {'pass' if c.passed else 'FAIL'} |"
        for c in checks
    ]
    return "\n".join(lines) + "\n\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="offline eval gate")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--inspect", action="store_true", help="run via Inspect AI instead of runner")
    ap.add_argument("--threshold", type=float, default=None, help="override eval_score_min")
    ap.add_argument("--config", default=str(GATE_CONFIG))
    ap.add_argument("--summary", default=None, help="markdown file to append (GITHUB_STEP_SUMMARY)")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--out", default=None, help="append a runs/ row (experiment=gate)")
    args = ap.parse_args(argv)

    spec = AgentSpec.from_yaml(args.spec)
    if args.inspect:
        from adcp.evals.inspect_runner import run_inspect

        metrics = run_inspect(args.spec, tasks_path=args.tasks)
        engine = "inspect-ai"
    else:
        from adcp.env.tasks import load_tasks
        from adcp.evals.runner import default_tasks_path, evaluate_spec

        _, metrics = evaluate_spec(spec, load_tasks(args.tasks or default_tasks_path()))
        engine = "runner"

    thresholds = load_thresholds(args.config)
    if args.threshold is not None:
        thresholds["eval_score_min"] = args.threshold
    checks = evaluate_gate(metrics, thresholds)
    passed = all(c.passed for c in checks)
    label = f"{spec.name}@{spec.version}"
    text = render_summary(label, checks, engine)
    print(text, end="")
    if args.summary:
        with Path(args.summary).open("a") as f:
            f.write(text)
    if args.out:
        append_rows(args.out, [make_row(metrics, experiment="gate", spec=spec)])
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
