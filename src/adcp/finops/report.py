"""FinOps report -> results/finops.md.

    python -m adcp.finops.report [--specs specs] [--out results/finops.md] [--skip-runs]

1. Per-deploy token cost: every specs/*.yaml on the frozen task set (mock provider) ->
   cost/task, cost per 1k tasks, tokens, cache hit rate, eval score.
2. AKS vs EKS monthly cluster cost: node pools from `terraform output -json node_pools` when
   state exists, else terraform/{aws,azure}/variables.tf defaults x infra_prices.yaml. `infracost breakdown --format json` replaces the static
   totals when the binary is on PATH and succeeds.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3]
INFRA_PRICES = Path(__file__).with_name("infra_prices.yaml")
VAR_RE = re.compile(r'variable\s+"(\w+)"\s*\{(.*?)\n\}', re.S)
OBJ_RE = re.compile(r"default\s*=\s*\{([^{}]*)\}")
KV_RE = re.compile(r'(\w+)\s*=\s*"?([\w.\-]+)"?')
BOOL_RE = re.compile(r"default\s*=\s*(true|false)")


class Pool(BaseModel):
    cloud: str
    name: str
    instance_type: str
    count: int
    hourly_usd: float
    enabled: bool = True

    @property
    def monthly(self) -> float:
        return self.count * self.hourly_usd * 730


def parse_tf_variables(tf_dir: Path) -> dict[str, Any]:
    """Defaults of object/bool variables in *.tf (enough for node pool sizing; no HCL parser)."""
    out: dict[str, Any] = {}
    for f in sorted(tf_dir.glob("*.tf")):
        for name, body in VAR_RE.findall(f.read_text()):
            if m := OBJ_RE.search(body):
                out[name] = {k: v for k, v in KV_RE.findall(m.group(1))}
            elif m := BOOL_RE.search(body):
                out[name] = m.group(1) == "true"
    return out


def tf_output_pools(tf_dir: Path) -> list[Pool] | None:
    """`terraform output -json node_pools` when state exists (list or map of pools)."""
    if not (tf_dir / "terraform.tfstate").exists() or not shutil.which("terraform"):
        return None
    try:
        raw = json.loads(
            subprocess.run(
                ["terraform", f"-chdir={tf_dir}", "output", "-json", "node_pools"],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            ).stdout
        )
    except Exception:
        return None
    items = raw.items() if isinstance(raw, dict) else enumerate(raw)
    return [
        Pool(
            cloud=p["cloud"],
            name=str(p.get("name", k)),
            instance_type=p["instance_type"],
            count=int(p["count"]),
            hourly_usd=float(p["hourly_usd"]),
        )
        for k, p in items
    ]


def node_pools(
    cloud: str, prices: dict[str, Any], tf_root: Path = ROOT / "terraform"
) -> list[Pool]:
    if (applied := tf_output_pools(tf_root / cloud)) is not None:
        return applied
    tf = parse_tf_variables(tf_root / cloud) if (tf_root / cloud).exists() else {}
    table = prices[cloud]["instances"]
    type_key, count_key = ("instance_type", "desired") if cloud == "aws" else ("vm_size", "count")
    pools = []
    for name in ("cpu", "gpu"):
        var = tf.get(f"{name}_node_pool")
        if var and type_key in var:
            itype, count = var[type_key], int(var.get(count_key, 1))
        else:
            d = prices["default_pools"][cloud][name]
            itype, count = d["instance_type"], int(d["count"])
        enabled = name == "cpu" or bool(tf.get("enable_gpu_pool", False))
        pools.append(
            Pool(
                cloud=cloud,
                name=name,
                instance_type=itype,
                count=count,
                hourly_usd=float(table.get(itype, 0.0)),
                enabled=enabled,
            )
        )
    return pools


def extras(
    cloud: str, prices: dict[str, Any], tf_root: Path = ROOT / "terraform"
) -> dict[str, float]:
    """Hourly price of flat-rate resources present in the terraform dir."""
    text = (
        "".join(f.read_text() for f in (tf_root / cloud).glob("*.tf"))
        if (tf_root / cloud).exists()
        else ""
    )
    return {
        rtype: hourly
        for rtype, hourly in prices[cloud].get("extras", {}).items()
        if f'resource "{rtype}"' in text
    }


def infracost_total(path: Path) -> float | None:
    if not shutil.which("infracost"):
        return None
    try:
        out = subprocess.run(
            ["infracost", "breakdown", "--path", str(path), "--format", "json"],
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        ).stdout
        return float(json.loads(out)["totalMonthlyCost"])
    except Exception:
        return None


def cluster_table(
    prices: dict[str, Any], tf_root: Path = ROOT / "terraform"
) -> tuple[str, dict[str, float]]:
    h = prices["hours_per_month"]
    rows = [
        "| cloud | item | type | count | $/h | $/month |",
        "|---|---|---|---:|---:|---:|",
    ]
    totals: dict[str, float] = {}
    for cloud, cp_key, cp_label in (
        ("aws", "eks_standard", "EKS control plane"),
        ("azure", "aks_free", "AKS control plane (Free tier)"),
    ):
        cp = prices[cloud]["control_plane"][cp_key]
        base = cp * h
        rows.append(f"| {cloud} | {cp_label} | {cp_key} | 1 | {cp:.3f} | {base:,.2f} |")
        gpu_total = 0.0
        for p in node_pools(cloud, prices, tf_root):
            tag = "" if p.enabled else " (disabled by default)"
            rows.append(
                f"| {cloud} | {p.name} node pool{tag} | {p.instance_type} | {p.count} | "
                f"{p.hourly_usd:.4f} | {p.count * p.hourly_usd * h:,.2f} |"
            )
            if p.name == "gpu" and not p.enabled:
                gpu_total = p.count * p.hourly_usd * h
            if p.enabled:
                base += p.count * p.hourly_usd * h
        for rtype, hourly in extras(cloud, prices, tf_root).items():
            rows.append(f"| {cloud} | {rtype} | - | 1 | {hourly:.4f} | {hourly * h:,.2f} |")
            base += hourly * h
        totals[f"{cloud}_base"] = base
        totals[f"{cloud}_with_gpu"] = base + gpu_total
        if cloud == "azure":
            std = prices["azure"]["control_plane"]["aks_standard"] * h
            totals["azure_standard_tier_delta"] = std
    lines = rows + [
        "",
        "| total $/month | EKS | AKS |",
        "|---|---:|---:|",
        f"| CPU pool + control plane + extras | {totals['aws_base']:,.2f} | {totals['azure_base']:,.2f} |",
        f"| + GPU node pool (vLLM/SGLang) | {totals['aws_with_gpu']:,.2f} | {totals['azure_with_gpu']:,.2f} |",
        "",
        f"AKS Standard tier (uptime SLA) adds ${totals['azure_standard_tier_delta']:,.2f}/month. "
        "GPU SKUs differ (L4 vs T4). Excludes egress, storage, ECR, load balancers, model API spend.",
    ]
    return "\n".join(lines), totals


def deploy_table(specs_dir: Path, skip_runs: bool = False) -> tuple[str, list[dict[str, Any]]]:
    from adcp.spec import AgentSpec

    specs = sorted(specs_dir.glob("*.yaml"))
    hdr = [
        "| spec | model | provider | cache | cost/task $ | $/1k tasks | tokens in/out per task | cache hit | eval score | p95 ms |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    out: list[dict[str, Any]] = []
    if skip_runs or not specs:
        return "\n".join([*hdr, "| (skipped) |||||||||| "]), out
    from adcp.env.tasks import load_tasks
    from adcp.evals.runner import default_tasks_path, evaluate_spec

    tasks = load_tasks(default_tasks_path())
    for p in specs:
        spec = AgentSpec.from_yaml(p)
        _, m = evaluate_spec(spec, tasks)
        n = max(m["n"], 1)
        out.append({"spec": p.stem, "version": spec.version, "model_id": spec.model_id, **m})
        hdr.append(
            f"| {p.stem} ({spec.version}) | {spec.model_id} | {spec.provider} | "
            f"{'on' if spec.params.prompt_caching else 'off'} | {m['cost_per_task']:.5f} | "
            f"{m['cost_per_task'] * 1000:.2f} | {m['tokens_in'] // n}/{m['tokens_out'] // n} | "
            f"{m['cache_hit_rate']:.2f} | {m['eval_score']:.3f} | {m['p95_latency_ms']:.0f} |"
        )
    return "\n".join(hdr), out


def build(
    specs_dir: Path, out: Path, skip_runs: bool = False, tf_root: Path = ROOT / "terraform"
) -> str:
    prices = yaml.safe_load(INFRA_PRICES.read_text())
    deploy_md, deploys = deploy_table(specs_dir, skip_runs)
    cluster_md, totals = cluster_table(prices, tf_root)
    ic = {c: infracost_total(tf_root / c) for c in ("aws", "azure")}
    source = (
        "Infracost: " + ", ".join(f"{c} ${v:,.2f}/month" for c, v in ic.items() if v is not None)
        if any(v is not None for v in ic.values())
        else f"Static price table `src/adcp/finops/infra_prices.yaml` (as of {prices['as_of']}); "
        "`infracost` not on PATH."
    )
    amort = ""
    if deploys:
        monthly_tasks = 1_000_000
        per_1k = totals["aws_base"] / (monthly_tasks / 1000)
        amort = (
            f"\nCluster amortized at {monthly_tasks:,} tasks/month: EKS ${per_1k:.2f} / 1k tasks, "
            f"AKS ${totals['azure_base'] / (monthly_tasks / 1000):.2f} / 1k tasks "
            "(add to the token cost above).\n"
        )
    md = (
        "# FinOps\n\n"
        "## Per-deploy token cost (frozen task set, mock provider, `finops/prices.yaml`)\n\n"
        f"{deploy_md}\n{amort}\n"
        "## Cluster cost: EKS vs AKS (Terraform node pool defaults)\n\n"
        f"{cluster_md}\n\nSource: {source}\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    return md


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="per-deploy and AKS-vs-EKS cost tables")
    ap.add_argument("--specs", default=str(ROOT / "specs"))
    ap.add_argument("--out", default=str(ROOT / "results" / "finops.md"))
    ap.add_argument("--skip-runs", action="store_true")
    args = ap.parse_args(argv)
    print(build(Path(args.specs), Path(args.out), args.skip_runs))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
