"""Comparison matrix on the frozen set (evals/matrix.yaml): models, prompt caching, context strategy.

    python -m adcp.evals.matrix [--config evals/matrix.yaml] [--out runs/matrix.jsonl] [--only models]

Each experiment writes one row per variant with `experiment` set. --out is truncated first so the
file reflects one matrix run.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from typing import Any

import yaml

from adcp.env.tasks import Task, load_tasks
from adcp.evals.metrics import append_rows, make_row
from adcp.evals.runner import default_tasks_path, evaluate_spec
from adcp.spec import AgentSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
MATRIX_CONFIG = REPO_ROOT / "evals" / "matrix.yaml"


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = (
            deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        )
    return out


def override(spec: AgentSpec, patch: dict[str, Any]) -> AgentSpec:
    return AgentSpec.model_validate(deep_merge(spec.model_dump(mode="json"), patch))


def expand(name: str, cfg: dict[str, Any]) -> list[tuple[str, AgentSpec]]:
    """(variant label, spec) pairs for one experiment block."""
    if "specs" in cfg:
        paths = sorted(p for pattern in cfg["specs"] for p in glob.glob(str(REPO_ROOT / pattern)))
        if not paths:
            raise FileNotFoundError(f"{name}: no specs match {cfg['specs']}")
        specs = [AgentSpec.from_yaml(p) for p in paths]
        return [(s.labels.get("display", s.version.removeprefix("matrix-")), s) for s in specs]
    base = AgentSpec.from_yaml(REPO_ROOT / cfg["base"])
    return [(label, override(base, patch or {})) for label, patch in cfg["variants"].items()]


def run_matrix(
    config: dict[str, Any], tasks: list[Task], only: list[str] | None = None
) -> list[dict[str, Any]]:
    rows = []
    for name, cfg in config["experiments"].items():
        if only and name not in only:
            continue
        for label, spec in expand(name, cfg):
            _, metrics = evaluate_spec(spec, tasks)
            row = make_row(metrics, experiment=name, variant=label, spec=spec)
            rows.append(row)
            print(
                f"{name:8s} {label:22s} success={row['task_success']:.2f} "
                f"score={row['eval_score']:.3f} cost=${row['cost_per_task']:.5f} "
                f"p95={row['p95_latency_ms']:.0f}ms cache={row['cache_hit_rate']:.2f}"
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="comparison matrix")
    ap.add_argument("--config", default=str(MATRIX_CONFIG))
    ap.add_argument("--out", default="runs/matrix.jsonl")
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args(argv)
    config = yaml.safe_load(Path(args.config).read_text())
    rows = run_matrix(config, load_tasks(args.tasks or default_tasks_path()), args.only)
    out = Path(args.out)
    if out.exists():
        out.unlink()
    append_rows(out, rows)
    print(f"wrote {len(rows)} rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
