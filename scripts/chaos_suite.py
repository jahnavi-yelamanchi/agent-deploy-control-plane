"""Chaos suite: inject each fault into the canary (v1 vs v1+fault), measure MTTD / MTTR.

    python scripts/chaos_suite.py [--seeds 5] [--out runs/chaos.jsonl]

MTTD: mean simulated seconds from canary start to the rollback decision.
MTTR: MTTD + simulated abort latency (Argo abort + route shift).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from pathlib import Path

from adcp.chaos import default_faults
from adcp.rollback.pareto import Decision
from adcp.rollout.rows import append_rows, arm_row
from adcp.rollout.sim import RolloutConfig, arun_rollout
from adcp.spec import AgentSpec

ROOT = Path(__file__).resolve().parents[1]


async def run(seeds: int, out: str | None, spec_path: Path) -> list[dict]:
    spec = AgentSpec.from_yaml(spec_path)
    rows, table = [], []
    for fault in default_faults():
        outs = [
            await arun_rollout(
                spec,
                spec,
                config=RolloutConfig(seed=s),
                fault=fault,
                expected=Decision.rollback,
            )
            for s in range(seeds)
        ]
        ttd = [o.time_to_detect_s for o in outs if o.rollback_triggered and o.time_to_detect_s]
        ttr = [o.time_to_rollback_s for o in outs if o.time_to_rollback_s]
        mttd = round(statistics.fmean(ttd), 1) if ttd else None
        mttr = round(statistics.fmean(ttr), 1) if ttr else None
        detected = sum(o.rollback_triggered for o in outs)
        decisions = ",".join(sorted({o.decision.value for o in outs}))
        reasons = ",".join(sorted({o.comparison.reason for o in outs if o.comparison}))
        table.append((fault.name, detected, len(outs), mttd, mttr, decisions, reasons))
        rows.append(
            arm_row(
                "chaos",
                fault.name,
                outs[-1].canary,
                spec=spec,
                canary_weight=float(outs[-1].first_weight),
                rollback_triggered=detected == len(outs),
                time_to_detect_s=mttd,
            )
        )
    print(f"{'fault':<26} {'detected':>8} {'MTTD_s':>7} {'MTTR_s':>7}  decisions / reason")
    for name, det, n, mttd, mttr, dec, rsn in table:
        print(f"{name:<26} {det:>4}/{n:<3} {mttd!s:>7} {mttr!s:>7}  {dec} / {rsn}")
    if out:
        Path(out).unlink(missing_ok=True)
        append_rows(out, rows)
        print(f"wrote {out}")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="in-process chaos suite (MTTD/MTTR)")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default="runs/chaos.jsonl")
    ap.add_argument("--spec", default=str(ROOT / "specs" / "v1.yaml"))
    args = ap.parse_args(argv)
    asyncio.run(run(args.seeds, args.out, Path(args.spec)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
