"""SLO rules smoke: generated file is fresh and carries the burn-rate alerts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "gen_slo_rules.py"
RULES = ROOT / "infra" / "observability" / "prometheus" / "rules"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_slo_rules", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_slo_rules_fresh_and_complete() -> None:
    gen = _load_gen()
    assert gen.main(["--check"]) == 0

    doc = yaml.safe_load((RULES / "slo-generated.yml").read_text())
    rules = [r for g in doc["groups"] for r in g["rules"]]
    alerts = {r["alert"] for r in rules if "alert" in r}
    records = {r["record"] for r in rules if "record" in r}

    for slo in ("Availability", "Latency", "Quality", "Cost"):
        assert f"AgentRuntime{slo}BurnPage" in alerts
        assert f"AgentRuntime{slo}BurnTicket" in alerts
    for w in ("5m", "30m", "1h", "2h", "6h", "1d", "3d", "30d"):
        assert f"slo:sli_error:ratio_rate{w}" in records
        assert f"slo:burn_rate:{w}" in records
    assert "slo:error_budget:remaining" in records

    page = next(r for r in rules if r.get("alert") == "AgentRuntimeAvailabilityBurnPage")
    assert "> 14.4" in page["expr"] and "> 6" in page["expr"]

    canary = yaml.safe_load((RULES / "recording.yml").read_text())
    canary_records = {r["record"] for g in canary["groups"] for r in g["rules"]}
    assert {
        "adcp:cost_per_task:rate5m",
        "adcp:latency_p95:5m",
        "adcp:eval_score:mean5m",
        "adcp:cache_hit_ratio:rate5m",
        "vllm:prefix_cache_hit_ratio:rate5m",
    } <= canary_records
