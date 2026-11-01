"""Rollout experiments -> runs/rollout.jsonl.

    python -m adcp.rollout.experiments [--out runs/rollout.jsonl] [--seeds 5]

canary_weight   v1 -> v2-bad with first step 10% vs 25%: time to detect, canary requests
                served before the decision (blast radius, reported in `n`).
rollback_rule   Pareto vs per-metric thresholds over candidates with known ground truth.
                naive = fixed thresholds; naive_se = thresholds + the same noise band as Pareto.
                Candidates:
                v2-bad (rollback), v3-tradeoff (needs_human), v1-copy (no-op version bump,
                promote), v1 vs v1 noise reruns. Counts false rollbacks per rule.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from pathlib import Path
from typing import Any

from adcp.rollback.pareto import Decision, ParetoPolicy
from adcp.rollout.rows import append_rows, arm_row
from adcp.rollout.sim import RolloutConfig, RolloutOutcome, arun_rollout
from adcp.spec import AgentSpec

SPECS = Path(__file__).resolve().parents[3] / "specs"


def load_specs(specs_dir: Path = SPECS) -> dict[str, AgentSpec]:
    return {
        k: AgentSpec.from_yaml(specs_dir / f"{k}.yaml") for k in ("v1", "v2-bad", "v3-tradeoff")
    }


def v1_copy(v1: AgentSpec) -> AgentSpec:
    """Version bump with a label change only: behaviourally identical to v1."""
    return v1.model_copy(
        update={"version": f"{v1.version}+copy", "labels": {**v1.labels, "copy": "true"}}
    )


def _mean(xs: list[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(statistics.fmean(vals), 1) if vals else None


async def canary_weight(
    specs: dict[str, AgentSpec], seeds: int, policy: ParetoPolicy | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    rows, lines = [], []
    for first in (10, 25):
        steps = [first] + [w for w in (25, 50, 100) if w > first]
        outs = [
            await arun_rollout(
                specs["v1"],
                specs["v2-bad"],
                config=RolloutConfig(steps=steps, seed=s),
                policy=policy,
                expected=Decision.rollback,
            )
            for s in range(seeds)
        ]
        ttd = _mean([o.time_to_detect_s for o in outs])
        exposed = _mean([float(o.canary_requests) for o in outs])
        rolled = sum(o.rollback_triggered for o in outs)
        rows.append(
            arm_row(
                "canary_weight",
                f"first_step_{first}",
                outs[-1].canary,
                spec=specs["v2-bad"],
                canary_weight=float(first),
                n=round(exposed or 0),
                rollback_triggered=rolled == len(outs),
                time_to_detect_s=ttd,
                false_rollbacks=sum(bool(o.false_rollback) for o in outs),
            )
        )
        lines.append(
            f"first step {first:>3}%: TTD {ttd}s  canary reqs before decision {exposed}  "
            f"rolled back {rolled}/{len(outs)}"
        )
    return rows, lines


def candidates(
    specs: dict[str, AgentSpec], noise_seeds: int
) -> list[tuple[str, AgentSpec, Decision, int]]:
    v1 = specs["v1"]
    out = [
        ("v2-bad", specs["v2-bad"], Decision.rollback, 0),
        ("v3-tradeoff", specs["v3-tradeoff"], Decision.needs_human, 0),
        ("v1-copy", v1_copy(v1), Decision.promote, 0),
    ]
    out += [(f"v1-noise-{s}", v1, Decision.promote, 100 + s) for s in range(noise_seeds)]
    return out


async def rollback_rule(
    specs: dict[str, AgentSpec], noise_seeds: int, policy: ParetoPolicy | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    rows, lines = [], []
    cands = candidates(specs, noise_seeds)
    for rule in ("pareto", "naive_se", "naive"):
        outs: list[tuple[str, RolloutOutcome]] = []
        for label, spec, expected, seed in cands:
            o = await arun_rollout(
                specs["v1"],
                spec,
                config=RolloutConfig(seed=seed, rule=rule),
                policy=policy,
                expected=expected,
                rule=rule,
            )
            outs.append((label, o))
            rows.append(
                arm_row(
                    "rollback_rule",
                    f"{rule}:{label}",
                    o.canary,
                    spec=spec,
                    canary_weight=float(o.final_weight),
                    rollback_triggered=o.rollback_triggered,
                    time_to_detect_s=o.time_to_detect_s,
                    false_rollbacks=int(bool(o.false_rollback)),
                )
            )
        fr = sum(bool(o.false_rollback) for _, o in outs)
        missed = sum(o.expected == Decision.rollback and not o.rollback_triggered for _, o in outs)
        rows.append(
            arm_row(
                "rollback_rule",
                rule,
                None,
                n=len(outs),
                rollback_triggered=any(o.rollback_triggered for _, o in outs),
                false_rollbacks=fr,
            )
        )
        detail = " ".join(f"{lbl}={o.decision.value}" for lbl, o in outs)
        lines.append(f"{rule:>6}: false rollbacks {fr}/{len(outs)}  missed {missed}  [{detail}]")
    return rows, lines


async def amain(out: str, seeds: int, noise_seeds: int) -> list[str]:
    specs = load_specs()
    r1, l1 = await canary_weight(specs, seeds)
    r2, l2 = await rollback_rule(specs, noise_seeds)
    Path(out).unlink(missing_ok=True)
    append_rows(out, r1 + r2)
    return l1 + l2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="canary weight + rollback rule experiments")
    ap.add_argument("--out", default="runs/rollout.jsonl")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--noise-seeds", type=int, default=5)
    args = ap.parse_args(argv)
    for line in asyncio.run(amain(args.out, args.seeds, args.noise_seeds)):
        print(line)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
